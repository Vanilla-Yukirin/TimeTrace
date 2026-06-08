import { memo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { AuditRow as AuditRowT } from '@/types/api'
import { formatTime } from '@/lib/dateUtils'
import { useRecord } from '@/hooks/useRecord'
import { CategoryBadge } from '@/components/detail/CategoryBadge'
import { ThumbnailView } from '@/components/detail/ThumbnailView'
import { StatusChip } from './StatusChip'

/** Shared grid template so every row + the column header line up like a table.
 *  Columns: chevron | status | time | app·title | category | upload-delay | flags */
export const AUDIT_COLS = '22px 92px 84px minmax(0, 1fr) 136px 88px 104px'

export const AUDIT_HEADERS = ['', '状态', '时间', '应用 / 标题', '分类', '上传延迟', '旗标'] as const

/** Format a latency in ms → short human string; null/undefined → em dash, and a
 *  (skew-induced) negative delay shows as ≈0 rather than a confusing "-3s". */
function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 0) return '≈0'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return s ? `${m}m ${s}s` : `${m}m`
}

function fullTime(ms: number): string {
  return new Date(ms).toLocaleString('zh-CN', { hour12: false })
}

const cellMono: React.CSSProperties = {
  fontFamily: 'var(--font-mono, ui-monospace, monospace)',
  fontSize: 12,
  color: 'var(--text-secondary)',
}

function AuditRowImpl({ row }: { row: AuditRowT }) {
  const [expanded, setExpanded] = useState(false)
  // Lazily fetch full detail (screenshots + full vlm_desc) only when opened —
  // collapsed rows never load images. Reuses the shared record cache.
  const detail = useRecord(expanded ? row.id : null)
  const suggestion = suggestionLabel(row)

  return (
    <div
      style={{
        borderBottom: '1px solid var(--bg-border)',
        background: expanded ? 'var(--bg-raised)' : 'transparent',
      }}
    >
      <div
        onClick={() => setExpanded((s) => !s)}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setExpanded((s) => !s)
          }
        }}
        style={{
          display: 'grid',
          gridTemplateColumns: AUDIT_COLS,
          alignItems: 'center',
          gap: 10,
          padding: '7px 12px',
          cursor: 'pointer',
        }}
      >
        <span style={{ color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>

        <StatusChip status={row.status} />

        <span style={cellMono}>{formatTime(row.ts_start)}</span>

        <span style={{ minWidth: 0 }}>
          <span
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: 'var(--text-primary)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              display: 'block',
            }}
          >
            {row.app_name || '—'}
          </span>
          <span
            style={{
              fontSize: 11.5,
              color: 'var(--text-muted)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              display: 'block',
            }}
          >
            {row.window_title || '(无标题)'}
          </span>
        </span>

        <span style={{ minWidth: 0 }}>
          {row.category_final ? (
            // confidence intentionally hidden: it's a rule-vs-VLM vote margin,
            // not a model probability (the trace in detail carries the breakdown).
            <CategoryBadge category={row.category_final} confidence={null} />
          ) : (
            <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>未分类</span>
          )}
        </span>

        <span style={{ ...cellMono, textAlign: 'right' }}>
          {row.single_process ? (
            <span style={{ color: 'var(--text-muted)' }} title="单进程：采集即入库，无上传延迟">
              N/A
            </span>
          ) : (
            fmtMs(row.ingest_delay_ms)
          )}
        </span>

        <FlagChips row={row} />
      </div>

      {expanded && (
        <div
          style={{
            padding: '4px 12px 14px 44px',
            background: 'var(--bg-base)',
            borderTop: '1px solid var(--bg-border)',
          }}
        >
          {detail.data?.screenshots && detail.data.screenshots.length > 0 ? (
            <div style={{ maxWidth: 420, margin: '10px 0' }}>
              <ThumbnailView
                screenshots={detail.data.screenshots}
                screenshotCount={row.screenshot_count}
              />
            </div>
          ) : detail.isLoading ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', margin: '10px 0' }}>
              加载详情…
            </div>
          ) : null}

          <KV
            label="描述"
            value={
              detail.data?.vlm_desc ||
              (detail.isLoading ? '加载中…' : row.desc_chars ? '（描述加载失败）' : '（无描述）')
            }
            muted={!detail.data?.vlm_desc}
          />

          <Section title="时间线">
            <KV label="活动时间" value={fullTime(row.ts_start)} mono />
            <KV label="收到时间" value={fullTime(row.created_at)} mono />
            <KV
              label="上传延迟"
              value={row.single_process ? 'N/A（单进程）' : fmtMs(row.ingest_delay_ms)}
            />
            <KV label="截图滞后" value={fmtMs(row.screenshot_lag_ms)} />
            <KV label="活动时长" value={fmtMs(row.activity_duration_ms)} />
            <KV label="队列等待" value={fmtMs(row.queue_wait_ms)} />
            <KV label="VLM 耗时" value={fmtMs(row.vlm_duration_ms)} />
            <KV label="总延迟" value={fmtMs(row.total_latency_ms)} hint="收到→分析完" />
            <KV label="端到端" value={fmtMs(row.end_to_end_ms)} hint="含上传·跨时钟" />
          </Section>

          <Section title="管线状态">
            <KV label="状态" value={row.status} />
            <KV label="analysis_status" value={row.analysis_status ?? '—'} mono />
            <KV label="record_status" value={row.record_status} mono />
            <KV label="需要 VLM" value={yesNo(row.needs_vlm)} />
            <KV label="需要分类" value={yesNo(row.needs_classification)} />
            <KV label="已完成" value={yesNo(row.completed)} />
            {row.retry_count > 0 && <KV label="重试次数" value={String(row.retry_count)} />}
            {row.next_retry_at != null && (
              <KV label="下次重试" value={fullTime(row.next_retry_at)} mono />
            )}
            {row.error_msg && <KV label="最后错误" value={row.error_msg} danger />}
          </Section>

          <Section title="分类 / 内容">
            <KV label="最终分类" value={row.category_final ?? '—'} />
            <KV label="VLM 建议" value={suggestion.text} muted={suggestion.muted} />
            <TraceKV trace={row.decision_trace} />
            <KV label="描述字数" value={row.desc_chars == null ? '—' : String(row.desc_chars)} />
            <KV label="VLM 模型" value={row.vlm_model ?? '—'} mono />
            <KV label="截图数" value={String(row.screenshot_count)} />
            {row.url && <KV label="URL" value={row.url} mono />}
          </Section>

          <Section title="标识">
            <KV label="记录 ID" value={row.id} mono />
            <KV label="客户端 ID" value={row.client_record_id ?? '（单进程无）'} mono />
            <KV label="事件类型" value={row.event_type} />
            {row.capture_reason && <KV label="采集原因" value={row.capture_reason} />}
          </Section>
        </div>
      )}
    </div>
  )
}

function yesNo(b: boolean): string {
  return b ? '是' : '否'
}

/** Label for the "VLM 建议" row: VLM's raw pick → final. Only attribute the
 *  change to a rule when the decision_trace actually shows a rule hit matching
 *  the final — a user/agent feedback edit changes category_final WITHOUT a rule
 *  (and leaves the trace untouched), so fall back to neutral "改为" rather than
 *  wrongly blaming a rule. */
function suggestionLabel(row: AuditRowT): { text: string; muted: boolean } {
  const sug = row.category_suggested
  if (sug == null) return { text: '—', muted: true }
  if (sug === row.category_final) return { text: sug, muted: false }
  let byRule = false
  if (row.decision_trace) {
    try {
      const t = JSON.parse(row.decision_trace) as TraceData
      byRule = Boolean(t.signals?.rule?.hit) && t.signals?.rule?.cat === row.category_final
    } catch {
      // malformed trace → neutral wording
    }
  }
  const verb = byRule ? '规则改成' : '改为'
  return { text: `${sug} → ${verb} ${row.category_final}`, muted: false }
}

interface TraceData {
  candidates?: { cat: string; score: number }[]
  signals?: {
    rule?: { hit?: boolean; cat?: string | null }
    vlm?: { cat?: string; conf?: number } | null
  }
}

/** Render the decide_category vote breakdown (decision_trace JSON) as one row:
 *  which source voted what + the candidate scores. Falls back gracefully. */
function TraceKV({ trace }: { trace: string | null }) {
  if (!trace) return <KV label="投票明细" value="—" muted />
  let parsed: TraceData
  try {
    parsed = JSON.parse(trace) as TraceData
  } catch {
    return <KV label="投票明细" value={trace} mono />
  }
  const sig = parsed.signals ?? {}
  const ruleTxt = sig.rule?.hit ? `规则命中→${sig.rule.cat}` : '规则未命中'
  const vlmTxt = sig.vlm?.cat ? `VLM→${sig.vlm.cat}` : 'VLM 无'
  const cands = (parsed.candidates ?? []).map((c) => `${c.cat} ${c.score}`).join(' · ')
  return (
    <div style={{ display: 'flex', gap: 8, fontSize: 12, lineHeight: 1.5 }}>
      <span style={{ color: 'var(--text-muted)', flexShrink: 0, minWidth: 76 }}>投票明细</span>
      <span style={{ flex: 1, minWidth: 0, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>
        {ruleTxt} · {vlmTxt}
        {cands && <span style={{ color: 'var(--text-muted)' }}>（候选 {cands}）</span>}
      </span>
    </div>
  )
}

function FlagChips({ row }: { row: AuditRowT }) {
  const chips: { key: string; label: string; color: string }[] = []
  if (row.retry_count > 0)
    chips.push({ key: 'retry', label: `↻${row.retry_count}`, color: 'var(--warning)' })
  if (row.error_msg) chips.push({ key: 'err', label: '错误', color: 'var(--error)' })
  if (row.needs_classification)
    chips.push({ key: 'cls', label: '待分类', color: 'var(--accent)' })
  if (row.needs_vlm && !row.error_msg && row.status !== 'processing')
    chips.push({ key: 'vlm', label: '待VLM', color: 'var(--accent)' })
  return (
    <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
      {chips.slice(0, 2).map((c) => (
        <span
          key={c.key}
          style={{
            padding: '1px 7px',
            fontSize: 10.5,
            fontWeight: 600,
            borderRadius: 'var(--radius-pill)',
            color: 'var(--text-primary)',
            background: `color-mix(in srgb, ${c.color} 14%, transparent)`,
            border: `1px solid color-mix(in srgb, ${c.color} 38%, transparent)`,
            whiteSpace: 'nowrap',
          }}
        >
          {c.label}
        </span>
      ))}
    </span>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.04em',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          marginBottom: 5,
        }}
      >
        {title}
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
          gap: '3px 18px',
        }}
      >
        {children}
      </div>
    </div>
  )
}

function KV({
  label,
  value,
  mono = false,
  muted = false,
  danger = false,
  hint,
}: {
  label: string
  value: string
  mono?: boolean
  muted?: boolean
  danger?: boolean
  hint?: string
}) {
  return (
    <div style={{ display: 'flex', gap: 8, fontSize: 12, lineHeight: 1.5 }}>
      <span style={{ color: 'var(--text-muted)', flexShrink: 0, minWidth: 76 }}>{label}</span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          wordBreak: 'break-word',
          color: danger ? 'var(--error)' : muted ? 'var(--text-muted)' : 'var(--text-secondary)',
          fontFamily: mono ? 'var(--font-mono, ui-monospace, monospace)' : undefined,
        }}
      >
        {value}
        {hint && (
          <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--text-muted)', opacity: 0.8 }}>
            · {hint}
          </span>
        )}
      </span>
    </div>
  )
}

/** memo: during the 4s poll the parent re-renders the whole list; only rows
 *  whose data actually changed should re-render (and an open row keeps its
 *  local expanded state across polls regardless). */
export const AuditRowItem = memo(AuditRowImpl)
