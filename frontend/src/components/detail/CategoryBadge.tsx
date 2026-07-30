import { categoryColor } from '@/lib/categories'
import { Chip } from '@/components/ui/Chip'

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
      {/* Label uses readable text color (the category hue is carried by the
          dot + border); saturated hues as text fail AA on the light surface. */}
      <Chip color={color} dot style={{ color: 'var(--text-primary)' }}>
        {category}
      </Chip>
      {pct != null && (
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{pct}%</span>
      )}
    </div>
  )
}
