import { useState } from 'react'
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { SearchResultItem } from '@/types/api'
import { formatTime, toDateParam } from '@/lib/dateUtils'

interface ResultRowProps {
  item: SearchResultItem
}

export function ResultRow({ item }: ResultRowProps) {
  const [expanded, setExpanded] = useState(false)
  const navigate = useNavigate()

  const thumb = item.thumb_path ? `/thumbs/${item.thumb_path.replace(/\\/g, '/')}` : null
  const date = new Date(item.ts_start)

  const jumpToTimeline = (e: React.MouseEvent) => {
    e.stopPropagation()
    const params = new URLSearchParams()
    params.set('date', toDateParam(date))
    params.set('highlight', item.record_id)
    navigate(`/?${params.toString()}`)
  }

  return (
    <div
      style={{
        border: '1px solid var(--bg-border)',
        borderRadius: 6,
        background: 'var(--bg-surface)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={() => setExpanded((s) => !s)}
        style={{
          width: '100%',
          padding: 10,
          background: 'transparent',
          border: 'none',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <div style={{ flexShrink: 0, color: 'var(--text-muted)' }}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
        <div
          style={{
            width: 80,
            height: 50,
            flexShrink: 0,
            background: 'var(--bg-raised)',
            border: '1px solid var(--bg-border)',
            borderRadius: 4,
            overflow: 'hidden',
          }}
        >
          {thumb ? (
            <img
              src={thumb}
              alt=""
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
            />
          ) : null}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12,
              color: 'var(--text-muted)',
              marginBottom: 2,
            }}
          >
            <span>{date.toLocaleDateString()}</span>
            <span>{formatTime(item.ts_start)}</span>
            <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{item.app_name}</span>
            {item.match.reasons.map((r) => (
              <ReasonBadge key={r} text={r} />
            ))}
          </div>
          <div
            style={{
              fontSize: 13,
              color: 'var(--text-primary)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {item.window_title || '(无标题)'}
          </div>
          {item.vlm_desc && (
            <div
              style={{
                fontSize: 11,
                color: 'var(--text-muted)',
                marginTop: 2,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {item.vlm_desc.slice(0, 80)}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={jumpToTimeline}
          title="在时间轴中查看"
          style={{
            flexShrink: 0,
            padding: '5px 9px',
            background: 'var(--bg-raised)',
            border: '1px solid var(--bg-border)',
            borderRadius: 4,
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: 11,
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          <ExternalLink size={12} />
          时间轴
        </button>
      </button>

      {expanded && (
        <div
          style={{
            padding: 12,
            borderTop: '1px solid var(--bg-border)',
            background: 'var(--bg-base)',
            fontSize: 12,
            color: 'var(--text-secondary)',
          }}
        >
          {thumb && (
            <img
              src={thumb}
              alt=""
              style={{
                width: '100%',
                maxHeight: 320,
                objectFit: 'contain',
                border: '1px solid var(--bg-border)',
                borderRadius: 4,
                marginBottom: 8,
                background: 'black',
              }}
            />
          )}
          <Field label="分类" value={item.category_final ?? '未分类'} />
          {item.url && <Field label="URL" value={item.url} />}
          <Field
            label="描述"
            value={item.vlm_desc ?? '（画面描述：VLM 未启用）'}
            muted={!item.vlm_desc}
          />
          <Field
            label="匹配"
            value={item.match.reasons.length ? item.match.reasons.join('、') : '时间序列'}
          />
        </div>
      )}
    </div>
  )
}

function Field({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
      <span style={{ color: 'var(--text-muted)', minWidth: 36 }}>{label}</span>
      <span style={{ color: muted ? 'var(--text-muted)' : 'var(--text-secondary)', flex: 1, wordBreak: 'break-word' }}>
        {value}
      </span>
    </div>
  )
}

function ReasonBadge({ text }: { text: string }) {
  return (
    <span
      style={{
        padding: '1px 6px',
        fontSize: 10,
        borderRadius: 8,
        background: 'var(--accent-subtle)',
        color: 'var(--accent-hover)',
        fontWeight: 500,
      }}
    >
      {text}
    </span>
  )
}
