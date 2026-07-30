/** Shared grid definition for the audit header, rows, and loading skeleton. */
export const AUDIT_COLS = '22px 92px 84px minmax(0, 1fr) 136px 88px 104px'

interface AuditHeader {
  label: string
  align?: 'left' | 'right'
}

export const AUDIT_HEADERS: readonly AuditHeader[] = [
  { label: '' },
  { label: '状态' },
  { label: '时间' },
  { label: '应用 / 标题' },
  { label: '分类' },
  { label: '上传延迟', align: 'right' },
  { label: '旗标' },
]
