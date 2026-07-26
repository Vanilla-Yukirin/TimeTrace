import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { AppOverrides } from '@/types/api'

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

const inputStyle: React.CSSProperties = {
  padding: '9px 12px',
  background: 'var(--bg-raised)',
  border: '1px solid var(--bg-border)',
  borderRadius: 'var(--radius-md)',
  color: 'var(--text-primary)',
  fontSize: 13,
  outline: 'none',
}

/** Settings → 应用分类规则: a PaaS-env-var-style KV editor. Each row pins an app
 *  (matched as a case-insensitive substring of the window's app name) to a fixed
 *  category — which DETERMINISTICALLY overrides the AI's per-frame guess — and/or
 *  attaches a free-text note injected into the AI's prompts as background. */
export function AppOverridesSection() {
  const queryClient = useQueryClient()
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

  const toRows = (d: AppOverrides): Row[] =>
    Object.entries(d.apps).map(([app, v]) => ({
      rid: nextRid.current++,
      app,
      category: v.category ?? '',
      note: v.note ?? '',
    }))

  const saveMut = useMutation({
    mutationFn: (body: AppOverrides) => api.putAppOverrides(body),
    onSuccess: (saved) => {
      setRows(toRows(saved)) // resync to the validated/cleaned server copy
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

  return (
    <div>
      <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
        应用分类规则
      </h3>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16, lineHeight: 1.6 }}>
        给特定应用固定分类或补充说明。固定分类会<strong>确定性覆盖 AI 的判断</strong>（如把 Vanish
        始终归为「工作」）；说明会注入到 AI 的描述与看板提示里，作为该应用的背景。应用名按
        <strong>子串、不区分大小写</strong>匹配窗口的应用名。
      </div>

      {query.isLoading && (
        <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>加载中…</div>
      )}
      {query.error && (
        <div style={{ fontSize: 13, color: 'var(--error)' }}>
          加载失败：{(query.error as Error).message}
        </div>
      )}

      {!query.isLoading && !query.error && (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {visibleRows.length === 0 && (
              <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                还没有规则，点下面「添加」新增一条。
              </div>
            )}
            {visibleRows.map((r) => (
              <div key={r.rid} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input
                  value={r.app}
                  onChange={(e) => update(r.rid, { app: e.target.value })}
                  placeholder="应用名，如 Vanish"
                  aria-label="应用名"
                  autoComplete="off"
                  style={{ ...inputStyle, flex: '0 0 140px', minWidth: 0 }}
                />
                <select
                  value={r.category}
                  onChange={(e) => update(r.rid, { category: e.target.value })}
                  aria-label="固定分类"
                  style={{ ...inputStyle, flex: '0 0 120px', cursor: 'pointer' }}
                >
                  <option value="">不改分类</option>
                  {CATEGORY_OPTIONS.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <input
                  value={r.note}
                  onChange={(e) => update(r.rid, { note: e.target.value })}
                  placeholder="说明（可选），如：公司内部 IM，工作沟通用"
                  aria-label="说明"
                  autoComplete="off"
                  style={{ ...inputStyle, flex: 1, minWidth: 0 }}
                />
                <button
                  onClick={() => remove(r.rid)}
                  title="删除这条规则"
                  aria-label="删除"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    padding: '8px 10px',
                    background: 'transparent',
                    color: 'var(--error)',
                    border: '1px solid var(--bg-border)',
                    borderRadius: 'var(--radius-md)',
                    cursor: 'pointer',
                    flex: '0 0 auto',
                  }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button
              onClick={add}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '9px 16px',
                background: 'var(--bg-raised)',
                color: 'var(--text-primary)',
                border: '1px solid var(--bg-border)',
                borderRadius: 'var(--radius-md)',
                fontSize: 13,
                cursor: 'pointer',
              }}
            >
              <Plus size={14} />
              添加
            </button>
            <button
              onClick={save}
              disabled={saveMut.isPending}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '9px 20px',
                background: 'var(--grad-accent)',
                color: '#fff',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                fontSize: 13,
                fontWeight: 600,
                cursor: 'pointer',
                opacity: saveMut.isPending ? 0.6 : 1,
              }}
            >
              {saveMut.isPending ? '保存中…' : '保存'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
