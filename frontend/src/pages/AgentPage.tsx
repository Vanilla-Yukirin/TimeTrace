import { useRef, useState } from 'react'
import { Send, Wrench, Square } from 'lucide-react'
import { streamAgentChat, type AgentEvent, type ChatMessage } from '@/lib/agentApi'
import { CatMascot } from '@/components/brand/CatMascot'

// A "step" the agent took mid-answer (a tool call + its one-line result),
// surfaced inline so the user sees it consulting real data, not hallucinating.
interface ToolStep {
  tool: string
  args?: Record<string, unknown>
  summary?: string
}

interface Turn {
  role: 'user' | 'assistant'
  content: string
  steps?: ToolStep[]
  pending?: boolean
  error?: string
}

const TOOL_LABELS: Record<string, string> = {
  search_activity: '检索活动',
  get_recent_activity: '拉取最近活动',
  get_app_breakdown: '统计应用时长',
  get_category_stats: '统计分类时长',
  apply_label: '打标签',
}

const SUGGESTIONS = [
  '我今天主要在用哪些应用？各花了多久？',
  '我最近一周在忙什么？帮我总结一下。',
  '我有没有在摸鱼？花了多少时间在娱乐上？',
]

export function AgentPage() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  async function send(text: string) {
    const q = text.trim()
    if (!q || busy) return
    setInput('')
    setBusy(true)
    const history: ChatMessage[] = turns
      .filter((t) => !t.error)
      .map((t) => ({ role: t.role, content: t.content }))
    history.push({ role: 'user', content: q })
    setTurns((prev) => [
      ...prev,
      { role: 'user', content: q },
      { role: 'assistant', content: '', steps: [], pending: true },
    ])
    scrollToBottom()

    const ctrl = new AbortController()
    abortRef.current = ctrl
    const patchLast = (fn: (t: Turn) => Turn) =>
      setTurns((prev) => {
        const next = [...prev]
        next[next.length - 1] = fn(next[next.length - 1])
        return next
      })

    try {
      for await (const ev of streamAgentChat(history, { signal: ctrl.signal })) {
        applyEvent(ev, patchLast)
        scrollToBottom()
      }
    } catch (err) {
      if (!ctrl.signal.aborted) {
        patchLast((t) => ({ ...t, pending: false, error: String(err) }))
      }
    } finally {
      patchLast((t) => ({ ...t, pending: false }))
      setBusy(false)
      abortRef.current = null
    }
  }

  function applyEvent(ev: AgentEvent, patchLast: (fn: (t: Turn) => Turn) => void) {
    if (ev.type === 'step' && ev.phase === 'tool_call') {
      patchLast((t) => ({ ...t, steps: [...(t.steps ?? []), { tool: ev.tool, args: ev.args }] }))
    } else if (ev.type === 'step' && ev.phase === 'tool_result') {
      patchLast((t) => {
        const steps = [...(t.steps ?? [])]
        for (let i = steps.length - 1; i >= 0; i--) {
          if (steps[i].tool === ev.tool && steps[i].summary === undefined) {
            steps[i] = { ...steps[i], summary: ev.summary }
            break
          }
        }
        return { ...t, steps }
      })
    } else if (ev.type === 'token') {
      patchLast((t) => ({ ...t, content: t.content + ev.text }))
    } else if (ev.type === 'error') {
      patchLast((t) => ({ ...t, error: ev.message, pending: false }))
    }
  }

  function stop() {
    abortRef.current?.abort()
    setBusy(false)
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        minHeight: 0,
        maxWidth: 820,
        margin: '0 auto',
        width: '100%',
      }}
    >
      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '24px 20px' }}>
        {turns.length === 0 ? (
          <div
            style={{
              paddingTop: 40,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <CatMascot size={84} float />
            <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)', marginTop: 6 }}>
              问问你的活动记录
            </div>
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--text-muted)',
                maxWidth: 360,
                lineHeight: 1.6,
                textAlign: 'center',
                marginBottom: 6,
              }}
            >
              基于你电脑上记录的真实活动，我会查数据再回答——不编造。
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 480, width: '100%' }}>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  style={{
                    textAlign: 'left',
                    padding: '12px 16px',
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--bg-border)',
                    background: 'var(--bg-surface)',
                    color: 'var(--text-secondary)',
                    fontSize: 14,
                    cursor: 'pointer',
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {turns.map((t, i) => (
              <TurnBubble key={i} turn={t} />
            ))}
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
        style={{
          display: 'flex',
          gap: 10,
          padding: '14px 20px',
          borderTop: '1px solid var(--bg-border)',
          background: 'var(--bg-surface)',
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="问问你最近在干嘛…"
          disabled={busy}
          aria-label="输入问题"
          style={{
            flex: 1,
            padding: '12px 16px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--bg-border)',
            background: 'var(--bg-base)',
            color: 'var(--text-primary)',
            fontSize: 14,
            outline: 'none',
          }}
        />
        {busy ? (
          <button
            type="button"
            onClick={stop}
            aria-label="停止"
            style={{
              padding: '0 18px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--bg-border)',
              background: 'var(--bg-raised)',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            <Square size={16} />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="发送"
            style={{
              padding: '0 20px',
              borderRadius: 'var(--radius-md)',
              border: 'none',
              background: 'var(--grad-accent)',
              color: '#fff',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            <Send size={16} />
          </button>
        )}
      </form>
    </div>
  )
}

function TurnBubble({ turn }: { turn: Turn }) {
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
          }}
        >
          {turn.content}
        </div>
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
      {turn.steps && turn.steps.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {turn.steps.map((s, i) => (
            <span
              key={i}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                padding: '4px 10px',
                borderRadius: 'var(--radius-pill)',
                background: 'var(--bg-surface)',
                border: '1px solid var(--bg-border)',
                color: 'var(--text-muted)',
                fontSize: 12,
              }}
            >
              <Wrench size={12} aria-hidden="true" />
              {TOOL_LABELS[s.tool] ?? s.tool}
              {s.summary ? ` · ${s.summary}` : '…'}
            </span>
          ))}
        </div>
      )}
      {turn.error ? (
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
      ) : (
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
            whiteSpace: 'pre-wrap',
          }}
        >
          {turn.content || (turn.pending ? '思考中…' : '')}
          {turn.pending && turn.content ? ' ▍' : ''}
        </div>
      )}
    </div>
  )
}
