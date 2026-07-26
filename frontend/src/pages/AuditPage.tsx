import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useAuditRecords } from '@/hooks/useAuditRecords'
import { AuditRowItem } from '@/components/audit/AuditRow'
import { AUDIT_COLS, AUDIT_HEADERS } from '@/components/audit/auditLayout'
import { CatMascot } from '@/components/brand/CatMascot'

const SIZES = [50, 100, 200]

export function AuditPage() {
  const [limit, setLimit] = useState(50)
  const q = useAuditRecords(limit)
  const items = q.data?.items ?? []
  const polling = q.isFetching && !q.isLoading

  return (
    <div style={{ flex: 1, overflowY: 'auto', width: '100%' }}>
      <div style={{ maxWidth: 1120, margin: '0 auto', padding: '20px 18px 40px', width: '100%' }}>
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: 6,
          }}
        >
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 18,
                fontWeight: 700,
                color: 'var(--text-primary)',
              }}
            >
              审计日志
              <span
                title={polling ? '实时刷新中' : '每 4 秒自动刷新'}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 11,
                  fontWeight: 600,
                  color: polling ? 'var(--success)' : 'var(--text-muted)',
                }}
              >
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: polling ? 'var(--success)' : 'var(--text-muted)',
                  }}
                />
                {polling ? '刷新中' : '实时'}
              </span>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 4, maxWidth: 560, lineHeight: 1.6 }}>
              一条一条地审计每条记录的采集 → 分析进度：当前状态、活动时间与收到时间、上传延迟，
              点开任意一行看截图、各阶段耗时与完整字段。每 4 秒自动刷新。
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
            {SIZES.map((s) => (
              <button
                key={s}
                onClick={() => setLimit(s)}
                style={{
                  padding: '5px 11px',
                  borderRadius: 'var(--radius-pill)',
                  border: '1px solid var(--bg-border)',
                  background: limit === s ? 'var(--accent-subtle)' : 'transparent',
                  color: limit === s ? 'var(--accent)' : 'var(--text-secondary)',
                  fontWeight: limit === s ? 600 : 500,
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                最近 {s}
              </button>
            ))}
            <button
              onClick={() => q.refetch()}
              title="立即刷新"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                padding: 7,
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--bg-border)',
                background: 'var(--bg-surface)',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              <RefreshCw size={14} className={polling ? 'tt-spin' : undefined} />
            </button>
          </div>
        </div>

        {q.isError && (
          <div
            style={{
              padding: '12px 16px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--error-bg)',
              border: '1px solid var(--error)',
              color: 'var(--error)',
              fontSize: 13,
              margin: '14px 0',
            }}
          >
            加载失败：{String((q.error as Error)?.message ?? q.error)}
            <button
              onClick={() => q.refetch()}
              style={{
                marginLeft: 10,
                padding: '2px 10px',
                borderRadius: 'var(--radius-pill)',
                border: '1px solid var(--error)',
                background: 'transparent',
                color: 'var(--error)',
                cursor: 'pointer',
                fontSize: 12,
              }}
            >
              重试
            </button>
          </div>
        )}

        {/* Table */}
        <div style={{ marginTop: 14, overflowX: 'auto' }}>
          <div
            style={{
              minWidth: 760,
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-lg)',
              background: 'var(--bg-surface)',
              overflow: 'hidden',
            }}
          >
            {/* Column header */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: AUDIT_COLS,
                alignItems: 'center',
                gap: 10,
                padding: '8px 12px',
                position: 'sticky',
                top: 0,
                zIndex: 1,
                background: 'var(--bg-raised)',
                borderBottom: '1px solid var(--bg-border)',
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.03em',
                color: 'var(--text-muted)',
              }}
            >
              {AUDIT_HEADERS.map((h, i) => (
                <span
                  key={i}
                  style={{ textAlign: h === '上传延迟' ? 'right' : 'left', whiteSpace: 'nowrap' }}
                >
                  {h}
                </span>
              ))}
            </div>

            {/* Body */}
            {q.isLoading ? (
              <SkeletonRows />
            ) : items.length === 0 ? (
              <div
                style={{
                  padding: 36,
                  textAlign: 'center',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <CatMascot size={72} float />
                <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>
                  还没有记录
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  采集开始后，这里会一条一条实时出现。
                </div>
              </div>
            ) : (
              items.map((row) => <AuditRowItem key={row.id} row={row} />)
            )}
          </div>
        </div>

        {!q.isLoading && items.length > 0 && (
          <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--text-muted)', textAlign: 'center' }}>
            显示最近 {items.length} 条 · 按活动时间倒序
          </div>
        )}
      </div>
    </div>
  )
}

function SkeletonRows() {
  return (
    <div>
      {Array.from({ length: 8 }).map((_, i) => (
        <div
          key={i}
          style={{
            display: 'grid',
            gridTemplateColumns: AUDIT_COLS,
            gap: 10,
            padding: '12px',
            borderBottom: '1px solid var(--bg-border)',
          }}
        >
          {AUDIT_HEADERS.map((_h, j) => (
            <div
              key={j}
              style={{
                height: 12,
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-raised)',
                opacity: 0.6,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}
