import { useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useRecord } from '@/hooks/useRecord'
import { useFeedback } from '@/hooks/useFeedback'
import { CatMascot } from '@/components/brand/CatMascot'
import { categoryColor } from '@/lib/categories'
import { RecordMeta } from './RecordMeta'
import { CategoryBadge } from './CategoryBadge'
import { ThumbnailView } from './ThumbnailView'
import { FeedbackControls } from './FeedbackControls'

const PANEL_W = 340

interface RecordDetailPanelProps {
  recordId: string | null
  onClose: () => void
  onZoom?: (recordId: string) => void
  /** Mobile: render as a full-screen overlay instead of a fixed side column. */
  isMobile?: boolean
}

/** Outer shell shared by every panel state. Desktop = fixed side column; mobile
 *  = a Radix Dialog full-screen sheet (focus trap/restore, background inert,
 *  Escape, body scroll-lock for free — same primitive as ImageLightbox) so it
 *  doesn't squeeze the timeline into an unusable sliver. */
function Shell({
  children,
  isMobile,
  onClose,
}: {
  children: React.ReactNode
  isMobile?: boolean
  onClose?: () => void
}) {
  if (isMobile) {
    return (
      <Dialog.Root open onOpenChange={(o) => { if (!o) onClose?.() }}>
        <Dialog.Portal>
          <Dialog.Overlay
            className="tt-overlay"
            style={{ position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 49 }}
          />
          <Dialog.Content
            className="tt-sheet-content"
            aria-label="活动详情"
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 50,
              background: 'var(--bg-surface)',
              display: 'flex',
              flexDirection: 'column',
              overflowY: 'auto',
              outline: 'none',
            }}
          >
            <Dialog.Title className="sr-only">活动详情</Dialog.Title>
            {children}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    )
  }
  return (
    <aside
      style={{
        width: PANEL_W,
        flexShrink: 0,
        borderLeft: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
      }}
    >
      {children}
    </aside>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  )
}

export function RecordDetailPanel({ recordId, onClose, onZoom, isMobile = false }: RecordDetailPanelProps) {
  const { data: record, isLoading, error } = useRecord(recordId)
  const feedback = useFeedback()
  const { reset: resetFeedback } = feedback

  // Clear stale success/error state when the user switches to a different record
  useEffect(() => {
    resetFeedback()
  }, [recordId, resetFeedback])

  // Mobile: nothing selected → render nothing (no empty side column / overlay).
  if (!recordId) {
    if (isMobile) return null
    return (
      <Shell isMobile={isMobile} onClose={onClose}>
        <Centered>
          <div>
            <CatMascot size={76} float style={{ margin: '0 auto', opacity: 0.9 }} />
            <div style={{ marginTop: 12, color: 'var(--text-secondary)', fontSize: 13, fontWeight: 500 }}>
              未选择活动
            </div>
            <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-muted)' }}>
              点击时间轴上的活动块查看详情
            </div>
          </div>
        </Centered>
      </Shell>
    )
  }

  if (isLoading) {
    return (
      <Shell isMobile={isMobile} onClose={onClose}>
        <div style={{ padding: 16 }}>
          <div className="tt-fade" style={{ height: 150, background: 'var(--bg-raised)', borderRadius: 'var(--radius-lg)', marginBottom: 16 }} />
          <div className="tt-fade" style={{ height: 16, background: 'var(--bg-raised)', borderRadius: 6, marginBottom: 8, width: '70%' }} />
          <div className="tt-fade" style={{ height: 16, background: 'var(--bg-raised)', borderRadius: 6, width: '50%' }} />
        </div>
      </Shell>
    )
  }

  if (error || !record) {
    return (
      <Shell isMobile={isMobile} onClose={onClose}>
        <Centered>
          <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            <div style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>活动不存在</div>
            <div style={{ marginTop: 4, fontSize: 12 }}>该活动可能已被删除</div>
          </div>
        </Centered>
      </Shell>
    )
  }

  const accent = categoryColor(record.category_final)

  return (
    <Shell isMobile={isMobile} onClose={onClose}>
      <div className="tt-fade" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              color: 'var(--text-muted)',
            }}
          >
            活动详情
          </span>
          <button
            onClick={onClose}
            aria-label="关闭"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 28,
              height: 28,
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-raised)',
              border: '1px solid var(--bg-border)',
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            <X size={15} />
          </button>
        </div>

        {/* Cover */}
        <ThumbnailView
          thumbPath={record.thumb_path}
          screenshots={record.screenshots}
          screenshotCount={record.screenshot_count}
          onZoom={onZoom ? () => onZoom(record.id) : undefined}
        />

        {/* Title + meta */}
        <Section>
          <div
            style={{
              borderLeft: `3px solid ${accent}`,
              paddingLeft: 12,
              marginBottom: 12,
            }}
          >
            <RecordMeta record={record} />
          </div>
        </Section>

        {/* Category + scene description */}
        <Section title="分类">
          <CategoryBadge category={record.category_final} confidence={record.confidence} />
          {record.vlm_desc != null ? (
            <p
              style={{
                margin: '10px 0 0',
                fontSize: 12.5,
                lineHeight: 1.6,
                color: 'var(--text-secondary)',
                padding: '10px 12px',
                background: 'var(--bg-raised)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--bg-border)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {record.vlm_desc}
            </p>
          ) : (
            <div
              style={{
                marginTop: 10,
                fontSize: 11.5,
                color: 'var(--text-muted)',
                padding: '8px 10px',
                background: 'var(--bg-raised)',
                borderRadius: 'var(--radius-md)',
                border: '1px dashed var(--bg-border)',
              }}
            >
              画面描述（VLM 分析中或未启用）
            </div>
          )}
        </Section>

        {/* Feedback */}
        <Section title="校正">
          <FeedbackControls record={record} feedback={feedback} />
          {feedback.isSuccess && (
            <div style={statusStyle('var(--success)', 'var(--success-bg)')}>分类已更新</div>
          )}
          {feedback.isError && (
            <div style={statusStyle('var(--error)', 'var(--error-bg)')}>更新失败，请重试</div>
          )}
        </Section>
      </div>
    </Shell>
  )
}

function Section({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <div>
      {title && (
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.04em',
            textTransform: 'uppercase',
            color: 'var(--text-muted)',
            marginBottom: 8,
          }}
        >
          {title}
        </div>
      )}
      {children}
    </div>
  )
}

function statusStyle(color: string, bg: string): React.CSSProperties {
  return {
    marginTop: 10,
    padding: '7px 10px',
    background: bg,
    border: `1px solid ${color}`,
    borderRadius: 'var(--radius-md)',
    fontSize: 11.5,
    color,
  }
}
