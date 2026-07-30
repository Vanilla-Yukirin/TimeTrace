import { useEffect, useState, type CSSProperties } from 'react'
import { BarChart3, Check, MessageSquarePlus, Trash2 } from 'lucide-react'
import type { AgentSession, TokenStats } from '@/lib/agentSessions'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Feedback'

const fmt = (n: number) => n.toLocaleString('en-US')

function whenLabel(ms: number): string {
  const d = new Date(ms)
  const now = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return d.toDateString() === now.toDateString()
    ? `${p(d.getHours())}:${p(d.getMinutes())}`
    : `${d.getMonth() + 1}/${d.getDate()}`
}

const rowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  margin: '0 8px',
  padding: '8px 10px',
  borderRadius: 'var(--radius-md)',
  cursor: 'pointer',
}

const delBtn: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 24,
  height: 24,
  flexShrink: 0,
  borderRadius: 'var(--radius-sm)',
  background: 'transparent',
  border: 'none',
  color: 'var(--text-muted)',
  cursor: 'pointer',
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span>{label}</span>
      <span style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}

function TokenPanel({ stats }: { stats: TokenStats }) {
  return (
    <div style={{ borderTop: '1px solid var(--bg-border)', padding: '12px 14px 12px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '.04em',
          textTransform: 'uppercase',
          color: 'var(--text-muted)',
          marginBottom: 8,
        }}
      >
        <BarChart3 size={13} aria-hidden="true" /> Token 消耗
      </div>
      <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--text-primary)', lineHeight: 1.1 }}>
        {fmt(stats.totalTokens)}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>累计 tokens（Agent 对话）</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, fontSize: 12, color: 'var(--text-secondary)' }}>
        <Stat label="输入" value={fmt(stats.inputTokens)} />
        <Stat label="输出" value={fmt(stats.outputTokens)} />
        <Stat label="工具调用" value={`${stats.toolCalls} 次`} />
        <Stat label="对话轮次" value={`${stats.messages} 轮`} />
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.5 }}>
        仅本机 Agent 对话累计；VLM / 全量用量统计待接入后端。
      </div>
    </div>
  )
}

export function AgentSidebar({
  sessions,
  activeId,
  onNew,
  onSelect,
  onDelete,
  stats,
}: {
  sessions: AgentSession[]
  activeId: string
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  stats: TokenStats
}) {
  const ordered = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)
  // Two-step delete: first click arms the button (turns into a confirm tick),
  // second click actually deletes. Arms auto-release after a few seconds.
  const [armedId, setArmedId] = useState<string | null>(null)
  useEffect(() => {
    if (armedId == null) return undefined
    const id = setTimeout(() => setArmedId(null), 3000)
    return () => clearTimeout(id)
  }, [armedId])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Button variant="primary" onClick={onNew} style={{ margin: '12px 12px 8px', padding: '9px 14px' }}>
        <MessageSquarePlus size={16} aria-hidden="true" /> 新对话
      </Button>
      <div style={{ flex: 1, overflowY: 'auto', padding: '2px 0', minHeight: 0 }}>
        {ordered.length === 0 ? (
          <EmptyState title="还没有对话" desc="点上方「新对话」开始。" style={{ padding: '28px 12px' }} />
        ) : (
          ordered.map((s) => {
            const active = s.id === activeId
            const armed = s.id === armedId
            const questions = s.turns.filter((t) => t.role === 'user').length
            return (
              <div
                key={s.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(s.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onSelect(s.id)
                  }
                }}
                aria-current={active ? 'true' : undefined}
                className="tt-nav-row"
                data-active={active || undefined}
                style={{ ...rowStyle, background: active ? 'var(--accent-subtle)' : 'transparent' }}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    style={{
                      fontSize: 13,
                      color: active ? 'var(--accent)' : 'var(--text-primary)',
                      fontWeight: active ? 600 : 500,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {s.title || '新对话'}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {questions > 0 ? `${whenLabel(s.updatedAt)} · ${questions} 问` : '空对话'}
                  </div>
                </div>
                {/* Always rendered — previously empty sessions had no delete
                    button and could never be removed. */}
                <button
                  aria-label={armed ? '确认删除对话' : '删除对话'}
                  title={armed ? '再点一次确认删除' : '删除对话'}
                  onClick={(e) => {
                    e.stopPropagation()
                    if (armed) {
                      setArmedId(null)
                      onDelete(s.id)
                    } else {
                      setArmedId(s.id)
                    }
                  }}
                  className="tt-nav-row"
                  style={{ ...delBtn, color: armed ? 'var(--error)' : 'var(--text-muted)' }}
                >
                  {armed ? <Check size={13} aria-hidden="true" /> : <Trash2 size={13} aria-hidden="true" />}
                </button>
              </div>
            )
          })
        )}
      </div>
      <TokenPanel stats={stats} />
    </div>
  )
}
