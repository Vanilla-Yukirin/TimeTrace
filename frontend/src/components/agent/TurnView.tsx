import { memo, useEffect, useState, type CSSProperties } from 'react'
import { Brain, ChevronRight, Wrench } from 'lucide-react'
import { Markdown } from '@/components/ui/Markdown'
import type { ChatTurn } from '@/lib/agentSessions'
import type { Usage } from '@/lib/agentApi'

const TOOL_LABELS: Record<string, string> = {
  search_activity: '检索活动',
  get_recent_activity: '拉取最近活动',
  get_app_breakdown: '统计应用时长',
  get_category_stats: '统计分类时长',
  apply_label: '打标签',
}

const chipStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  padding: '4px 10px',
  borderRadius: 'var(--radius-pill)',
  background: 'var(--bg-surface)',
  border: '1px solid var(--bg-border)',
  color: 'var(--text-muted)',
  fontSize: 12,
}

function ToolChip({ tool, summary }: { tool: string; summary?: string }) {
  return (
    <span style={chipStyle}>
      <Wrench size={12} aria-hidden="true" />
      {TOOL_LABELS[tool] ?? tool}
      {summary ? ` · ${summary}` : '…'}
    </span>
  )
}

// Reasoning/thinking text: auto-expands while the turn streams, auto-collapses
// when it finishes; the user can toggle it any time.
function ThinkingBlock({ text, pending }: { text: string; pending: boolean }) {
  const [open, setOpen] = useState(pending)
  useEffect(() => {
    setOpen(pending)
  }, [pending])
  return (
    <div style={{ width: '100%' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '3px 6px',
          borderRadius: 'var(--radius-md)',
          background: 'transparent',
          border: 'none',
          color: 'var(--text-muted)',
          fontSize: 12,
          cursor: 'pointer',
        }}
      >
        <ChevronRight
          size={13}
          aria-hidden="true"
          style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}
        />
        <Brain size={13} aria-hidden="true" />
        思考过程
      </button>
      {open && (
        <div
          style={{
            margin: '2px 0 2px 9px',
            padding: '6px 12px',
            borderLeft: '2px solid var(--bg-border)',
            color: 'var(--text-muted)',
            fontSize: 12.5,
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            fontStyle: 'italic',
          }}
        >
          {text}
        </div>
      )}
    </div>
  )
}

const TextBubble = memo(function TextBubble({ text, cursor }: { text: string; cursor: boolean }) {
  return (
    <div
      style={{
        maxWidth: '90%',
        padding: '10px 14px',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--bg-raised)',
        border: '1px solid var(--bg-border)',
        color: 'var(--text-primary)',
        fontSize: 14,
        lineHeight: 1.6,
      }}
    >
      <Markdown text={text} trailing={cursor ? ' ▍' : null} />
    </div>
  )
})

const fmt = (n: number) => n.toLocaleString('en-US')

function UsageFooter({ usage, toolCount }: { usage?: Usage; toolCount: number }) {
  const parts: string[] = []
  if (usage && usage.total_tokens > 0) {
    parts.push(
      `输入 ${fmt(usage.prompt_tokens)} · 输出 ${fmt(usage.completion_tokens)} · 共 ${fmt(usage.total_tokens)} tokens`,
    )
  }
  if (toolCount > 0) parts.push(`工具 ${toolCount} 次`)
  if (parts.length === 0) return null
  return (
    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 1, paddingLeft: 2 }}>
      {parts.join(' · ')}
    </div>
  )
}

export const TurnView = memo(function TurnView({ turn }: { turn: ChatTurn }) {
  if (turn.role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <div
          style={{
            maxWidth: '80%',
            padding: '10px 14px',
            borderRadius: 'var(--radius-lg)',
            background: 'var(--grad-accent)',
            color: '#fff',
            fontSize: 14,
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
          }}
        >
          {turn.content}
        </div>
      </div>
    )
  }

  // Streaming cursor attaches to the LAST text block only.
  let lastTextIdx = -1
  for (let i = turn.blocks.length - 1; i >= 0; i--) {
    if (turn.blocks[i].kind === 'text') {
      lastTextIdx = i
      break
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
      {turn.blocks.map((b, i) => {
        if (b.kind === 'thinking') return <ThinkingBlock key={i} text={b.text} pending={!!turn.pending} />
        if (b.kind === 'tool') return <ToolChip key={i} tool={b.tool} summary={b.summary} />
        return <TextBubble key={i} text={b.text} cursor={!!turn.pending && i === lastTextIdx} />
      })}

      {turn.pending && turn.blocks.length === 0 && (
        <div
          style={{
            padding: '10px 14px',
            borderRadius: 'var(--radius-lg)',
            background: 'var(--bg-raised)',
            border: '1px solid var(--bg-border)',
            color: 'var(--text-muted)',
            fontSize: 14,
          }}
        >
          思考中…
        </div>
      )}

      {turn.error && (
        <div
          style={{
            padding: '10px 14px',
            borderRadius: 'var(--radius-lg)',
            background: 'var(--error-bg)',
            border: '1px solid var(--error)',
            color: 'var(--error)',
            fontSize: 14,
          }}
        >
          出错了：{turn.error}
        </div>
      )}

      <UsageFooter usage={turn.usage} toolCount={turn.toolCount} />
    </div>
  )
})
