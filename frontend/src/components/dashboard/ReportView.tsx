import type { ReportData } from '@/lib/agentApi'

/**
 * Themed renderer for a format:'json' insight report. The LLM supplies only the
 * content text (ReportData); ALL colour + layout live here and use the app's
 * theme tokens (--bg-surface / --text-primary / --accent …), so the report
 * adapts cleanly to light AND dark mode — unlike the old approach of letting the
 * model bake inline colours into raw HTML (which never matched the theme).
 */
export function ReportView({ data }: { data: ReportData }) {
  return (
    <div
      className="tt-fade"
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--bg-border)',
        borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-sm)',
        padding: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 18, fontWeight: 800, color: 'var(--text-primary)' }}>
          📊 近况洞察
        </span>
        {data.scope_label && (
          <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-muted)' }}>
            · {data.scope_label}
          </span>
        )}
      </div>

      {/* Headline stats */}
      {(data.headline || data.top_app) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {data.headline && (
            <Stat label={data.headline_caption || '总活跃时长'} value={data.headline} big />
          )}
          {data.top_app && (
            <Stat label="C 位应用" value={data.top_app.name} sub={data.top_app.value} />
          )}
        </div>
      )}

      {/* Caveat callout */}
      {data.caveat && (
        <div
          style={{
            display: 'flex',
            gap: 8,
            padding: '10px 14px',
            background: 'var(--bg-raised)',
            border: '1px dashed var(--warning)',
            borderRadius: 'var(--radius-md)',
            fontSize: 12.5,
            lineHeight: 1.6,
            color: 'var(--text-secondary)',
          }}
        >
          <span aria-hidden="true" style={{ color: 'var(--warning)' }}>
            ⚠️
          </span>
          <span>{data.caveat}</span>
        </div>
      )}

      {/* Insight cards */}
      {data.insights.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 12,
          }}
        >
          {data.insights.map((it, i) => (
            <div
              key={i}
              style={{
                background: 'var(--bg-raised)',
                border: '1px solid var(--bg-border)',
                borderRadius: 'var(--radius-md)',
                padding: '12px 14px',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  gap: 6,
                  fontSize: 14,
                  fontWeight: 700,
                  color: 'var(--text-primary)',
                  marginBottom: 6,
                }}
              >
                <span aria-hidden="true">{it.emoji}</span>
                <span>{it.title}</span>
              </div>
              <div style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                {it.body}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  sub,
  big = false,
}: {
  label: string
  value: string
  sub?: string
  big?: boolean
}) {
  return (
    <div
      style={{
        flex: '1 1 200px',
        minWidth: 160,
        background: 'var(--bg-raised)',
        border: '1px solid var(--bg-border)',
        borderRadius: 'var(--radius-md)',
        padding: '14px 16px',
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          color: 'var(--text-muted)',
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div style={{ fontSize: big ? 26 : 18, fontWeight: 800, color: 'var(--text-primary)', lineHeight: 1.2 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}
