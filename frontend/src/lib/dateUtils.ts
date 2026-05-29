import { format, formatDuration, intervalToDuration } from 'date-fns'
import { zhCN } from 'date-fns/locale'

export function epochToDate(ms: number): Date {
  return new Date(ms)
}

export function formatTime(ms: number): string {
  return format(new Date(ms), 'HH:mm:ss')
}

export function formatDate(ms: number): string {
  return format(new Date(ms), 'yyyy-MM-dd EEEE', { locale: zhCN })
}

export function formatDurationMs(ms: number): string {
  if (ms < 1000) return '< 1秒'
  const dur = intervalToDuration({ start: 0, end: ms })
  return formatDuration(dur, {
    locale: zhCN,
    format: ['hours', 'minutes', 'seconds'],
    zero: false,
  })
}

/** Return [dayStart epoch ms, dayEnd epoch ms] for a given Date */
export function dayRange(date: Date): [number, number] {
  const start = new Date(date)
  start.setHours(0, 0, 0, 0)
  const end = new Date(date)
  end.setHours(23, 59, 59, 999)
  return [start.getTime(), end.getTime()]
}

/** Format date to YYYY-MM-DD for URL params */
export function toDateParam(date: Date): string {
  return format(date, 'yyyy-MM-dd')
}

/** Parse YYYY-MM-DD string to Date (local timezone) */
export function fromDateParam(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
