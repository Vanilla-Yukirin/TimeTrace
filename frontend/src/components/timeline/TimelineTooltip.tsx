import { createPortal } from 'react-dom'
import type { ApiRecord } from '@/types/api'
import { formatTime, formatDurationMs } from '@/lib/dateUtils'

interface TimelineTooltipProps {
  record: ApiRecord
  x: number
  y: number
}

export function TimelineTooltip({ record, x, y }: TimelineTooltipProps) {
  const duration = record.ts_end
    ? record.ts_end - record.ts_start
    : Date.now() - record.ts_start
  const startStr = formatTime(record.ts_start)
  const endStr = record.ts_end ? formatTime(record.ts_end) : '进行中'
  const durationStr = formatDurationMs(duration)

  const tooltip = (
    <div
      style={{
        position: 'fixed',
        left: x + 14,
        top: y - 10,
        zIndex: 9999,
        pointerEvents: 'none',
        background: 'var(--bg-raised)',
        border: '1px solid var(--bg-border)',
        borderRadius: 6,
        padding: '6px 10px',
        minWidth: 160,
        maxWidth: 280,
        boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
      }}
    >
      <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-primary)', marginBottom: 3 }}>
        {record.app_name}
      </div>
      {record.window_title && (
        <div style={{
          fontSize: 11,
          color: 'var(--text-secondary)',
          marginBottom: 4,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          maxWidth: 260,
        }}>
          {record.window_title}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {startStr} – {endStr}
        <span style={{ marginLeft: 6, color: 'var(--text-secondary)' }}>{durationStr}</span>
      </div>
      {record.category_final && (
        <div style={{
          marginTop: 4,
          fontSize: 11,
          color: 'var(--accent-hover)',
          fontWeight: 500,
        }}>
          {record.category_final}
        </div>
      )}
    </div>
  )

  return createPortal(tooltip, document.body)
}
