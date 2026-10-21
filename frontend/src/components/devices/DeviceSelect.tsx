import { Monitor } from 'lucide-react'
import { useDeviceDirectory, UNKNOWN_DEVICE, UNKNOWN_DEVICE_LABEL } from '@/hooks/useDevices'

interface DeviceSelectProps {
  /** Selected device key (uuid / _unknown_device); null = 全部设备. */
  value: string | null
  onChange: (next: string | null) => void
  /** Compact = no section title (inline filter rows). */
  compact?: boolean
}

/** 全部设备 / one device / 未知设备. Hidden while only one device exists and
 *  nothing is selected — a single-device install shouldn't see the control. */
export function DeviceSelect({ value, onChange, compact = false }: DeviceSelectProps) {
  const dir = useDeviceDirectory()
  if (!dir.multi && value === null) return null
  const options = [...dir.options]
  // A device from the URL that the directory doesn't list (yet) stays selectable.
  if (value && !options.some((o) => o.id === value)) {
    options.push({ id: value, label: value === UNKNOWN_DEVICE ? UNKNOWN_DEVICE_LABEL : value.slice(0, 8) })
  }
  return (
    <div>
      {!compact && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 11,
            color: 'var(--text-muted)',
            marginBottom: 6,
            fontWeight: 500,
          }}
        >
          <Monitor size={12} aria-hidden="true" />
          设备
        </div>
      )}
      <select
        aria-label="设备"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        style={{
          width: '100%',
          background: 'var(--bg-raised)',
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--text-primary)',
          padding: '6px 8px',
          fontSize: 12,
        }}
      >
        <option value="">全部设备</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  )
}

/** Small device chip for result rows / detail panel. Renders only when more
 *  than one device exists (single-device installs keep the old look). */
export function DeviceBadge({ deviceId }: { deviceId: string | null | undefined }) {
  const dir = useDeviceDirectory()
  if (!dir.multi) return null
  const key = deviceId ?? UNKNOWN_DEVICE
  const label = dir.labels[key] ?? key.slice(0, 8)
  return (
    <span
      title={`设备：${label}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: 11,
        lineHeight: 1,
        padding: '3px 8px',
        borderRadius: 'var(--radius-pill)',
        border: '1px solid var(--bg-border)',
        background: 'var(--bg-raised)',
        color: 'var(--text-secondary)',
        whiteSpace: 'nowrap',
      }}
    >
      <Monitor size={11} aria-hidden="true" />
      {label}
    </span>
  )
}
