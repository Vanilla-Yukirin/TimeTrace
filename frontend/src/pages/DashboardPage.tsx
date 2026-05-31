import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { reportsApi, type Report } from '@/lib/agentApi'
import { CatMascot } from '@/components/brand/CatMascot'

const SCOPES = [
  { key: 'recent_3h', label: '最近 3 小时' },
  { key: 'recent_24h', label: '最近 24 小时' },
  { key: 'recent_7d', label: '最近 7 天' },
]

function fmtTime(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function DashboardPage() {
  const [scope, setScope] = useState('recent_24h')
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
    setGenerating(true)
    setError(null)
    try {
      const r = await reportsApi.generate(scope)
      setReport(r)
    } catch (e) {
      setError(String(e))
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', width: '100%' }}>
      <div style={{ maxWidth: 760, margin: '0 auto', padding: '24px 20px', width: '100%' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: 18,
          }}
        >
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            AI 自动分析你的真实活动，抓特点、给洞察。系统每 30 分钟自动刷新一份。
          </div>
          <button
            onClick={regenerate}
            disabled={generating}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 7,
              padding: '8px 16px',
              borderRadius: 'var(--radius-md)',
              border: 'none',
              background: 'var(--grad-accent)',
              color: '#fff',
              fontWeight: 600,
              fontSize: 13,
              cursor: generating ? 'wait' : 'pointer',
              opacity: generating ? 0.7 : 1,
              boxShadow: 'var(--shadow-glow)',
            }}
          >
            <RefreshCw size={14} style={generating ? { opacity: 0.6 } : undefined} />
            {generating ? '生成中…' : '重新生成'}
          </button>
        </div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
          {SCOPES.map((s) => (
            <button
              key={s.key}
              onClick={() => setScope(s.key)}
              style={{
                padding: '6px 14px',
                borderRadius: 'var(--radius-pill)',
                border: '1px solid var(--bg-border)',
                background: scope === s.key ? 'var(--accent-subtle)' : 'transparent',
                color: scope === s.key ? 'var(--accent)' : 'var(--text-secondary)',
                fontWeight: scope === s.key ? 600 : 500,
                fontSize: 13,
                cursor: 'pointer',
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

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
            加载中…
          </div>
        ) : !report ? (
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
        ) : (
          <>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
              生成于 {fmtTime(report.created_at)}
              {report.model ? ` · ${report.model}` : ''}
            </div>
            {/* The report body is HTML authored by the user's own local LLM from
                their own activity data (single-user, local-first threat model).
                Rendered inline so its color:inherit picks up the theme tokens. */}
            <div
              style={{ color: 'var(--text-primary)' }}
              dangerouslySetInnerHTML={{ __html: report.content }}
            />
          </>
        )}
      </div>
    </div>
  )
}
