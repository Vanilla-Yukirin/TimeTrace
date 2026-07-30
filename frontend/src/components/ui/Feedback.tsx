import type { CSSProperties, ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { CatMascot } from '@/components/brand/CatMascot'
import { Button } from './Button'

/** Unified empty state — the mascot version (Audit/LlmLog) and the plain-text
 *  versions (TokenManager/AppOverrides/AgentSidebar) previously disagreed. */
export function EmptyState({
  title,
  desc,
  mascot = false,
  children,
  style,
}: {
  title: ReactNode
  desc?: ReactNode
  /** Show the cat mascot above the title (brand touch for full-page empties). */
  mascot?: boolean
  children?: ReactNode
  style?: CSSProperties
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        padding: '36px 20px',
        ...style,
      }}
    >
      {mascot && <CatMascot size={72} float style={{ marginBottom: 10 }} />}
      <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-secondary)' }}>{title}</div>
      {desc && (
        <div style={{ marginTop: 5, fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7, maxWidth: 380 }}>
          {desc}
        </div>
      )}
      {children && <div style={{ marginTop: 14 }}>{children}</div>}
    </div>
  )
}

/** Error banner with optional retry — consolidates the 3-4 divergent error
 *  displays (AuditPage with retry, LlmLogPage without, TurnView bubble,
 *  Settings plain text). role="alert" so screen readers announce failures. */
export function ErrorBanner({
  title = '出错了',
  message,
  onRetry,
  retrying = false,
  style,
}: {
  title?: ReactNode
  message: ReactNode
  onRetry?: () => void
  retrying?: boolean
  style?: CSSProperties
}) {
  return (
    <div
      role="alert"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '11px 14px',
        borderRadius: 'var(--radius-md)',
        background: 'var(--error-bg)',
        border: '1px solid color-mix(in srgb, var(--error) 35%, transparent)',
        color: 'var(--error)',
        fontSize: 13,
        ...style,
      }}
    >
      <AlertTriangle size={15} aria-hidden="true" style={{ flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <span style={{ fontWeight: 600 }}>{title}</span>
        {message && <span style={{ opacity: 0.9, marginLeft: 8 }}>{message}</span>}
      </div>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry} loading={retrying} style={{ flexShrink: 0 }}>
          {!retrying && <RefreshCw size={13} aria-hidden="true" />}
          重试
        </Button>
      )}
    </div>
  )
}

/** Live/polling indicator dot — the "实时 / 刷新中" badge that AuditPage and
 *  LlmLogPage had copied character-for-character. */
export function LiveBadge({ active, label }: { active: boolean; label?: string }) {
  return (
    <span
      role="status"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 11.5,
        fontWeight: 600,
        color: active ? 'var(--success)' : 'var(--text-muted)',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: active ? 'var(--success)' : 'var(--text-muted)',
        }}
      />
      {label ?? (active ? '实时' : '已暂停')}
    </span>
  )
}
