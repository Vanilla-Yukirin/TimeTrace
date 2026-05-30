import { categoryColor } from '@/lib/categories'

interface CategoryBadgeProps {
  category: string | null | undefined
  confidence: number | null | undefined
}

export function CategoryBadge({ category, confidence }: CategoryBadgeProps) {
  if (!category) {
    return (
      <span style={{
        display: 'inline-block',
        padding: '3px 10px',
        borderRadius: 'var(--radius-pill)',
        fontSize: 11,
        background: 'var(--bg-raised)',
        color: 'var(--text-muted)',
        border: '1px solid var(--bg-border)',
      }}>
        未分析
      </span>
    )
  }

  const color = categoryColor(category)
  const pct = confidence != null ? Math.round(confidence * 100) : null

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 11px',
        borderRadius: 'var(--radius-pill)',
        fontSize: 11.5,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
        color,
        border: `1px solid color-mix(in srgb, ${color} 45%, transparent)`,
        fontWeight: 600,
      }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color }} />
        {category}
      </span>
      {pct != null && (
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{pct}%</span>
      )}
    </div>
  )
}
