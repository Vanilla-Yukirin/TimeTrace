import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'

export function SettingsPage() {
  const { data: info, isLoading, error } = useQuery({
    queryKey: queryKeys.runtimeInfo(),
    queryFn: () => api.getRuntimeInfo(),
  })

  if (isLoading) {
    return (
      <div style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--text-muted)',
      }}>
        加载中...
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: 24, color: 'var(--text-secondary)' }}>
        <div style={{ color: '#ef4444', marginBottom: 16 }}>
          加载失败：{(error as Error).message}
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: 24, maxWidth: 600 }}>
      <h2 style={{
        fontSize: 16,
        fontWeight: 600,
        color: 'var(--text-primary)',
        marginBottom: 20,
      }}>
        后端状态
      </h2>

      {/* Version */}
      <div style={{
        padding: 16,
        background: 'var(--bg-surface)',
        borderRadius: 8,
        border: '1px solid var(--bg-border)',
        marginBottom: 16,
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
        borderRadius: 8,
        border: '1px solid var(--bg-border)',
        marginBottom: 16,
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
        borderRadius: 8,
        border: '1px solid var(--bg-border)',
        marginBottom: 16,
      }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
          API 地址
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          {info?.api_host}:{info?.api_port}
        </div>
      </div>

      {/* Placeholder for Phase 1.5 */}
      <div style={{
        padding: 16,
        background: 'var(--bg-raised)',
        borderRadius: 8,
        border: '1px solid var(--bg-border)',
        marginTop: 24,
      }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
          ℹ 提示
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          Phase 1.5 将支持在此页编辑配置
        </div>
      </div>
    </div>
  )
}
