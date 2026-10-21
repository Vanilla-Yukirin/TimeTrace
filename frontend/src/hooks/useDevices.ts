import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { ApiRecord, DevicesResponse } from '@/types/api'

/** Read-side bucket for records captured before device identity existed. */
export const UNKNOWN_DEVICE = '_unknown_device'
export const UNKNOWN_DEVICE_LABEL = '未知设备'

export interface DeviceOption {
  id: string
  label: string
}

export interface DeviceDirectory {
  /** Selectable devices in display order (unknown last, only if it has records). */
  options: DeviceOption[]
  /** Lane / sort order of device keys (stable reference). */
  order: string[]
  /** Device key → label (stable reference). */
  labels: Record<string, string>
  /** More than one device has uploaded — show badges / lanes / selectors. */
  multi: boolean
}

/** The device directory (GET /v1/devices). Rarely changes → 5 min stale. */
export function useDevices() {
  return useQuery({
    queryKey: queryKeys.devices(),
    queryFn: () => apiFetch<DevicesResponse>('/v1/devices'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
}

/** Directory derived from useDevices, safe to pass as memoized props. Falls back
 *  to an empty directory while loading / on an older server without the route. */
export function useDeviceDirectory(): DeviceDirectory {
  const { data } = useDevices()
  return useMemo(() => {
    const options: DeviceOption[] = (data?.items ?? []).map((d) => ({ id: d.id, label: d.label }))
    if (data?.unknown && data.unknown.record_count > 0) {
      options.push({ id: UNKNOWN_DEVICE, label: data.unknown.label || UNKNOWN_DEVICE_LABEL })
    }
    const labels: Record<string, string> = { [UNKNOWN_DEVICE]: UNKNOWN_DEVICE_LABEL }
    for (const o of options) labels[o.id] = o.label
    return { options, order: options.map((o) => o.id), labels, multi: options.length > 1 }
  }, [data])
}

export function deviceKeyOf(rec: Pick<ApiRecord, 'device_id'>): string {
  return rec.device_id ?? UNKNOWN_DEVICE
}
