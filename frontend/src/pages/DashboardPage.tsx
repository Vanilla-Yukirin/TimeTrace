import { useEffect, useRef, useState } from 'react'
import { RefreshCw, Wrench, Hourglass } from 'lucide-react'
import { reportsApi, parseReportData, type Report, type ReportEvent } from '@/lib/agentApi'
import { ReportView } from '@/components/dashboard/ReportView'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorBanner } from '@/components/ui/Feedback'
import { Skeleton } from '@/components/ui/Skeleton'

const SCOPES = [
  { key: 'recent_3h', label: '最近 3 小时' },
  { key: 'recent_24h', label: '最近 24 小时' },
  { key: 'recent_7d', label: '最近 7 天' },
]

const TOOL_LABELS: Record<string, string> = {
  search_activity: '检索活动',
  get_recent_activity: '拉取最近活动',
  get_app_breakdown: '统计应用时长',
  get_category_stats: '统计分类时长',
  apply_label: '打标签',
}

interface ToolStep {
  tool: string
  summary?: string
}

function fmtTime(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function DashboardPage() {
  const [scope, setScope] = useState('recent_24h')
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Generation (streaming) state — persisted across renders so the button stays
  // disabled until the run finishes, and the live progress survives re-renders.
  const [generating, setGenerating] = useState(false)
  // True while the SSE stream was cut and we're recovering via the
  // non-streaming fallback / polling — the panel tells the user so.
  const [recovering, setRecovering] = useState(false)
  const [steps, setSteps] = useState<ToolStep[]>([])
  const [liveText, setLiveText] = useState('')
  const genScopeRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    reportsApi
      .latest(scope)
      .then((r) => {
        if (!cancelled) setReport(r)
      })
      .catch((e) => {
        if (!cancelled) setError(String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [scope])

  async function regenerate() {
    if (generating) return // guard against double-fire
    setGenerating(true)
    setError(null)
    setSteps([])
    setLiveText('')
    genScopeRef.current = scope
    const startedAt = Date.now()
    try {
      for await (const ev of reportsApi.generateStream(scope)) {
        applyEvent(ev)
      }
    } catch {
      // The streaming endpoint is an SSE-over-fetch POST. Two failure modes seen in
      // the wild: (a) some browser + proxy(TUN) setups can't read the long-lived
      // stream; (b) a long (>~60s) generation can get idle-cut by an intermediary
      // even on the plain POST — but the server still finishes + PERSISTS the report.
      // So: fall back to the non-streaming POST; and if THAT is also severed, poll
      // latest() to recover the report that landed server-side after we were cut.
      setSteps([])
      setLiveText('')
      setRecovering(true)
      const sc = genScopeRef.current ?? scope
      try {
        setReport(await reportsApi.generate(sc))
      } catch (fallbackErr) {
        let recovered: Report | null = null
        for (let i = 0; i < 15 && !recovered; i++) {
          await new Promise((r) => setTimeout(r, 4000))
          try {
            const r = await reportsApi.latest(sc)
            if (r && r.created_at >= startedAt - 5000) recovered = r // a fresh one landed
          } catch {
            // keep polling
          }
        }
        if (recovered) setReport(recovered)
        else setError(String(fallbackErr))
      }
    } finally {
      setGenerating(false)
      setRecovering(false)
      setLiveText('')
      setSteps([])
      genScopeRef.current = null
    }
  }

  function applyEvent(ev: ReportEvent) {
    if (ev.type === 'step' && ev.phase === 'tool_call') {
      setSteps((prev) => [...prev, { tool: ev.tool }])
    } else if (ev.type === 'step' && ev.phase === 'tool_result') {
      setSteps((prev) => {
        const next = [...prev]
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].tool === ev.tool && next[i].summary === undefined) {
            next[i] = { ...next[i], summary: ev.summary }
            break
          }
        }
        return next
      })
    } else if (ev.type === 'token') {
      setLiveText((prev) => prev + ev.text)
    } else if (ev.type === 'report') {
      setReport(ev.report)
    } else if (ev.type === 'error') {
      setError(ev.message)
    }
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', width: '100%' }}>
      <div style={{ maxWidth: 760, margin: '0 auto', padding: '24px 20px', width: '100%' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: 8,
          }}
        >
          <div style={{ fontSize: 13, color: 'var(--text-muted)', maxWidth: 440, lineHeight: 1.6 }}>
            AI 自动分析你的真实活动，抓特点、给洞察。系统每 30 分钟自动刷新一份。
          </div>
          <Button
            variant="primary"
            onClick={regenerate}
            disabled={generating}
            title={generating ? '正在生成，请稍候…' : '让 AI 现在重新分析一份'}
            style={{ flexShrink: 0 }}
          >
            <RefreshCw size={14} className={generating ? 'tt-spin' : undefined} />
            {generating ? '生成中…' : '重新生成'}
          </Button>
        </div>

        {/* Cost/time hint — set expectations before they click. */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 18, opacity: 0.85 }}>
          <Hourglass size={12} aria-hidden="true" />
          生成由本地大模型实时分析，耗时约 10–60 秒、消耗算力，期间按钮不可点；请耐心等待。
        </div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
          {SCOPES.map((s) => (
            <button
              key={s.key}
              className="tt-chip"
              data-active={scope === s.key || undefined}
              onClick={() => setScope(s.key)}
              disabled={generating}
              style={{ fontSize: 13, padding: '6px 14px', opacity: generating && scope !== s.key ? 0.5 : 1 }}
            >
              {s.label}
            </button>
          ))}
        </div>

        {error && (
          <ErrorBanner style={{ marginBottom: 16 }} title="生成失败" message={error} />
        )}

        {/* Live generation panel: tool steps + streaming raw output. */}
        {generating && (
          <div
            style={{
              padding: '16px 18px',
              borderRadius: 'var(--radius-lg)',
              background: 'var(--bg-surface)',
              border: '1px solid var(--bg-border)',
              marginBottom: 18,
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 10 }}>
              {recovering ? '连接中断，正在取回已生成的结果…' : '✨ AI 正在分析你的活动…'}
            </div>
            {steps.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                {steps.map((s, i) => (
                  <span
                    key={i}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 5,
                      padding: '4px 10px',
                      borderRadius: 'var(--radius-pill)',
                      background: 'var(--bg-base)',
                      border: '1px solid var(--bg-border)',
                      color: 'var(--text-muted)',
                      fontSize: 12,
                    }}
                  >
                    <Wrench size={12} aria-hidden="true" />
                    {TOOL_LABELS[s.tool] ?? s.tool}
                    {s.summary ? ` · ${s.summary}` : '…'}
                  </span>
                ))}
              </div>
            )}
            {liveText && (
              // The model is streaming raw JSON now (parsed + themed on completion),
              // so show a friendly "writing" indicator instead of the raw payload.
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12.5,
                  color: 'var(--text-secondary)',
                }}
              >
                ✍️ 正在落笔写报告…
                <span style={{ opacity: 0.6 }}>▍</span>
              </div>
            )}
            {steps.length === 0 && !liveText && (
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                {recovering ? '报告在服务器上继续生成，稍等片刻即可取回。' : '正在连接本地大模型…'}
              </div>
            )}
          </div>
        )}

        {loading ? (
          // Mirrors the ReportView shape (title bar + stat cards + card grid)
          // so the wait reads as structure, not a spinner.
          <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Skeleton style={{ height: 30, width: '55%' }} />
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Skeleton style={{ height: 64, flex: '1 1 150px' }} />
              <Skeleton style={{ height: 64, flex: '1 1 150px' }} />
              <Skeleton style={{ height: 64, flex: '1 1 150px' }} />
            </div>
            <Skeleton style={{ height: 120 }} />
            <Skeleton style={{ height: 90 }} />
          </div>
        ) : !report && !generating ? (
          <EmptyState
            mascot
            title="还没有这个时段的看板"
            desc="点右上角「重新生成」让 AI 现在分析一份；之后系统会每 30 分钟自动刷新。"
          />
        ) : report ? (
          <>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
              生成于 {fmtTime(report.created_at)}
              {report.model ? ` · ${report.model}` : ''}
            </div>
            {(() => {
              // New reports are format:'json' — the LLM gives only content text,
              // and our themed <ReportView> owns all colour + layout, so it adapts
              // to light/dark. Legacy format:'html' reports (pre-JSON) fall back to
              // rendering the model's self-contained HTML as-is (don't impose a
              // surface — it carries its own colours).
              const data = report.format === 'json' ? parseReportData(report.content) : null
              return data ? (
                <ReportView data={data} />
              ) : (
                <div
                  className="tt-report"
                  style={{ overflowX: 'auto' }}
                  dangerouslySetInnerHTML={{ __html: report.content }}
                />
              )
            })()}
          </>
        ) : null}
      </div>
    </div>
  )
}
