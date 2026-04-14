interface CategoryBadgeProps {
  category: string | null | undefined
  confidence: number | null | undefined
}

const CATEGORY_COLORS: Record<string, string> = {
  work: '#22c55e',
  development: '#6366f1',
  communication: '#3b82f6',
  entertainment: '#f59e0b',
  social: '#ec4899',
  productivity: '#8b5cf6',
  browsing: '#06b6d4',
  other: '#64748b',
}

function getCategoryColor(cat: string): string {
  const key = cat.toLowerCase()
  return CATEGORY_COLORS[key] ?? '#64748b'
}

export function CategoryBadge({ category, confidence }: CategoryBadgeProps) {
  if (!category) {
    return (
      <span style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 11,
        background: 'var(--bg-raised)',
        color: 'var(--text-muted)',
        border: '1px solid var(--bg-border)',
      }}>
        未分析
      </span>
    )
  }

  const color = getCategoryColor(category)
  const pct = confidence != null ? Math.round(confidence * 100) : null

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 11,
        background: color + '22',
        color,
        border: `1px solid ${color}55`,
        fontWeight: 600,
      }}>
        {category}
      </span>
      {pct != null && (
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{pct}%</span>
      )}
    </div>
  )
}
