import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { CatMascot } from '@/components/brand/CatMascot'
import type { LlmRequest } from '@/types/api'

// caller id → 中文标签 + 色。caller 是后端 llm_log 写死的几种来源。
const CALLERS: Record<string, { label: string; color: string }> = {
  worker_vlm: { label: '图片分析', color: '#3b82f6' },
  narrate: { label: '叙述', color: '#a855f7' },
  ask_agent: { label: '问问', color: '#10b981' },
  report: { label: '报告', color: '#f59e0b' },
}
const FILTERS: { id: string | null; label: string }[] = [
  { id: null, label: '全部' },
  { id: 'worker_vlm', label: '图片分析' },
  { id: 'narrate', label: '叙述' },
  { id: 'ask_agent', label: '问问' },
]

const COLS = '92px 78px 1fr 76px 132px 64px 70px'
const HEADERS = ['时间', '来源', '模型', '耗时', '输入/输出/思考 tok', '内容字', '状态']

function fmtTime(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function fmtDur(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
function fmtNum(n: number | null): string {
  if (n == null) return '·'
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export function LlmLogPage() {
  const [caller, setCaller] = useState<string | null>(null)
  const q = useQuery({
    queryKey: queryKeys.llmRequests(caller, 100),
    queryFn: () => api.getLlmRequests({ caller: caller ?? undefined, limit: 100 }),
    refetchInterval: 4000,
    refetchIntervalInBackground: false,
    staleTime: 2000,
    placeholderData: keepPreviousData,
    retry: 1,
  })
  const stats = useQuery({
    queryKey: queryKeys.llmStats(),
    queryFn: () => api.getLlmStats(),
    refetchInterval: 8000,
    refetchIntervalInBackground: false,
  })
  const items = q.data?.items ?? []
  const polling = q.isFetching && !q.isLoading
  const s = stats.data

  return (
    <div style={{ flex: 1, overflowY: 'auto', width: '100%' }}>
      <div style={{ maxWidth: 1120, margin: '0 auto', padding: '20px 18px 40px', width: '100%' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>
              LLM 请求日志
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 600, color: polling ? 'var(--success)' : 'var(--text-muted)' }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: polling ? 'var(--success)' : 'var(--text-muted)' }} />
                {polling ? '刷新中' : '实时'}
              </span>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 4, maxWidth: 560, lineHeight: 1.6 }}>
              每一次 LLM 调用的原始账本：何时发起、耗时多久、用了多少 token（输入 / 输出 / 思考，取自模型返回的真实
              usage）、内容多大。worker 图片分析、叙述、问问都在这里。每 4 秒刷新。
            </div>
          </div>
          <button onClick={() => q.refetch()} title="立即刷新" style={iconBtn}>
            <RefreshCw size={14} className={polling ? 'tt-spin' : undefined} />
          </button>
        </div>

        {/* Stats cards */}
        {s && (
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
            <StatCard label="总调用" value={String(s.n)} />
            <StatCard label="失败" value={String(s.n_error)} accent={s.n_error > 0 ? 'var(--error)' : undefined} />
            <StatCard label="输入 token" value={fmtNum(s.prompt_tokens)} />
            <StatCard label="输出 token" value={fmtNum(s.completion_tokens)} />
            <StatCard label="思考 token" value={fmtNum(s.reasoning_tokens)} />
            <StatCard label="平均耗时" value={s.avg_duration_ms != null ? fmtDur(Math.round(s.avg_duration_ms)) : '·'} />
          </div>
        )}

        {/* Filter chips */}
        <div style={{ display: 'flex', gap: 6, marginTop: 14, flexWrap: 'wrap' }}>
          {FILTERS.map((f) => (
            <button key={f.label} onClick={() => setCaller(f.id)} style={chip(caller === f.id)}>
              {f.label}
            </button>
          ))}
        </div>

        {q.isError && (
          <div style={{ padding: '12px 16px', borderRadius: 'var(--radius-md)', background: 'var(--error-bg)', border: '1px solid var(--error)', color: 'var(--error)', fontSize: 13, margin: '14px 0' }}>
            加载失败：{String((q.error as Error)?.message ?? q.error)}
          </div>
        )}

        {/* Table */}
        <div style={{ marginTop: 14, overflowX: 'auto' }}>
          <div style={{ minWidth: 720, border: '1px solid var(--bg-border)', borderRadius: 'var(--radius-lg)', background: 'var(--bg-surface)', overflow: 'hidden' }}>
            <div style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center', gap: 10, padding: '8px 12px', position: 'sticky', top: 0, zIndex: 1, background: 'var(--bg-raised)', borderBottom: '1px solid var(--bg-border)', fontSize: 11, fontWeight: 700, letterSpacing: '0.03em', color: 'var(--text-muted)' }}>
              {HEADERS.map((h, i) => (
                <span key={i} style={{ whiteSpace: 'nowrap' }}>{h}</span>
              ))}
            </div>
            {q.isLoading ? (
              <div style={{ padding: 36, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>加载中…</div>
            ) : items.length === 0 ? (
              <div style={{ padding: 36, textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
                <CatMascot size={72} float />
                <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>还没有 LLM 调用</div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>采集 / 叙述跑起来后，这里会一条条出现。</div>
              </div>
            ) : (
              items.map((r) => <Row key={r.id} r={r} />)
            )}
          </div>
        </div>
        {!q.isLoading && items.length > 0 && (
          <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--text-muted)', textAlign: 'center' }}>
            显示最近 {items.length} 条 · 按发起时间倒序
          </div>
        )}
      </div>
    </div>
  )
}

function Row({ r }: { r: LlmRequest }) {
  const c = CALLERS[r.caller] ?? { label: r.caller, color: 'var(--text-muted)' }
  const isErr = r.status === 'error'
  return (
    <div style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center', gap: 10, padding: '9px 12px', borderBottom: '1px solid var(--bg-border)', fontSize: 12.5 }}>
      <span style={{ color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{fmtTime(r.ts_start)}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: 600, color: c.color }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: c.color }} />
        {c.label}
      </span>
      <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.model ?? ''}>{r.model ?? '·'}</span>
      <span style={{ color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{fmtDur(r.duration_ms)}</span>
      <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--text-secondary)' }}>
        {fmtNum(r.prompt_tokens)} <span style={{ color: 'var(--text-muted)' }}>/</span> {fmtNum(r.completion_tokens)}{' '}
        <span style={{ color: 'var(--text-muted)' }}>/</span> <span style={{ color: '#a855f7' }}>{fmtNum(r.reasoning_tokens)}</span>
      </span>
      <span style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }} title={`输入 ${r.prompt_chars ?? '?'} 字 / 输出 ${r.completion_chars ?? '?'} 字`}>
        {fmtNum(r.prompt_chars)}/{fmtNum(r.completion_chars)}
      </span>
      <span style={{ fontSize: 11, fontWeight: 600, color: isErr ? 'var(--error)' : 'var(--success)' }} title={r.error ?? ''}>
        {isErr ? '失败' : 'ok'}
      </span>
    </div>
  )
}

function StatCard({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ padding: '10px 14px', border: '1px solid var(--bg-border)', borderRadius: 'var(--radius-md)', background: 'var(--bg-surface)', minWidth: 92 }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: accent ?? 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )
}

const iconBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', padding: 7, borderRadius: 'var(--radius-md)',
  border: '1px solid var(--bg-border)', background: 'var(--bg-surface)', color: 'var(--text-secondary)', cursor: 'pointer',
}
function chip(active: boolean): React.CSSProperties {
  return {
    padding: '5px 12px', borderRadius: 'var(--radius-pill)', border: '1px solid var(--bg-border)',
    background: active ? 'var(--accent-subtle)' : 'transparent', color: active ? 'var(--accent)' : 'var(--text-secondary)',
    fontWeight: active ? 600 : 500, fontSize: 12, cursor: 'pointer',
  }
}
