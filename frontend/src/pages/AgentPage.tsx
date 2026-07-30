import { useEffect, useMemo, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Menu, Send, Square } from 'lucide-react'
import { streamAgentChat, type AgentEvent, type ChatMessage } from '@/lib/agentApi'
import {
  deriveTitle,
  loadSessions,
  newSession,
  saveSessions,
  statsForSessions,
  type AgentSession,
  type AssistantTurn,
  type ChatTurn,
} from '@/lib/agentSessions'
import { useIsMobile } from '@/hooks/useIsMobile'
import { CatMascot } from '@/components/brand/CatMascot'
import { AgentSidebar } from '@/components/agent/AgentSidebar'
import { TurnView } from '@/components/agent/TurnView'
import { Button } from '@/components/ui/Button'
import { IconButton } from '@/components/ui/IconButton'

const SUGGESTIONS = [
  '我今天主要在用哪些应用？各花了多久？',
  '我最近一周在忙什么？帮我总结一下。',
  '我有没有在摸鱼？花了多少时间在娱乐上？',
]

// ---- pure block-builders: fold a streamed event into the assistant turn ------
function appendText(t: AssistantTurn, text: string): AssistantTurn {
  const blocks = [...t.blocks]
  const last = blocks[blocks.length - 1]
  if (last && last.kind === 'text') blocks[blocks.length - 1] = { kind: 'text', text: last.text + text }
  else blocks.push({ kind: 'text', text })
  return { ...t, blocks }
}

function appendThinking(t: AssistantTurn, text: string): AssistantTurn {
  const blocks = [...t.blocks]
  const last = blocks[blocks.length - 1]
  if (last && last.kind === 'thinking') blocks[blocks.length - 1] = { kind: 'thinking', text: last.text + text }
  else blocks.push({ kind: 'thinking', text })
  return { ...t, blocks }
}

function fillToolResult(t: AssistantTurn, tool: string, summary: string): AssistantTurn {
  const blocks = [...t.blocks]
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i]
    if (b.kind === 'tool' && b.tool === tool && b.summary === undefined) {
      blocks[i] = { ...b, summary }
      break
    }
  }
  return { ...t, blocks }
}

export function AgentPage() {
  // Start on a fresh blank chat with the persisted history below it (ChatGPT-style).
  const [{ initialSessions, initialActive }] = useState(() => {
    const blank = newSession()
    return { initialSessions: [blank, ...loadSessions()], initialActive: blank.id }
  })
  const [sessions, setSessions] = useState<AgentSession[]>(initialSessions)
  const [activeId, setActiveId] = useState(initialActive)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const isMobile = useIsMobile()
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const sessionsRef = useRef(sessions)
  sessionsRef.current = sessions

  const active = sessions.find((s) => s.id === activeId) ?? sessions[0]
  const stats = useMemo(() => statsForSessions(sessions), [sessions])

  // Debounced persistence: stream updates fire many setSessions per second, so
  // we only hit localStorage ~once the burst settles (saveSessions filters out
  // empty drafts + caps history).
  useEffect(() => {
    const id = setTimeout(() => saveSessions(sessions), 400)
    return () => clearTimeout(id)
  }, [sessions])

  // Flush latest state on unmount (route change) so an exchange that completed
  // inside the 400ms debounce window isn't lost when navigating away.
  useEffect(() => () => saveSessions(sessionsRef.current), [])

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  useEffect(scrollToBottom, [activeId])

  // Patch the last (assistant) turn of session `sid`.
  const patchTurn = (sid: string, fn: (t: AssistantTurn) => AssistantTurn) =>
    setSessions((prev) =>
      prev.map((s) => {
        if (s.id !== sid) return s
        const turns = [...s.turns]
        const last = turns[turns.length - 1]
        if (last && last.role === 'assistant') turns[turns.length - 1] = fn(last)
        return { ...s, turns, updatedAt: Date.now() }
      }),
    )

  function applyEvent(sid: string, ev: AgentEvent) {
    if (ev.type === 'token') {
      patchTurn(sid, (t) => appendText(t, ev.text))
    } else if (ev.type === 'reasoning') {
      patchTurn(sid, (t) => appendThinking(t, ev.text))
    } else if (ev.type === 'step' && ev.phase === 'tool_call') {
      patchTurn(sid, (t) => ({ ...t, blocks: [...t.blocks, { kind: 'tool', tool: ev.tool, args: ev.args }] }))
    } else if (ev.type === 'step' && ev.phase === 'tool_result') {
      patchTurn(sid, (t) => fillToolResult(t, ev.tool, ev.summary))
    } else if (ev.type === 'done') {
      patchTurn(sid, (t) => ({
        ...t,
        usage: ev.usage,
        toolCount: t.blocks.filter((b) => b.kind === 'tool').length,
        pending: false,
      }))
    } else if (ev.type === 'error') {
      patchTurn(sid, (t) => ({ ...t, error: ev.message, pending: false }))
    }
    scrollToBottom()
  }

  async function send(text: string) {
    const q = text.trim()
    if (!q || busy) return
    setInput('')
    setBusy(true)
    const sid = active.id

    // History the model sees: prior turns, assistant collapsed to its text blocks.
    const history: ChatMessage[] = []
    for (const t of active.turns) {
      if (t.role === 'user') {
        history.push({ role: 'user', content: t.content })
      } else if (!t.error) {
        const txt = t.blocks
          .filter((b) => b.kind === 'text')
          .map((b) => (b.kind === 'text' ? b.text : ''))
          .join('')
          .trim()
        if (txt) history.push({ role: 'assistant', content: txt })
      }
    }
    history.push({ role: 'user', content: q })

    setSessions((prev) =>
      prev.map((s) => {
        if (s.id !== sid) return s
        const turns: ChatTurn[] = [
          ...s.turns,
          { role: 'user', content: q },
          { role: 'assistant', blocks: [], toolCount: 0, pending: true },
        ]
        return { ...s, turns, title: s.turns.length === 0 ? deriveTitle(turns) : s.title, updatedAt: Date.now() }
      }),
    )
    scrollToBottom()

    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      for await (const ev of streamAgentChat(history, { signal: ctrl.signal })) {
        applyEvent(sid, ev)
      }
    } catch (err) {
      if (!ctrl.signal.aborted) patchTurn(sid, (t) => ({ ...t, error: String(err), pending: false }))
    } finally {
      patchTurn(sid, (t) => ({ ...t, pending: false }))
      setBusy(false)
      abortRef.current = null
    }
  }

  function stop() {
    abortRef.current?.abort()
    setBusy(false)
  }

  function onNew() {
    const cur = sessions.find((s) => s.id === activeId)
    if (cur && cur.turns.length === 0) {
      setDrawerOpen(false)
      return
    }
    const blank = newSession()
    // Drop any other empty drafts so at most one blank chat exists.
    setSessions((prev) => [blank, ...prev.filter((s) => s.turns.length > 0)])
    setActiveId(blank.id)
    setDrawerOpen(false)
  }

  function onSelect(id: string) {
    setActiveId(id)
    // Drop any empty draft we're leaving behind (so the list isn't littered).
    setSessions((prev) => prev.filter((s) => s.id === id || s.turns.length > 0))
    setDrawerOpen(false)
  }

  function onDelete(id: string) {
    // The confirmation step lives in the sidebar's delete button (two-step:
    // first click arms, second click deletes) — no native confirm() dialog.
    const remaining = sessions.filter((s) => s.id !== id)
    const next = remaining.length > 0 ? remaining : [newSession()]
    setSessions(next)
    if (id === activeId) {
      const top = [...next].sort((a, b) => b.updatedAt - a.updatedAt)[0]
      setActiveId(top.id)
    }
  }

  const chatArea = (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '24px 20px' }}>
        {active.turns.length === 0 ? (
          <div style={{ paddingTop: 36, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, maxWidth: 560, margin: '0 auto' }}>
            <CatMascot size={84} float />
            <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)', marginTop: 6 }}>
              问问你的活动记录
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', maxWidth: 360, lineHeight: 1.6, textAlign: 'center', marginBottom: 6 }}>
              基于你电脑上记录的真实活动，我会查数据再回答——不编造。
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 480, width: '100%' }}>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="tt-nav-row"
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 820, margin: '0 auto', width: '100%' }}>
            {active.turns.map((t, i) => (
              <TurnView key={i} turn={t} />
            ))}
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
        style={{ display: 'flex', gap: 10, padding: '14px 20px', borderTop: '1px solid var(--bg-border)', background: 'var(--bg-surface)' }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="问问你最近在干嘛…"
          disabled={busy}
          aria-label="输入问题"
          className="tt-input"
          style={{
            flex: 1,
            padding: '12px 16px',
            background: 'var(--bg-base)',
            fontSize: 14,
          }}
        />
        {busy ? (
          <IconButton aria-label="停止" onClick={stop} style={{ width: 'auto', height: 'auto', padding: '0 18px' }}>
            <Square size={16} />
          </IconButton>
        ) : (
          <Button
            type="submit"
            variant="primary"
            aria-label="发送"
            disabled={!input.trim()}
            style={{ padding: '0 20px' }}
          >
            <Send size={16} />
          </Button>
        )}
      </form>
    </div>
  )

  const sidebar = (
    <AgentSidebar sessions={sessions} activeId={activeId} onNew={onNew} onSelect={onSelect} onDelete={onDelete} stats={stats} />
  )

  if (isMobile) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderBottom: '1px solid var(--bg-border)', background: 'var(--bg-surface)' }}>
          <button onClick={() => setDrawerOpen(true)} aria-label="对话列表" className="tt-nav-row" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 38, height: 38, borderRadius: 'var(--radius-md)', background: 'var(--bg-raised)', border: '1px solid var(--bg-border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
            <Menu size={18} />
          </button>
          <div style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {active.title || '新对话'}
          </div>
        </div>
        {chatArea}
        <Dialog.Root open={drawerOpen} onOpenChange={setDrawerOpen}>
          <Dialog.Portal>
            <Dialog.Overlay className="tt-overlay" style={{ position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 49 }} />
            <Dialog.Content
              className="tt-drawer-content"
              aria-label="对话列表"
              style={{ position: 'fixed', top: 0, bottom: 0, left: 0, width: 'min(82vw, 320px)', zIndex: 50, background: 'var(--bg-surface)', borderRight: '1px solid var(--bg-border)', outline: 'none' }}
            >
              <Dialog.Title className="sr-only">对话列表</Dialog.Title>
              {sidebar}
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
      <aside style={{ width: 248, flexShrink: 0, borderRight: '1px solid var(--bg-border)', background: 'var(--bg-surface)', minHeight: 0 }}>
        {sidebar}
      </aside>
      {chatArea}
    </div>
  )
}
