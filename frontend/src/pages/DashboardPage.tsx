import { useEffect, useRef, useState } from 'react'
import { RefreshCw, Wrench } from 'lucide-react'
import { reportsApi, parseReportData, type Report, type ReportEvent } from '@/lib/agentApi'
import { ReportView } from '@/components/dashboard/ReportView'
import { CatMascot } from '@/components/brand/CatMascot'

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
    try {
      for await (const ev of reportsApi.generateStream(scope)) {
        applyEvent(ev)
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setGenerating(false)
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
          <button
            onClick={regenerate}
            disabled={generating}
            title={generating ? '正在生成，请稍候…' : '让 AI 现在重新分析一份'}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 7,
              padding: '8px 16px',
              borderRadius: 'var(--radius-md)',
              border: 'none',
              background: generating ? 'var(--bg-raised)' : 'var(--grad-accent)',
              color: generating ? 'var(--text-muted)' : '#fff',
              fontWeight: 600,
              fontSize: 13,
              cursor: generating ? 'not-allowed' : 'pointer',
              boxShadow: generating ? 'none' : 'var(--shadow-glow)',
              flexShrink: 0,
            }}
          >
            <RefreshCw size={14} className={generating ? 'tt-spin' : undefined} />
            {generating ? '生成中…' : '重新生成'}
          </button>
        </div>

        {/* Cost/time hint — set expectations before they click. */}
        <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 18, opacity: 0.85 }}>
          ⏳ 生成由本地大模型实时分析，耗时约 10–60 秒、消耗算力，期间按钮不可点；请耐心等待。
        </div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
          {SCOPES.map((s) => (
            <button
              key={s.key}
              onClick={() => setScope(s.key)}
              disabled={generating}
              style={{
                padding: '6px 14px',
                borderRadius: 'var(--radius-pill)',
                border: '1px solid var(--bg-border)',
                background: scope === s.key ? 'var(--accent-subtle)' : 'transparent',
                color: scope === s.key ? 'var(--accent)' : 'var(--text-secondary)',
                fontWeight: scope === s.key ? 600 : 500,
                fontSize: 13,
                cursor: generating ? 'not-allowed' : 'pointer',
                opacity: generating && scope !== s.key ? 0.5 : 1,
              }}
            >
              {s.label}
            </button>
          ))}
        </div>

        {error && (
          <div
            style={{
              padding: '12px 16px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--error-bg)',
              border: '1px solid var(--error)',
              color: 'var(--error)',
              fontSize: 13,
              marginBottom: 16,
            }}
          >
            {error}
          </div>
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
              ✨ AI 正在分析你的活动…
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
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>正在连接本地大模型…</div>
            )}
          </div>
        )}

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            加载中…
          </div>
        ) : !report && !generating ? (
          <div
            style={{
              padding: 32,
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <CatMascot size={84} float />
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginTop: 6 }}>
              还没有这个时段的看板
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', maxWidth: 340, lineHeight: 1.6 }}>
              点右上角「重新生成」让 AI 现在分析一份；之后系统会每 30 分钟自动刷新。
            </div>
          </div>
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
