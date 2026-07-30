import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useAuditRecords } from '@/hooks/useAuditRecords'
import { AuditRowItem } from '@/components/audit/AuditRow'
import { AUDIT_COLS, AUDIT_HEADERS } from '@/components/audit/auditLayout'
import { PageShell } from '@/components/ui/PageShell'
import { IconButton } from '@/components/ui/IconButton'
import { EmptyState, ErrorBanner, LiveBadge } from '@/components/ui/Feedback'

const SIZES = [50, 100, 200]

export function AuditPage() {
  const [limit, setLimit] = useState(50)
  const q = useAuditRecords(limit)
  const items = q.data?.items ?? []
  const polling = q.isFetching && !q.isLoading

  return (
    <PageShell>
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
            <LiveBadge active={polling} label={polling ? '刷新中' : '实时'} />
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
              className="tt-chip"
              data-active={limit === s || undefined}
              onClick={() => setLimit(s)}
            >
              最近 {s}
            </button>
          ))}
          <IconButton onClick={() => q.refetch()} title="立即刷新" aria-label="立即刷新">
            <RefreshCw size={14} className={polling ? 'tt-spin' : undefined} />
          </IconButton>
        </div>
      </div>

      {q.isError && (
        <ErrorBanner
          style={{ margin: '14px 0' }}
          title="加载失败"
          message={String((q.error as Error)?.message ?? q.error)}
          onRetry={() => q.refetch()}
          retrying={q.isRefetching}
        />
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
              <span key={i} style={{ textAlign: h.align ?? 'left', whiteSpace: 'nowrap' }}>
                {h.label}
              </span>
            ))}
          </div>

          {/* Body */}
          {q.isLoading ? (
            <AuditSkeletonRows />
          ) : items.length === 0 ? (
            <EmptyState mascot title="还没有记录" desc="采集开始后，这里会一条一条实时出现。" />
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
    </PageShell>
  )
}

function AuditSkeletonRows() {
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
            <div key={j} className="tt-skeleton" style={{ height: 12 }} />
          ))}
        </div>
      ))}
    </div>
  )
}
