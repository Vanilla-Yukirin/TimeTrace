import type { ApiScreenshot } from '@/types/api'

interface ThumbnailViewProps {
  screenshots?: ApiScreenshot[]
  thumbPath?: string | null
  screenshotCount?: number
  onZoom?: () => void
}

export function ThumbnailView({ screenshots, thumbPath, screenshotCount, onZoom }: ThumbnailViewProps) {
  // Phase A: 只显示第一张缩略图
  const rawPath = thumbPath || screenshots?.[0]?.thumb_path || null
  const url: string | null = rawPath ? `/thumbs/${rawPath.replace(/\\/g, '/')}` : null

  if (!url) {
    return (
      <div style={{
        width: '100%',
        aspectRatio: '16/9',
        background: 'var(--bg-raised)',
        border: '1px dashed var(--bg-border)',
        borderRadius: 6,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: 16,
      }}>
        <div style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
          <div>无截图</div>
          <div style={{ marginTop: 2, opacity: 0.7 }}>
            {screenshotCount === 0 ? '切换过快，未触发截图' : 'Phase 1.5 启用后自动捕获'}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <button
        type="button"
        onClick={onZoom}
        title={onZoom ? '点击放大查看' : undefined}
        disabled={!onZoom}
        style={{
          padding: 0,
          margin: 0,
          width: '100%',
          background: 'transparent',
          border: 'none',
          cursor: onZoom ? 'zoom-in' : 'default',
          display: 'block',
        }}
      >
        <img
          src={url}
          alt="活动缩略图"
          style={{
            width: '100%',
            borderRadius: 6,
            border: '1px solid var(--bg-border)',
            background: 'var(--bg-surface)',
            display: 'block',
          }}
          onError={(e) => {
            // 图片加载失败时显示占位（占位 div 是 button 的下一个兄弟节点）
            const target = e.target as HTMLElement
            const button = target.parentElement
            if (button) button.style.display = 'none'
            const placeholder = button?.nextElementSibling as HTMLElement | null
            if (placeholder) placeholder.style.display = 'flex'
          }}
        />
      </button>
      <div style={{
        display: 'none',
        width: '100%',
        aspectRatio: '16/9',
        background: 'var(--bg-raised)',
        border: '1px dashed var(--bg-border)',
        borderRadius: 6,
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        color: 'var(--text-muted)',
        fontSize: 12,
      }}>
        图片加载失败
      </div>
    </div>
  )
}
