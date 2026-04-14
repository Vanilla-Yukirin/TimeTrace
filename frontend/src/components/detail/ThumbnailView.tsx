import type { ApiScreenshot } from '@/types/api'

interface ThumbnailViewProps {
  screenshots?: ApiScreenshot[]
  thumbPath?: string | null
}

export function ThumbnailView({ screenshots, thumbPath }: ThumbnailViewProps) {
  // Phase A: 只显示第一张缩略图
  const hasThumb = thumbPath && thumbPath.length > 0

  if (!hasThumb && (!screenshots || screenshots.length === 0)) {
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
            Phase 1.5 启用后自动捕获
          </div>
        </div>
      </div>
    )
  }

  const url = thumbPath ? `/thumbs/${thumbPath.replace(/\\/g, '/')}` : ''

  return (
    <div style={{ marginBottom: 16 }}>
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
          // 图片加载失败时显示占位
          const target = e.target as HTMLElement
          target.style.display = 'none'
          const placeholder = target.nextElementSibling as HTMLElement
          if (placeholder) placeholder.style.display = 'flex'
        }}
      />
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
