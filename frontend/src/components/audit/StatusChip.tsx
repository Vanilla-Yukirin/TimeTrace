import type { CSSProperties } from 'react'

/** Maps a derived audit status → a Chinese label + a semantic theme color.
 *  The color drives a soft pill (color-mix bg/border + dot), mirroring
 *  CategoryBadge so the audit table reads consistently in both themes. */
const STATUS_META: Record<string, { label: string; color: string }> = {
  captured: { label: '已采集', color: 'var(--text-muted)' },
  queued: { label: '排队中', color: 'var(--warning)' },
  retry_waiting: { label: '重试等待', color: 'var(--warning)' },
  processing: { label: '处理中', color: 'var(--accent)' },
  done: { label: '完成', color: 'var(--success)' },
  skipped_no_image: { label: '无图跳过', color: 'var(--text-muted)' },
  labeled: { label: '已标注', color: 'var(--cat-cyan)' },
  failed: { label: '失败', color: 'var(--error)' },
}

export function StatusChip({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, color: 'var(--text-muted)' }
  const style: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 5,
    padding: '2px 9px',
    borderRadius: 'var(--radius-pill)',
    fontSize: 11.5,
    fontWeight: 600,
    whiteSpace: 'nowrap',
    color: 'var(--text-primary)',
    background: `color-mix(in srgb, ${meta.color} 15%, transparent)`,
    border: `1px solid color-mix(in srgb, ${meta.color} 42%, transparent)`,
  }
  return (
    <span style={style}>
      <span
        style={{ width: 6, height: 6, borderRadius: '50%', background: meta.color, flexShrink: 0 }}
      />
      {meta.label}
    </span>
  )
}
