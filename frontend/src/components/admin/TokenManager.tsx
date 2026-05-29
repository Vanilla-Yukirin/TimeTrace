import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { TokenCreated } from '@/types/api'
import { TokenCreatedDialog } from './TokenCreatedDialog'

/** Settings → API Tokens section: list / create / revoke bearer tokens for
 *  MCP clients and capture clients. */
export function TokenManager() {
  const queryClient = useQueryClient()
  const [newLabel, setNewLabel] = useState('')
  const [created, setCreated] = useState<TokenCreated | null>(null)

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
  })

  const revokeMut = useMutation({
    mutationFn: (label: string) => api.revokeToken(label),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.adminTokens() })
    },
  })

  const tokens = tokensQuery.data ?? []

  return (
    <div>
      <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
        API Tokens
      </h3>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16 }}>
        给 MCP 客户端（Claude Code）与采集端用的 Bearer token。浏览器登录不需要。
      </div>

      {/* Create */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          placeholder="标签，如 claude-code"
          style={{
            flex: 1,
            padding: '8px 12px',
            background: 'var(--bg-raised)',
            border: '1px solid var(--bg-border)',
            borderRadius: 6,
            color: 'var(--text-primary)',
            fontSize: 13,
            outline: 'none',
          }}
        />
        <button
          onClick={() => newLabel && createMut.mutate(newLabel)}
          disabled={!newLabel || createMut.isPending}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '8px 14px',
            background: '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            fontSize: 13,
            cursor: newLabel ? 'pointer' : 'default',
            opacity: !newLabel || createMut.isPending ? 0.6 : 1,
            whiteSpace: 'nowrap',
          }}
        >
          <Plus size={14} />
          新建
        </button>
      </div>

      {createMut.isError && (
        <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 12 }}>
          {(createMut.error as Error).message}
        </div>
      )}

      {/* List */}
      <div
        style={{
          border: '1px solid var(--bg-border)',
          borderRadius: 8,
          overflow: 'hidden',
        }}
      >
        {tokensQuery.isLoading && (
          <div style={{ padding: 16, fontSize: 13, color: 'var(--text-muted)' }}>加载中...</div>
        )}
        {!tokensQuery.isLoading && tokens.length === 0 && (
          <div style={{ padding: 16, fontSize: 13, color: 'var(--text-muted)' }}>
            还没有 token
          </div>
        )}
        {tokens.map((t, i) => (
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
            <button
              onClick={() => {
                if (confirm(`撤销 token「${t.label}」？使用它的客户端会立即失效。`)) {
                  revokeMut.mutate(t.label)
                }
              }}
              title="撤销"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '4px 8px',
                background: 'transparent',
                color: '#ef4444',
                border: '1px solid var(--bg-border)',
                borderRadius: 4,
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              <Trash2 size={12} />
              撤销
            </button>
          </div>
        ))}
      </div>

      {created && <TokenCreatedDialog token={created} onClose={() => setCreated(null)} />}
    </div>
  )
}
