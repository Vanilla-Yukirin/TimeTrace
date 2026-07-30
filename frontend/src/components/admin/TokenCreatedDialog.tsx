import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Copy, Check } from 'lucide-react'
import type { TokenCreated } from '@/types/api'
import { Button } from '@/components/ui/Button'

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
    <Dialog.Root open>
      <Dialog.Portal>
        <Dialog.Overlay
          className="tt-overlay"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'var(--scrim)',
            zIndex: 50,
          }}
        />
        <Dialog.Content
          className="tt-modal-content"
          aria-describedby="token-created-warning"
          // This secret is shown exactly once. Outside clicks and Escape must
          // not discard it; only the explicit acknowledgement closes it.
          onPointerDownOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => e.preventDefault()}
          style={{
            position: 'fixed',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            zIndex: 51,
            width: 'calc(100% - 48px)',
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
          <Dialog.Title style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
            Token 已创建：{token.label}
          </Dialog.Title>
          <div id="token-created-warning" style={{ fontSize: 12, color: 'var(--warning)', marginTop: 6 }}>
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

        <Button variant="primary" onClick={onClose} style={{ alignSelf: 'flex-end', padding: '9px 18px', fontSize: 14 }}>
          我已保存
        </Button>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
          type="button"
          onClick={copy}
          className="tt-btn-ghost"
          style={{
            padding: '3px 9px',
            background: 'transparent',
            color: copied ? 'var(--success)' : 'var(--text-secondary)',
            fontSize: 11,
          }}
        >
          {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre
        className={mono ? 'font-mono' : undefined}
        style={{
          margin: 0,
          padding: 12,
          background: 'var(--bg-raised)',
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-md)',
          fontSize: mono ? 12 : 13,
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
