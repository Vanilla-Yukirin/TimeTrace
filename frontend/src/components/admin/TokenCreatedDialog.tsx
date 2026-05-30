import { useEffect, useRef, useState } from 'react'
import { Copy, Check } from 'lucide-react'
import type { TokenCreated } from '@/types/api'

interface Props {
  token: TokenCreated
  /** Public origin to put in the .mcp.json template. Defaults to the current
   *  browser origin — correct in production (front+back same host) and for the
   *  dev proxy (5173 fronting 8765). */
  origin?: string
  onClose: () => void
}

/** Shown once after a token is minted. The value is NEVER retrievable again,
 *  so this dialog is the single chance to copy it — and we hand the user a
 *  ready-to-paste .mcp.json so they don't have to remember the wiring. */
export function TokenCreatedDialog({ token, origin, onClose }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null)

  // Move focus into the dialog on open and let Escape dismiss it — this is the
  // one-time secret reveal, so it must behave like a real modal for keyboard
  // users (not a plain overlay).
  useEffect(() => {
    dialogRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const base = (origin ?? window.location.origin).replace(/\/$/, '')
  const mcpJson = JSON.stringify(
    {
      mcpServers: {
        timetrace: {
          type: 'http',
          url: `${base}/mcp/`,
          headers: { Authorization: `Bearer ${token.value}` },
        },
      },
    },
    null,
    2,
  )

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 50,
        padding: 24,
      }}
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="token-created-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%',
          maxWidth: 560,
          maxHeight: '90vh',
          overflow: 'auto',
          padding: 24,
          background: 'var(--bg-surface)',
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-xl)',
          boxShadow: 'var(--shadow-lg)',
          outline: 'none',
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <div>
          <h2 id="token-created-title" style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
            Token 已创建：{token.label}
          </h2>
          <div style={{ fontSize: 12, color: 'var(--warning)', marginTop: 6 }}>
            ⚠ 此 token 仅显示这一次，请立即复制保存。关闭后无法再次查看。
          </div>
        </div>

        <CopyBlock label="Token 值" text={token.value} mono />

        <div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
            接入 Claude Code —— 粘贴到项目根的 <code>.mcp.json</code>，然后重启 Claude Code：
          </div>
          <CopyBlock label=".mcp.json" text={mcpJson} mono multiline />
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
            重启后 <code>claude mcp list</code> 应看到 <code>timetrace</code>。
          </div>
        </div>

        <button
          onClick={onClose}
          style={{
            alignSelf: 'flex-end',
            padding: '9px 18px',
            background: 'var(--grad-accent)',
            color: '#fff',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            fontSize: 14,
            fontWeight: 600,
            cursor: 'pointer',
            boxShadow: 'var(--shadow-glow)',
          }}
        >
          我已保存
        </button>
      </div>
    </div>
  )
}

interface CopyBlockProps {
  label: string
  text: string
  mono?: boolean
  multiline?: boolean
}

function CopyBlock({ label, text, mono, multiline }: CopyBlockProps) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API unavailable (insecure context). User can select manually.
    }
  }

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 4,
        }}
      >
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{label}</span>
        <button
          onClick={copy}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '3px 9px',
            background: 'transparent',
            color: copied ? 'var(--success)' : 'var(--text-secondary)',
            border: '1px solid var(--bg-border)',
            borderRadius: 'var(--radius-md)',
            fontSize: 11,
            cursor: 'pointer',
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: 12,
          background: 'var(--bg-raised)',
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-md)',
          fontSize: mono ? 12 : 13,
          fontFamily: mono ? 'JetBrains Mono, monospace' : 'inherit',
          color: 'var(--text-primary)',
          whiteSpace: multiline ? 'pre' : 'pre-wrap',
          wordBreak: multiline ? 'normal' : 'break-all',
          overflow: 'auto',
        }}
      >
        {text}
      </pre>
    </div>
  )
}
