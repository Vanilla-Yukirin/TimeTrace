import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { AccountSection } from '@/components/admin/AccountSection'
import { TokenManager } from '@/components/admin/TokenManager'
import { EmbeddingDiagnostics } from '@/components/admin/EmbeddingDiagnostics'

export function SettingsPage() {
  const { data: info, isLoading, error } = useQuery({
    queryKey: queryKeys.runtimeInfo(),
    queryFn: () => api.getRuntimeInfo(),
  })

  // Account + Tokens render regardless of the runtime-info probe; only the
  // backend-status block below depends on it (so a slow /runtime-info doesn't
  // hide the auth controls).
  return (
    <div style={{ padding: '24px 28px 48px', maxWidth: 640, display: 'flex', flexDirection: 'column', gap: 30 }}>
      <AccountSection />

      <TokenManager />

      <EmbeddingDiagnostics />

      <div>
      <h3 style={{
        fontSize: 14,
        fontWeight: 600,
        color: 'var(--text-primary)',
        marginBottom: 16,
      }}>
        后端状态
      </h3>

      {isLoading && (
        <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>加载中...</div>
      )}
      {error && (
        <div style={{ fontSize: 13, color: 'var(--error)' }}>
          加载失败：{(error as Error).message}
        </div>
      )}
      {info && (
      <>


      {/* Version */}
      <div style={{
        padding: 16,
        background: 'var(--bg-surface)',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--bg-border)',
        marginBottom: 12,
      }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
          版本
        </div>
        <div style={{ fontSize: 14, color: 'var(--text-primary)', fontWeight: 600 }}>
          v{info?.version}
        </div>
      </div>

      {/* Data directory */}
      <div style={{
        padding: 16,
        background: 'var(--bg-surface)',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--bg-border)',
        marginBottom: 12,
      }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
          数据目录
        </div>
        <div style={{
          fontSize: 13,
          color: 'var(--text-secondary)',
          wordBreak: 'break-all',
          fontFamily: 'JetBrains Mono, monospace',
        }}>
          {info?.data_dir}
        </div>
      </div>

      {/* API endpoint */}
      <div style={{
        padding: 16,
        background: 'var(--bg-surface)',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--bg-border)',
        marginBottom: 12,
      }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
          API 地址
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          {info?.api_host}:{info?.api_port}
        </div>
      </div>
      </>
      )}
      </div>
    </div>
  )
}
