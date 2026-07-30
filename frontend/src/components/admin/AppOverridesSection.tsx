import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { AppOverrides } from '@/types/api'
import { useIsMobile } from '@/hooks/useIsMobile'
import { Section } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorBanner } from '@/components/ui/Feedback'
import { Skeleton } from '@/components/ui/Skeleton'

/** The flat-6 taxonomy, hardcoded on purpose: the box's `categories` table may
 *  still carry the legacy two-level rows (work/coding, …), so we do NOT read
 *  `/v1/categories` for the dropdown — only these six are valid override
 *  targets (mirrors db/sqlite.py _BUILTIN_CATEGORIES). */
const CATEGORY_OPTIONS = [
  { id: 'work', name: '工作' },
  { id: 'study', name: '学习' },
  { id: 'social', name: '沟通' },
  { id: 'entertainment', name: '娱乐' },
  { id: 'system', name: '系统' },
  { id: 'uncategorized', name: '未分类' },
] as const

interface Row {
  rid: number
  app: string
  category: string // '' = don't force a category
  note: string
}

/** Settings → 应用分类规则: a PaaS-env-var-style KV editor. Each row pins an app
 *  (matched as a case-insensitive substring of the window's app name) to a fixed
 *  category — which DETERMINISTICALLY overrides the AI's per-frame guess — and/or
 *  attaches a free-text note injected into the AI's prompts as background. */
export function AppOverridesSection() {
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  // null means the user has not edited yet, so the latest server snapshot can
  // be shown directly. The first edit materializes an independent local draft.
  const [rows, setRows] = useState<Row[] | null>(null)
  const nextRid = useRef(0)

  const query = useQuery({
    queryKey: queryKeys.appOverrides(),
    queryFn: api.getAppOverrides,
  })

  const serverRows = useMemo<Row[]>(
    () =>
      Object.entries(query.data?.apps ?? {}).map(([app, v], index) => ({
        rid: -(index + 1),
        app,
        category: v.category ?? '',
        note: v.note ?? '',
      })),
    [query.data],
  )
  const visibleRows = rows ?? serverRows

  const saveMut = useMutation({
    mutationFn: (body: AppOverrides) => api.putAppOverrides(body),
    onSuccess: (saved) => {
      // Publish the validated server copy synchronously, then leave draft mode
      // so the "未保存" marker reflects reality after a successful save.
      queryClient.setQueryData(queryKeys.appOverrides(), saved)
      setRows(null)
      toast.success('已保存应用分类规则')
      void queryClient.invalidateQueries({ queryKey: queryKeys.appOverrides() })
    },
    onError: (e) => toast.error('保存失败', { description: (e as Error).message }),
  })

  const update = (rid: number, patch: Partial<Row>) =>
    setRows((rs) => (rs ?? serverRows).map((r) => (r.rid === rid ? { ...r, ...patch } : r)))
  const remove = (rid: number) =>
    setRows((rs) => (rs ?? serverRows).filter((r) => r.rid !== rid))
  const add = () =>
    setRows((rs) => [
      ...(rs ?? serverRows),
      { rid: nextRid.current++, app: '', category: '', note: '' },
    ])

  const save = () => {
    const apps: AppOverrides['apps'] = {}
    for (const r of visibleRows) {
      const key = r.app.trim()
      if (!key) continue
      apps[key] = { category: r.category || null, note: r.note.trim() }
    }
    saveMut.mutate({ version: 1, apps })
  }

  const dirty = rows !== null

  return (
    <Section
      title={
        <>
          应用分类规则
          {dirty && (
            <span
              style={{
                marginLeft: 8,
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--warning)',
                background: 'color-mix(in srgb, var(--warning) 12%, transparent)',
                padding: '2px 8px',
                borderRadius: 'var(--radius-pill)',
              }}
            >
              未保存
            </span>
          )}
        </>
      }
      desc={
        <>
          给特定应用固定分类或补充说明。固定分类会<strong>确定性覆盖 AI 的判断</strong>（如把 Vanish
          始终归为「工作」）；说明会注入到 AI 的描述与看板提示里，作为该应用的背景。应用名按
          <strong>子串、不区分大小写</strong>匹配窗口的应用名。
        </>
      }
    >
      {query.isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Skeleton style={{ height: 40 }} />
          <Skeleton style={{ height: 40 }} />
        </div>
      )}
      {query.error && (
        <ErrorBanner
          title="加载失败"
          message={(query.error as Error).message}
          onRetry={() => query.refetch()}
          retrying={query.isRefetching}
        />
      )}

      {!query.isLoading && !query.error && (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {visibleRows.length === 0 && (
              <EmptyState title="还没有规则" desc="点下面「添加」新增一条。" style={{ padding: '18px 12px' }} />
            )}
            {visibleRows.map((r) => (
              <div
                key={r.rid}
                style={{
                  display: 'flex',
                  gap: 8,
                  alignItems: isMobile ? 'stretch' : 'center',
                  flexDirection: isMobile ? 'column' : 'row',
                }}
              >
                <input
                  value={r.app}
                  onChange={(e) => update(r.rid, { app: e.target.value })}
                  placeholder="应用名，如 Vanish"
                  aria-label="应用名"
                  autoComplete="off"
                  className="tt-input"
                  style={isMobile ? undefined : { flex: '0 0 140px', minWidth: 0 }}
                />
                <select
                  value={r.category}
                  onChange={(e) => update(r.rid, { category: e.target.value })}
                  aria-label="固定分类"
                  className="tt-input"
                  style={{ ...(isMobile ? {} : { flex: '0 0 120px' }), cursor: 'pointer' }}
                >
                  <option value="">不改分类</option>
                  {CATEGORY_OPTIONS.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 0 }}>
                  <input
                    value={r.note}
                    onChange={(e) => update(r.rid, { note: e.target.value })}
                    placeholder="说明（可选），如：公司内部 IM，工作沟通用"
                    aria-label="说明"
                    autoComplete="off"
                    className="tt-input"
                    style={{ flex: 1, minWidth: 0 }}
                  />
                  <Button
                    variant="danger"
                    onClick={() => remove(r.rid)}
                    title="删除这条规则"
                    aria-label="删除"
                    style={{ width: 'auto', height: 'auto', padding: '8px 10px', flex: '0 0 auto' }}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <Button onClick={add}>
              <Plus size={14} />
              添加
            </Button>
            <Button variant="primary" onClick={save} loading={saveMut.isPending}>
              {saveMut.isPending ? '保存中…' : '保存'}
            </Button>
          </div>
        </>
      )}
    </Section>
  )
}
