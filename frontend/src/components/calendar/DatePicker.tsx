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
      <div className="flex items-center justify-between mb-2">
        <button
          onClick={() => setViewMonth(subMonths(viewMonth, 1))}
          className="p-1 rounded hover:bg-[var(--bg-raised)]"
          style={{ color: 'var(--text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}
        >
          <ChevronLeft size={14} />
        </button>
        <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
          {format(viewMonth, 'yyyy年M月', { locale: zhCN })}
        </span>
        <button
          onClick={() => setViewMonth(addMonths(viewMonth, 1))}
          className="p-1 rounded hover:bg-[var(--bg-raised)]"
          style={{ color: 'var(--text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}
        >
          <ChevronRight size={14} />
        </button>
      </div>

      {/* Week header */}
      <div className="grid grid-cols-7 mb-1">
        {['一', '二', '三', '四', '五', '六', '日'].map(d => (
          <div key={d} className="text-center text-xs" style={{ color: 'var(--text-muted)', padding: '2px 0' }}>
            {d}
          </div>
        ))}
      </div>

      {/* Day grid */}
      <div className="grid grid-cols-7 gap-px">
        {padStart.map((_, i) => <div key={`pad-${i}`} />)}
        {days.map(day => {
          const selected = isSameDay(day, value)
          const today = isToday(day)
          return (
            <button
              key={day.toISOString()}
              onClick={() => onChange(day)}
              className="text-center text-xs rounded py-1 transition-colors"
              style={{
                background: selected ? 'var(--accent)' : today ? 'var(--accent-subtle)' : 'transparent',
                color: selected ? '#fff' : today ? 'var(--accent-hover)' : 'var(--text-secondary)',
                border: 'none',
                cursor: 'pointer',
                fontWeight: selected || today ? '600' : '400',
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
