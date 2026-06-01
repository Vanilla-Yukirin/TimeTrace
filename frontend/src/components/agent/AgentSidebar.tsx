import type { CSSProperties } from 'react'
import { BarChart3, MessageSquarePlus, Trash2 } from 'lucide-react'
import type { AgentSession, TokenStats } from '@/lib/agentSessions'

const fmt = (n: number) => n.toLocaleString('en-US')

function whenLabel(ms: number): string {
  const d = new Date(ms)
  const now = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return d.toDateString() === now.toDateString()
    ? `${p(d.getHours())}:${p(d.getMinutes())}`
    : `${d.getMonth() + 1}/${d.getDate()}`
}

const newBtn: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 7,
  margin: '12px 12px 8px',
  padding: '9px 14px',
  borderRadius: 'var(--radius-md)',
  border: 'none',
  background: 'var(--grad-accent)',
  color: '#fff',
  fontSize: 13.5,
  fontWeight: 600,
  cursor: 'pointer',
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
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <button onClick={onNew} style={newBtn}>
        <MessageSquarePlus size={16} aria-hidden="true" /> 新对话
      </button>
      <div style={{ flex: 1, overflowY: 'auto', padding: '2px 0', minHeight: 0 }}>
        {ordered.map((s) => {
          const active = s.id === activeId
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
              {questions > 0 && (
                <button
                  aria-label="删除对话"
                  title="删除对话"
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(s.id)
                  }}
                  style={delBtn}
                >
                  <Trash2 size={13} aria-hidden="true" />
                </button>
              )}
            </div>
          )
        })}
      </div>
      <TokenPanel stats={stats} />
    </div>
  )
}
