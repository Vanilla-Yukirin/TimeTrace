import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { useIsMobile } from '@/hooks/useIsMobile'
import { AccountSection } from '@/components/admin/AccountSection'
import { TokenManager } from '@/components/admin/TokenManager'
import { EmbeddingDiagnostics } from '@/components/admin/EmbeddingDiagnostics'
import { AppOverridesSection } from '@/components/admin/AppOverridesSection'
import { Card, Section } from '@/components/ui/Card'
import { ErrorBanner } from '@/components/ui/Feedback'
import { Skeleton } from '@/components/ui/Skeleton'

export function SettingsPage() {
  const isMobile = useIsMobile()
  const { data: info, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: queryKeys.runtimeInfo(),
    queryFn: () => api.getRuntimeInfo(),
  })

  // Account + Tokens render regardless of the runtime-info probe; only the
  // backend-status block below depends on it (so a slow /runtime-info doesn't
  // hide the auth controls).
  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: isMobile ? '18px 14px 48px' : '24px 28px 48px', maxWidth: 640, width: '100%', display: 'flex', flexDirection: 'column', gap: 30 }}>
      <AccountSection />

      <TokenManager />

      <EmbeddingDiagnostics />

      <AppOverridesSection />

      <Section title="后端状态">
        {isLoading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Skeleton style={{ height: 62 }} />
            <Skeleton style={{ height: 62 }} />
            <Skeleton style={{ height: 62 }} />
          </div>
        )}
        {error && (
          <ErrorBanner
            title="加载失败"
            message={(error as Error).message}
            onRetry={() => refetch()}
            retrying={isRefetching}
          />
        )}
        {info && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <InfoRow label="版本" value={`v${info.version}`} strong />
            <InfoRow label="数据目录" value={info.data_dir} mono />
            <InfoRow label="API 地址" value={`${info.api_host}:${info.api_port}`} />
          </div>
        )}
      </Section>
    </div>
  )
}

/** A labelled value card used by the backend-status block. */
function InfoRow({
  label,
  value,
  mono = false,
  strong = false,
}: {
  label: string
  value: string
  mono?: boolean
  strong?: boolean
}) {
  return (
    <Card style={{ padding: 16 }}>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      <div
        className={mono ? 'font-mono' : undefined}
        style={{
          fontSize: strong ? 14 : 13,
          fontWeight: strong ? 600 : 400,
          color: strong ? 'var(--text-primary)' : 'var(--text-secondary)',
          wordBreak: mono ? 'break-all' : undefined,
        }}
      >
        {value}
      </div>
    </Card>
  )
}
