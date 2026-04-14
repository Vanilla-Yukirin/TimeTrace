import { useEffect } from 'react'
import { useRecord } from '@/hooks/useRecord'
import { useFeedback } from '@/hooks/useFeedback'
import { RecordMeta } from './RecordMeta'
import { CategoryBadge } from './CategoryBadge'
import { ThumbnailView } from './ThumbnailView'
import { FeedbackControls } from './FeedbackControls'

interface RecordDetailPanelProps {
  recordId: string | null
  onClose: () => void
}

export function RecordDetailPanel({ recordId, onClose }: RecordDetailPanelProps) {
  const { data: record, isLoading, error } = useRecord(recordId)
  const feedback = useFeedback()
  const { reset: resetFeedback } = feedback

  // Clear stale success/error state when the user switches to a different record
  useEffect(() => {
    resetFeedback()
  }, [recordId, resetFeedback])

  if (!recordId) {
    return (
      <div style={{
        width: 320,
        flexShrink: 0,
        borderLeft: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
        padding: 16,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <div style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
          <div>未选择活动</div>
          <div style={{ marginTop: 4, fontSize: 12, opacity: 0.7 }}>
            点击时间轴上的活动块查看详情
          </div>
        </div>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div style={{
        width: 320,
        flexShrink: 0,
        borderLeft: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
        padding: 16,
      }}>
        <div style={{
          height: 120,
          background: 'var(--bg-raised)',
          borderRadius: 6,
          marginBottom: 16,
        }} />
        <div style={{ height: 16, background: 'var(--bg-raised)', borderRadius: 4, marginBottom: 8, width: '70%' }} />
        <div style={{ height: 16, background: 'var(--bg-raised)', borderRadius: 4, width: '50%' }} />
      </div>
    )
  }

  if (error || !record) {
    return (
      <div style={{
        width: 320,
        flexShrink: 0,
        borderLeft: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
        padding: 16,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <div style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
          <div>活动不存在</div>
          <div style={{ marginTop: 4, fontSize: 12, opacity: 0.7 }}>
            该活动可能已被删除
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{
      width: 320,
      flexShrink: 0,
      borderLeft: '1px solid var(--bg-border)',
      background: 'var(--bg-surface)',
      padding: 16,
      overflowY: 'auto',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 16,
      }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
          活动详情
        </span>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            fontSize: 16,
            padding: 0,
            lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>

      {/* Thumbnail */}
      <ThumbnailView
        thumbPath={record.thumb_path}
        screenshots={record.screenshots}
      />

      {/* Meta info */}
      <div style={{ marginBottom: 16 }}>
        <RecordMeta record={record} />
      </div>

      {/* Category */}
      <div style={{
        padding: '12px 0',
        borderTop: '1px solid var(--bg-border)',
        marginBottom: 16,
      }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
          分类
        </div>
        <CategoryBadge
          category={record.category_final}
          confidence={record.confidence}
        />
        {record.vlm_desc == null && (
          <div style={{
            marginTop: 8,
            fontSize: 11,
            color: 'var(--text-muted)',
            padding: '6px 8px',
            background: 'var(--bg-raised)',
            borderRadius: 4,
            border: '1px solid var(--bg-border)',
          }}>
            画面描述（Phase 1.5 启用后自动生成）
          </div>
        )}
      </div>

      {/* Feedback controls */}
      <div style={{
        padding: '12px 0',
        borderTop: '1px solid var(--bg-border)',
      }}>
        <FeedbackControls record={record} feedback={feedback} />
      </div>

      {/* Feedback status */}
      {feedback.isSuccess && (
        <div style={{
          marginTop: 12,
          padding: '6px 8px',
          background: 'rgba(34, 197, 94, 0.15)',
          border: '1px solid rgba(34, 197, 94, 0.3)',
          borderRadius: 4,
          fontSize: 11,
          color: '#22c55e',
        }}>
          分类已更新
        </div>
      )}
      {feedback.isError && (
        <div style={{
          marginTop: 12,
          padding: '6px 8px',
          background: 'rgba(239, 68, 68, 0.15)',
          border: '1px solid rgba(239, 68, 68, 0.3)',
          borderRadius: 4,
          fontSize: 11,
          color: '#ef4444',
        }}>
          更新失败，请重试
        </div>
      )}
    </div>
  )
}
