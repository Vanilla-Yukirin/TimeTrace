import type { ApiRecord } from '@/types/api'
import { formatTime, formatDurationMs } from '@/lib/dateUtils'
import { useNow } from '@/hooks/useNow'

interface RecordMetaProps {
  record: ApiRecord
}

export function RecordMeta({ record }: RecordMetaProps) {
  const isActive = !record.ts_end
  const now = useNow(isActive)
  const duration = (record.ts_end ?? now ?? record.ts_start) - record.ts_start

  return (
    <div style={{ fontSize: 13 }}>
      {/* App name */}
      <div style={{
        fontWeight: 800,
        fontSize: 16,
        color: 'var(--text-primary)',
        marginBottom: 5,
        lineHeight: 1.25,
        wordBreak: 'break-word',
      }}>
        {record.app_name}
      </div>

      {/* Window title */}
      {record.window_title && (
        <div style={{
          color: 'var(--text-secondary)',
          marginBottom: 8,
          fontSize: 12,
          lineHeight: '1.5',
          wordBreak: 'break-word',
        }}>
          {record.window_title}
        </div>
      )}

      {/* Time range */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <MetaRow label="开始" value={formatTime(record.ts_start)} />
        <MetaRow
          label="结束"
          value={isActive ? '进行中' : formatTime(record.ts_end!)}
          valueStyle={isActive ? { color: 'var(--accent-hover)', fontWeight: 600 } : undefined}
        />
        <MetaRow label="时长" value={formatDurationMs(duration)} />
        {record.screenshot_count != null && record.screenshot_count > 0 && (
          <MetaRow label="截图" value={`${record.screenshot_count} 张`} />
        )}
      </div>
    </div>
  )
}

function MetaRow({
  label,
  value,
  valueStyle,
}: {
  label: string
  value: string
  valueStyle?: React.CSSProperties
}) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
      <span style={{ color: 'var(--text-muted)', fontSize: 11, minWidth: 32 }}>{label}</span>
      <span style={{ color: 'var(--text-secondary)', fontSize: 12, ...valueStyle }}>{value}</span>
    </div>
  )
}
