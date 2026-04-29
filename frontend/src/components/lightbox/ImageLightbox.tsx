import { useCallback, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ChevronLeft, ChevronRight, ImageOff, X } from 'lucide-react'
import { CategoryBadge } from '@/components/detail/CategoryBadge'
import { formatTime, formatDate, formatDurationMs } from '@/lib/dateUtils'

export interface LightboxItem {
  id: string
  thumbPath: string | null
  tsStart: number
  tsEnd: number | null
  appName: string | null
  windowTitle: string | null
  url: string | null
  categoryFinal: string | null
  categoryConfidence?: number | null
  vlmDesc: string | null
}

interface ImageLightboxProps {
  items: LightboxItem[]
  index: number
  onIndexChange: (index: number) => void
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ImageLightbox({ items, index, onIndexChange, open, onOpenChange }: ImageLightboxProps) {
  const safeIndex = items.length > 0 ? Math.max(0, Math.min(index, items.length - 1)) : 0
  const item = items[safeIndex]
  const hasPrev = safeIndex > 0
  const hasNext = safeIndex < items.length - 1

  const goPrev = useCallback(() => {
    if (hasPrev) onIndexChange(safeIndex - 1)
  }, [hasPrev, onIndexChange, safeIndex])

  const goNext = useCallback(() => {
    if (hasNext) onIndexChange(safeIndex + 1)
  }, [hasNext, onIndexChange, safeIndex])

  // Global key handler — keeps arrow nav working even when focus drifts off the
  // dialog content (e.g. user just clicked a button, or focus is on body).
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        goPrev()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        goNext()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, goPrev, goNext])

  if (!item) return null

  const imgUrl = item.thumbPath ? `/thumbs/${item.thumbPath.replace(/\\/g, '/')}` : null
  const duration = item.tsEnd != null ? formatDurationMs(item.tsEnd - item.tsStart) : null

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          className="lightbox-overlay"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.78)',
            backdropFilter: 'blur(6px)',
            WebkitBackdropFilter: 'blur(6px)',
            zIndex: 100,
          }}
        />
        <Dialog.Content
          className="lightbox-content"
          aria-describedby={undefined}
          style={{
            position: 'fixed',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: 'min(1200px, 92vw)',
            maxHeight: '90vh',
            outline: 'none',
            zIndex: 101,
          }}
        >
          <Dialog.Title
            style={{
              position: 'absolute',
              width: 1,
              height: 1,
              padding: 0,
              margin: -1,
              overflow: 'hidden',
              clip: 'rect(0, 0, 0, 0)',
              whiteSpace: 'nowrap',
              border: 0,
            }}
          >
            活动截图：{item.windowTitle ?? item.appName ?? '未命名'}
          </Dialog.Title>

          {/* Image / placeholder area */}
          <div
            style={{
              position: 'relative',
              borderRadius: 8,
              overflow: 'hidden',
              background: '#000',
              boxShadow: '0 24px 64px rgba(0, 0, 0, 0.6)',
              border: '1px solid var(--bg-border)',
            }}
          >
            {imgUrl ? (
              <img
                key={item.id}
                src={imgUrl}
                alt={item.windowTitle ?? '活动截图'}
                className="lightbox-image-fade"
                style={{
                  display: 'block',
                  width: '100%',
                  maxHeight: 'calc(90vh - 0px)',
                  objectFit: 'contain',
                  background: '#000',
                }}
              />
            ) : (
              <div
                key={item.id}
                className="lightbox-image-fade"
                style={{
                  width: '100%',
                  aspectRatio: '16 / 9',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 12,
                  color: 'var(--text-muted)',
                  background: 'var(--bg-raised)',
                }}
              >
                <ImageOff size={42} />
                <div style={{ fontSize: 13 }}>该活动无截图</div>
              </div>
            )}

            {/* Close button (top-right, overlaid on image) */}
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="关闭"
                style={{
                  position: 'absolute',
                  top: 12,
                  right: 12,
                  width: 36,
                  height: 36,
                  borderRadius: 18,
                  background: 'rgba(15, 17, 23, 0.85)',
                  border: '1px solid rgba(255,255,255,0.15)',
                  color: '#f1f5f9',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                  backdropFilter: 'blur(4px)',
                  WebkitBackdropFilter: 'blur(4px)',
                  zIndex: 3,
                }}
              >
                <X size={18} />
              </button>
            </Dialog.Close>

            {/* Index indicator (top-left, overlaid on image) */}
            <div
              style={{
                position: 'absolute',
                top: 14,
                left: 14,
                padding: '4px 10px',
                borderRadius: 12,
                background: 'rgba(15, 17, 23, 0.85)',
                border: '1px solid rgba(255,255,255,0.12)',
                color: '#cbd5e1',
                fontSize: 11,
                fontWeight: 500,
                boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                backdropFilter: 'blur(4px)',
                WebkitBackdropFilter: 'blur(4px)',
                zIndex: 3,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {safeIndex + 1} / {items.length}
            </div>

            {/* Prev / Next buttons (overlaid on image edges) */}
            <NavButton side="left" disabled={!hasPrev} onClick={goPrev} label="上一条活动">
              <ChevronLeft size={24} />
            </NavButton>
            <NavButton side="right" disabled={!hasNext} onClick={goNext} label="下一条活动">
              <ChevronRight size={24} />
            </NavButton>

            {/* Bottom info bar — overlaid on the image */}
            <div
              style={{
                position: 'absolute',
                left: 0,
                right: 0,
                bottom: 0,
                padding: '24px 22px 18px 22px',
                background:
                  'linear-gradient(to top, rgba(0,0,0,0.92) 0%, rgba(0,0,0,0.78) 55%, rgba(0,0,0,0) 100%)',
                color: '#f1f5f9',
                pointerEvents: 'auto',
                zIndex: 2,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  flexWrap: 'wrap',
                  marginBottom: 6,
                }}
              >
                <CategoryBadge category={item.categoryFinal} confidence={item.categoryConfidence ?? null} />
                <span style={{ fontSize: 12, color: 'rgba(241,245,249,0.75)' }}>
                  {formatDate(item.tsStart)} · {formatTime(item.tsStart)}
                  {item.tsEnd != null ? ` – ${formatTime(item.tsEnd)}` : ''}
                  {duration ? ` · ${duration}` : ''}
                </span>
              </div>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  marginBottom: 4,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {item.appName ?? '未知应用'}
                {item.windowTitle ? <span style={{ fontWeight: 400, color: 'rgba(241,245,249,0.78)' }}> · {item.windowTitle}</span> : null}
              </div>
              {item.url && (
                <div
                  style={{
                    fontSize: 11,
                    color: 'rgba(148,163,184,0.95)',
                    marginBottom: 6,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {item.url}
                </div>
              )}
              {item.vlmDesc ? (
                <div
                  style={{
                    fontSize: 12,
                    lineHeight: 1.55,
                    color: 'rgba(226,232,240,0.92)',
                    maxHeight: 96,
                    overflowY: 'auto',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {item.vlmDesc}
                </div>
              ) : (
                <div style={{ fontSize: 11, color: 'rgba(148,163,184,0.7)', fontStyle: 'italic' }}>
                  画面描述：VLM 未启用或分析中
                </div>
              )}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function NavButton({
  side,
  disabled,
  onClick,
  label,
  children,
}: {
  side: 'left' | 'right'
  disabled: boolean
  onClick: () => void
  label: string
  children: React.ReactNode
}) {
  const sideStyle: React.CSSProperties =
    side === 'left' ? { left: 14 } : { right: 14 }
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      style={{
        position: 'absolute',
        top: '50%',
        ...sideStyle,
        transform: 'translateY(-50%)',
        width: 44,
        height: 44,
        borderRadius: 22,
        background: disabled ? 'rgba(15, 17, 23, 0.45)' : 'rgba(15, 17, 23, 0.85)',
        border: '1px solid rgba(255,255,255,0.15)',
        color: disabled ? 'rgba(148,163,184,0.55)' : '#f1f5f9',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
        backdropFilter: 'blur(4px)',
        WebkitBackdropFilter: 'blur(4px)',
        transition: 'background 120ms ease, opacity 120ms ease',
        zIndex: 3,
      }}
    >
      {children}
    </button>
  )
}
