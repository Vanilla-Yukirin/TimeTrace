import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { TokenCreated } from '@/types/api'
import { Section } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Feedback'
import { Skeleton } from '@/components/ui/Skeleton'
import { TokenCreatedDialog } from './TokenCreatedDialog'

/** Settings → API Tokens section: list / create / revoke bearer tokens for
 *  MCP clients and capture clients. */
export function TokenManager() {
  const queryClient = useQueryClient()
  const [newLabel, setNewLabel] = useState('')
  const [created, setCreated] = useState<TokenCreated | null>(null)
  // Two-step revoke: first click arms the row's button, second click revokes.
  const [armedLabel, setArmedLabel] = useState<string | null>(null)

  const tokensQuery = useQuery({
    queryKey: queryKeys.adminTokens(),
    queryFn: api.listTokens,
  })

  const createMut = useMutation({
    mutationFn: (label: string) => api.createToken(label),
    onSuccess: (token) => {
      setCreated(token)
      setNewLabel('')
      void queryClient.invalidateQueries({ queryKey: queryKeys.adminTokens() })
    },
    onError: (e) => toast.error('创建失败', { description: (e as Error).message }),
  })

  const revokeMut = useMutation({
    mutationFn: (label: string) => api.revokeToken(label),
    onSuccess: (_data, label) => {
      toast.success('已撤销 token', { description: label })
      void queryClient.invalidateQueries({ queryKey: queryKeys.adminTokens() })
    },
    onError: (e) => toast.error('撤销失败', { description: (e as Error).message }),
  })

  const tokens = tokensQuery.data ?? []

  return (
    <Section
      title="API Tokens"
      desc="给 MCP 客户端（Claude Code）与采集端用的 Bearer token。浏览器登录不需要。"
    >
      {/* Create */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          aria-label="新 token 的标签"
          name="tt-token-label"
          autoComplete="off"
          placeholder="标签，如 claude-code"
          className="tt-input"
          style={{ flex: 1 }}
        />
        <Button
          variant="primary"
          onClick={() => newLabel && createMut.mutate(newLabel)}
          disabled={!newLabel}
          loading={createMut.isPending}
          style={{ whiteSpace: 'nowrap' }}
        >
          {!createMut.isPending && <Plus size={14} />}
          新建
        </Button>
      </div>

      {/* List */}
      <div
        style={{
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-lg)',
          overflow: 'hidden',
          background: 'var(--bg-surface)',
        }}
      >
        {tokensQuery.isLoading && (
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Skeleton style={{ height: 34 }} />
            <Skeleton style={{ height: 34 }} />
          </div>
        )}
        {!tokensQuery.isLoading && tokens.length === 0 && (
          <EmptyState title="还没有 token" style={{ padding: '22px 16px' }} />
        )}
        {tokens.map((t, i) => {
          const armed = armedLabel === t.label
          return (
            <div
              key={t.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 14px',
                borderTop: i > 0 ? '1px solid var(--bg-border)' : 'none',
              }}
            >
              <div>
                <div style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 500 }}>
                  {t.label}
                </div>
                {t.created_at && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    {new Date(t.created_at).toLocaleString()}
                  </div>
                )}
              </div>
              <Button
                variant="danger"
                onClick={() => {
                  if (armed) {
                    setArmedLabel(null)
                    revokeMut.mutate(t.label)
                  } else {
                    setArmedLabel(t.label)
                  }
                }}
                onBlur={() => setArmedLabel(null)}
                loading={revokeMut.isPending && revokeMut.variables === t.label}
                title={armed ? '再点一次确认撤销' : '撤销'}
                style={{ padding: '5px 10px', fontSize: 12 }}
              >
                <Trash2 size={12} />
                {armed ? '确认撤销' : '撤销'}
              </Button>
            </div>
          )
        })}
      </div>

      {created && <TokenCreatedDialog token={created} onClose={() => setCreated(null)} />}
    </Section>
  )
}
