import type { ApiScreenshot } from '@/types/api'

interface ThumbnailViewProps {
  screenshots?: ApiScreenshot[]
  thumbPath?: string | null
  screenshotCount?: number
}

export function ThumbnailView({ screenshots, thumbPath, screenshotCount }: ThumbnailViewProps) {
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
