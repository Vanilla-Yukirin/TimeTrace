import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useApps } from '@/hooks/useApps'
import { useCategories } from '@/hooks/useCategories'
import { toDateParam } from '@/lib/dateUtils'

interface FilterPanelProps {
  startDate: string | null       // YYYY-MM-DD
  endDate: string | null
  apps: string[]
  categories: string[]
  onStartDate: (s: string | null) => void
  onEndDate: (s: string | null) => void
  onApps: (next: string[]) => void
  onCategories: (next: string[]) => void
}

const PRESETS: { label: string; days: number }[] = [
  { label: '今天', days: 0 },
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
]

function daysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return toDateParam(d)
}

export function FilterPanel(props: FilterPanelProps) {
  const [open, setOpen] = useState(false)
  const { data: appList } = useApps()
  const { data: categoryData } = useCategories()
  const categoryList = categoryData?.categories ?? []

  const toggle = (arr: string[], v: string, set: (x: string[]) => void) => {
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v])
  }

  const applyPreset = (days: number) => {
    const today = toDateParam(new Date())
    props.onStartDate(days === 0 ? today : daysAgo(days))
    props.onEndDate(today)
  }

  return (
    <div style={{ border: '1px solid var(--bg-border)', borderRadius: 'var(--radius-lg)', background: 'var(--bg-surface)' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          width: '100%',
          padding: '10px 12px',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          color: 'var(--text-secondary)',
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        筛选
      </button>

      {open && (
        <div style={{ padding: '0 12px 12px 12px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Time range */}
          <Section title="时间范围">
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
              {PRESETS.map((p) => (
                <Chip key={p.label} onClick={() => applyPreset(p.days)}>{p.label}</Chip>
              ))}
              <Chip onClick={() => { props.onStartDate(null); props.onEndDate(null) }}>
                清除
              </Chip>
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12 }}>
              <input
                type="date"
                value={props.startDate ?? ''}
                onChange={(e) => props.onStartDate(e.target.value || null)}
                style={inputStyle}
              />
              <span style={{ color: 'var(--text-muted)' }}>至</span>
              <input
                type="date"
                value={props.endDate ?? ''}
                onChange={(e) => props.onEndDate(e.target.value || null)}
                style={inputStyle}
              />
            </div>
          </Section>

          {/* Apps */}
          <Section title={`应用${props.apps.length ? `（已选 ${props.apps.length}）` : ''}`}>
            {appList?.length ? (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {appList.slice(0, 20).map((a) => (
                  <Chip
                    key={a.name}
                    selected={props.apps.includes(a.name)}
                    onClick={() => toggle(props.apps, a.name, props.onApps)}
                  >
                    {a.name}
                    <span style={{ opacity: 0.6, marginLeft: 4 }}>{a.count}</span>
                  </Chip>
                ))}
              </div>
            ) : (
              <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>无应用数据</div>
            )}
          </Section>

          {/* Categories */}
          <Section title={`分类${props.categories.length ? `（已选 ${props.categories.length}）` : ''}`}>
            {categoryList.length ? (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {categoryList.map((c) => (
                  <Chip
                    key={c.id}
                    selected={props.categories.includes(c.id)}
                    onClick={() => toggle(props.categories, c.id, props.onCategories)}
                  >
                    {c.name}
                  </Chip>
                ))}
              </div>
            ) : (
              <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>加载中...</div>
            )}
          </Section>
        </div>
      )}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  background: 'var(--bg-raised)',
  border: '1px solid var(--bg-border)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)',
  padding: '5px 8px',
  fontSize: 12,
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, fontWeight: 500 }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Chip({
  children,
  onClick,
  selected = false,
}: {
  children: React.ReactNode
  onClick: () => void
  selected?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '4px 10px',
        borderRadius: 'var(--radius-pill)',
        fontSize: 11,
        border: `1px solid ${selected ? 'var(--accent-border)' : 'var(--bg-border)'}`,
        background: selected ? 'var(--accent-subtle)' : 'var(--bg-raised)',
        color: selected ? 'var(--accent)' : 'var(--text-secondary)',
        cursor: 'pointer',
        display: 'inline-flex',
        alignItems: 'center',
      }}
    >
      {children}
    </button>
  )
}
