import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as Dialog from '@radix-ui/react-dialog'
import { Ban, Monitor, Pencil } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { AdminDevice } from '@/types/api'
import { Section } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorBanner } from '@/components/ui/Feedback'
import { Skeleton } from '@/components/ui/Skeleton'

function relativeTime(ms: number): string {
  const diff = Date.now() - ms
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 30 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`
  return new Date(ms).toLocaleDateString()
}

function labelOf(d: AdminDevice): string {
  return d.name || d.reported_name || `设备 ${d.id.slice(0, 8)}`
}

/** Settings → 设备: capture devices that have uploaded to this server. The
 *  name / description are what the timeline lanes, badges and the agent's
 *  prompts show ("办公电脑" / "家用电脑"). Revoking blocks the device UUID;
 *  its bearer token is revoked separately under API Tokens. */
export function DeviceManager() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState({ name: '', description: '' })
  const [revokeTarget, setRevokeTarget] = useState<AdminDevice | null>(null)

  const devicesQuery = useQuery({
    queryKey: queryKeys.adminDevices(),
    queryFn: api.listAdminDevices,
  })

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.adminDevices() })
    void queryClient.invalidateQueries({ queryKey: queryKeys.devices() })
  }

  const updateMut = useMutation({
    mutationFn: ({ id, name, description }: { id: string; name: string; description: string }) =>
      api.updateDevice(id, { name, description }),
    onSuccess: () => {
      setEditing(null)
      toast.success('已保存设备信息')
      refreshAll()
    },
    onError: (e) => toast.error('保存失败', { description: (e as Error).message }),
  })

  const revokeMut = useMutation({
    mutationFn: (id: string) => api.revokeDevice(id),
    onSuccess: () => {
      setRevokeTarget(null)
      toast.success('已停用设备')
      refreshAll()
    },
    onError: (e) => toast.error('停用失败', { description: (e as Error).message }),
  })

  const devices = devicesQuery.data ?? []

  return (
    <Section
      title="设备"
      desc="上传过数据的采集设备。名称与描述会显示在时间轴泳道、搜索结果和 Agent 的回答里。"
    >
      <div
        style={{
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-lg)',
          overflow: 'hidden',
          background: 'var(--bg-surface)',
        }}
      >
        {devicesQuery.isLoading && (
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Skeleton style={{ height: 40 }} />
            <Skeleton style={{ height: 40 }} />
          </div>
        )}
        {devicesQuery.isError && (
          <ErrorBanner
            title="设备列表加载失败"
            message={(devicesQuery.error as Error).message}
            onRetry={() => devicesQuery.refetch()}
            retrying={devicesQuery.isRefetching}
            style={{ margin: 12 }}
          />
        )}
        {!devicesQuery.isLoading && !devicesQuery.isError && devices.length === 0 && (
          <EmptyState title="还没有设备上传过数据" style={{ padding: '22px 16px' }} />
        )}
        {devices.map((d, i) => {
          const isEditing = editing === d.id
          const revoked = d.revoked_at != null
          return (
            <div
              key={d.id}
              style={{
                padding: '11px 14px',
                borderTop: i > 0 ? '1px solid var(--bg-border)' : 'none',
                opacity: revoked ? 0.6 : 1,
              }}
            >
              {isEditing ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    updateMut.mutate({ id: d.id, name: draft.name.trim(), description: draft.description.trim() })
                  }}
                  style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
                >
                  <input
                    className="tt-input"
                    aria-label="设备名称"
                    placeholder={d.reported_name || '如：办公电脑'}
                    maxLength={128}
                    value={draft.name}
                    onChange={(e) => setDraft((x) => ({ ...x, name: e.target.value }))}
                    autoFocus
                  />
                  <input
                    className="tt-input"
                    aria-label="设备描述"
                    placeholder="可选：这台设备通常用来做什么"
                    value={draft.description}
                    onChange={(e) => setDraft((x) => ({ ...x, description: e.target.value }))}
                  />
                  <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <Button type="button" onClick={() => setEditing(null)}>
                      取消
                    </Button>
                    <Button type="submit" variant="primary" loading={updateMut.isPending}>
                      保存
                    </Button>
                  </div>
                </form>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <Monitor size={16} aria-hidden="true" style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 600 }}>
                      {labelOf(d)}
                      {revoked && (
                        <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--danger, var(--warning))', fontWeight: 500 }}>
                          已停用
                        </span>
                      )}
                    </div>
                    {d.description && (
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{d.description}</div>
                    )}
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
                      {[
                        d.reported_name && d.reported_name !== d.name ? `上报名 ${d.reported_name}` : null,
                        d.client_version ? `客户端 ${d.client_version}` : null,
                        d.token_label ? `token ${d.token_label}` : null,
                        `最近上传 ${relativeTime(d.last_seen_at)}`,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                  </div>
                  <Button
                    onClick={() => {
                      setDraft({ name: d.name, description: d.description })
                      setEditing(d.id)
                    }}
                    title="编辑名称与描述"
                    style={{ padding: '5px 10px', fontSize: 12 }}
                  >
                    <Pencil size={12} />
                    编辑
                  </Button>
                  {!revoked && (
                    <Button
                      variant="danger"
                      onClick={() => setRevokeTarget(d)}
                      title="停用此设备"
                      style={{ padding: '5px 10px', fontSize: 12 }}
                    >
                      <Ban size={12} />
                      停用
                    </Button>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <Dialog.Root open={revokeTarget != null} onOpenChange={(open) => !open && setRevokeTarget(null)}>
        <Dialog.Portal>
          <Dialog.Overlay
            className="tt-overlay"
            style={{ position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 50 }}
          />
          <Dialog.Content
            className="tt-modal-content"
            style={{
              position: 'fixed',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              zIndex: 51,
              width: 'calc(100% - 48px)',
              maxWidth: 420,
              padding: 22,
              background: 'var(--bg-surface)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-xl)',
              boxShadow: 'var(--shadow-lg)',
              outline: 'none',
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <Dialog.Title style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
              停用设备「{revokeTarget ? labelOf(revokeTarget) : ''}」？
            </Dialog.Title>
            <Dialog.Description style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.6 }}>
              停用后该设备的上传会被拒绝，已上传的历史记录仍可查看。它使用的 token 不会被撤销，
              如需彻底收回上传权限，请在「API Tokens」里另行撤销。
            </Dialog.Description>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Dialog.Close asChild>
                <Button>取消</Button>
              </Dialog.Close>
              <Button
                variant="danger"
                loading={revokeMut.isPending}
                onClick={() => revokeTarget && revokeMut.mutate(revokeTarget.id)}
              >
                确认停用
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Section>
  )
}
