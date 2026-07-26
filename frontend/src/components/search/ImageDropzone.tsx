import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, X } from 'lucide-react'

const MAX_IMAGES = 5

interface ImageDropzoneProps {
  images: File[]
  onChange: (files: File[]) => void
}

export function ImageDropzone({ images, onChange }: ImageDropzoneProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [dragging, setDragging] = useState(false)
  const [previews, setPreviews] = useState<string[]>([])

  useEffect(() => {
    const urls = images.map((f) => URL.createObjectURL(f))
    // Blob URLs are external resources: create/revoke them in one effect and
    // publish the resulting handles together to avoid leaking old previews.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPreviews(urls)
    return () => urls.forEach((u) => URL.revokeObjectURL(u))
  }, [images])

  const addFiles = useCallback(
    (incoming: File[]) => {
      if (!incoming.length) return
      const combined = [...images, ...incoming].slice(0, MAX_IMAGES)
      onChange(combined)
    },
    [images, onChange],
  )

  // Paste handler (active only when this page is focused)
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const items = Array.from(e.clipboardData?.items ?? [])
      const files = items
        .filter((it) => it.kind === 'file' && it.type.startsWith('image/'))
        .map((it) => it.getAsFile())
        .filter((f): f is File => f !== null)
      if (files.length > 0) {
        e.preventDefault()
        addFiles(files)
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [addFiles])

  const onFiles = useCallback(
    (list: FileList | null) => {
      if (!list) return
      addFiles(Array.from(list).filter((f) => f.type.startsWith('image/')))
    },
    [addFiles],
  )

  const removeAt = (idx: number) => onChange(images.filter((_, i) => i !== idx))

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        onFiles(e.dataTransfer.files)
      }}
      style={{
        border: `1px dashed ${dragging ? 'var(--accent)' : 'var(--bg-border)'}`,
        borderRadius: 'var(--radius-lg)',
        padding: images.length === 0 ? 16 : 12,
        background: 'var(--bg-surface)',
        transition: 'border-color 0.15s',
      }}
    >
      {images.length === 0 ? (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-secondary)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            cursor: 'pointer',
            fontSize: 13,
          }}
        >
          <Camera size={16} />
          点击、拖拽或 Ctrl+V 粘贴参考图
        </button>
      ) : (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          {previews.map((url, i) => (
            <div
              key={url}
              style={{
                position: 'relative',
                width: 96,
                height: 60,
                borderRadius: 'var(--radius-md)',
                overflow: 'hidden',
                border: '1px solid var(--bg-border)',
              }}
            >
              <img
                src={url}
                alt={`参考图 ${i + 1}`}
                style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              />
              {i === 0 && (
                <div
                  style={{
                    position: 'absolute',
                    top: 2,
                    left: 2,
                    padding: '1px 4px',
                    background: 'rgba(0, 0, 0, 0.6)',
                    color: 'white',
                    fontSize: 9,
                    borderRadius: 2,
                  }}
                >
                  pHash 基准
                </div>
              )}
              <button
                type="button"
                onClick={() => removeAt(i)}
                aria-label="移除"
                style={{
                  position: 'absolute',
                  top: 2,
                  right: 2,
                  width: 18,
                  height: 18,
                  borderRadius: 9,
                  background: 'rgba(0, 0, 0, 0.6)',
                  color: 'white',
                  border: 'none',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: 0,
                }}
              >
                <X size={12} />
              </button>
            </div>
          ))}
          {images.length < MAX_IMAGES && (
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              style={{
                width: 96,
                height: 60,
                border: '1px dashed var(--bg-border)',
                borderRadius: 'var(--radius-md)',
                background: 'transparent',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                fontSize: 11,
              }}
            >
              + 添加
            </button>
          )}
        </div>
      )}
      {images.length >= MAX_IMAGES && (
        <div style={{ marginTop: 6, fontSize: 10, color: 'var(--text-muted)' }}>
          最多 {MAX_IMAGES} 张参考图
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        style={{ display: 'none' }}
        onChange={(e) => {
          onFiles(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
