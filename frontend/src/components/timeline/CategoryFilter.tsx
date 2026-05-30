import { useMemo } from 'react'
import { Layers } from 'lucide-react'
import type { ApiRecord } from '@/types/api'
import { categoryColor } from '@/lib/categories'

interface CategoryFilterProps {
  records: ApiRecord[]
  /** Selected category_final value; null = 全部. */
  selected: string | null
  onSelect: (cat: string | null) => void
}

const UNCATEGORIZED = '__uncat__'

/** "快速筛选" — derive the day's categories with live counts and let the user
 *  narrow the timeline to one. Mirrors the reference's left-column filter list. */
export function CategoryFilter({ records, selected, onSelect }: CategoryFilterProps) {
  const groups = useMemo(() => {
    const counts = new Map<string, number>()
    for (const r of records) {
      const key = r.category_final ?? UNCATEGORIZED
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [records])

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.04em',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          marginBottom: 8,
        }}
      >
        <Layers size={12} />
        快速筛选
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Row
          label="全部活动"
          color="var(--accent)"
          count={records.length}
          active={selected === null}
          onClick={() => onSelect(null)}
          allDot
        />
        {groups.map(([key, count]) => {
          const isUncat = key === UNCATEGORIZED
          const label = isUncat ? '未分类' : key
          const value = isUncat ? UNCATEGORIZED : key
          return (
            <Row
              key={key}
              label={label}
              color={isUncat ? 'var(--cat-slate)' : categoryColor(key)}
              count={count}
              active={selected === value}
              onClick={() => onSelect(value)}
            />
          )
        })}
        {records.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '6px 8px' }}>
            这一天还没有记录
          </div>
        )}
      </div>
    </div>
  )
}

/** The sentinel value the parent uses to filter "未分类" records. */
export { UNCATEGORIZED }

function Row({
  label,
  color,
  count,
  active,
  onClick,
  allDot = false,
}: {
  label: string
  color: string
  count: number
  active: boolean
  onClick: () => void
  allDot?: boolean
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        padding: '7px 9px',
        borderRadius: 'var(--radius-md)',
        background: active ? 'var(--accent-subtle)' : 'transparent',
        border: 'none',
        cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <span
        style={{
          width: 9,
          height: 9,
          borderRadius: allDot ? 3 : '50%',
          background: color,
          flexShrink: 0,
        }}
      />
      <span
        style={{
          flex: 1,
          fontSize: 13,
          fontWeight: active ? 600 : 500,
          color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: active ? 'var(--accent)' : 'var(--text-muted)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {count}
      </span>
    </button>
  )
}
