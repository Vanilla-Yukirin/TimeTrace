import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { format, addMonths, subMonths, startOfMonth, endOfMonth, eachDayOfInterval, isSameDay, isToday, getDay } from 'date-fns'
import { zhCN } from 'date-fns/locale'

interface DatePickerProps {
  value: Date
  onChange: (date: Date) => void
}

export function DatePicker({ value, onChange }: DatePickerProps) {
  const [viewMonth, setViewMonth] = useState(() => {
    const d = new Date(value)
    d.setDate(1)
    return d
  })

  const days = eachDayOfInterval({
    start: startOfMonth(viewMonth),
    end: endOfMonth(viewMonth),
  })

  // Pad start of week (Monday = 0 offset)
  const firstDow = (getDay(days[0]) + 6) % 7 // 0=Mon
  const padStart = Array(firstDow).fill(null)

  return (
    <div style={{ color: 'var(--text-primary)' }}>
      {/* Month navigation */}
      <div className="flex items-center justify-between" style={{ marginBottom: 10 }}>
        <button onClick={() => setViewMonth(subMonths(viewMonth, 1))} style={navBtn} aria-label="上个月">
          <ChevronLeft size={15} aria-hidden="true" />
        </button>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text-primary)' }}>
          {format(viewMonth, 'yyyy年M月', { locale: zhCN })}
        </span>
        <button onClick={() => setViewMonth(addMonths(viewMonth, 1))} style={navBtn} aria-label="下个月">
          <ChevronRight size={15} aria-hidden="true" />
        </button>
      </div>

      {/* Week header */}
      <div className="grid grid-cols-7" style={{ marginBottom: 4 }}>
        {['一', '二', '三', '四', '五', '六', '日'].map((d, i) => (
          <div
            key={d}
            className="text-center"
            style={{ color: i >= 5 ? 'var(--text-secondary)' : 'var(--text-muted)', fontSize: 11, padding: '2px 0' }}
          >
            {d}
          </div>
        ))}
      </div>

      {/* Day grid */}
      <div className="grid grid-cols-7" style={{ gap: 2 }}>
        {padStart.map((_, i) => <div key={`pad-${i}`} />)}
        {days.map(day => {
          const selected = isSameDay(day, value)
          const today = isToday(day)
          return (
            <button
              key={day.toISOString()}
              onClick={() => onChange(day)}
              aria-label={format(day, 'M月d日 EEEE', { locale: zhCN })}
              aria-pressed={selected}
              aria-current={today ? 'date' : undefined}
              className="text-center transition-colors"
              style={{
                aspectRatio: '1 / 1',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 12.5,
                borderRadius: 'var(--radius-md)',
                background: selected ? 'var(--grad-accent)' : 'transparent',
                color: selected ? '#fff' : today ? 'var(--accent)' : 'var(--text-secondary)',
                border: today && !selected ? '1px solid var(--accent-border)' : '1px solid transparent',
                cursor: 'pointer',
                fontWeight: selected || today ? 700 : 400,
                boxShadow: selected ? 'var(--shadow-glow)' : undefined,
              }}
            >
              {day.getDate()}
            </button>
          )
        })}
      </div>
    </div>
  )
}

const navBtn: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 26,
  height: 26,
  borderRadius: 'var(--radius-md)',
  color: 'var(--text-muted)',
  background: 'transparent',
  border: '1px solid transparent',
  cursor: 'pointer',
}
