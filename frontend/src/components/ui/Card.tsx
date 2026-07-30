import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** Surface card — the bg-surface + border + radius block repeated 6+ times
 *  across Settings/admin/audit pages (with the radius drifting lg↔md). */
export function Card({
  children,
  className,
  style,
}: {
  children: ReactNode
  className?: string
  style?: CSSProperties
}) {
  return (
    <div className={cn('tt-card', className)} style={style}>
      {children}
    </div>
  )
}

/** Section header (h3 + optional description) — one of the most-copied
 *  snippets in the admin/Settings cluster (5 copies, two margin variants). */
export function Section({
  title,
  desc,
  children,
  style,
}: {
  title: ReactNode
  desc?: ReactNode
  children?: ReactNode
  style?: CSSProperties
}) {
  return (
    <section style={style}>
      <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
        {title}
      </h3>
      {desc && (
        <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
          {desc}
        </p>
      )}
      {children && <div style={{ marginTop: 12 }}>{children}</div>}
    </section>
  )
}

/** Label/value row used in detail panels and diagnostics. */
export function KV({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, fontSize: 12.5 }}>
      <span style={{ color: 'var(--text-muted)', flexShrink: 0, minWidth: 64 }}>{label}</span>
      <span style={{ color: 'var(--text-primary)', minWidth: 0, wordBreak: 'break-all' }}>
        {children}
      </span>
    </div>
  )
}
