import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Activity, Cpu } from 'lucide-react'
import {
  getEmbKey,
  setEmbKey,
  loadEmbModel,
  runEmbSelftest,
  type SelftestResult,
} from '@/api/embedding'
import { Card, Section } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Chip } from '@/components/ui/Chip'

const DTYPES = ['bfloat16', 'float16', 'int8', 'int4'] as const

function cosColor(c: number): string {
  if (c >= 0.999) return 'var(--success)'
  if (c >= 0.99) return 'var(--warning)'
  return 'var(--error)'
}

export function EmbeddingDiagnostics() {
  const [key, setKeyState] = useState(getEmbKey())
  const [dtype, setDtype] = useState<string>('bfloat16')
  const [stage, setStage] = useState<string | null>(null)
  const [result, setResult] = useState<SelftestResult | null>(null)

  const detect = useMutation({
    mutationFn: async () => {
      if (!key.trim()) throw new Error('请先填写嵌入服务 API Key（tt_emb_…）')
      // Load the chosen precision first so the self-test measures THIS dtype
      // (the daemon embeds at whatever precision is currently resident).
      setStage(`加载 ${dtype} 精度…`)
      await loadEmbModel(dtype)
      setStage('运行自检…')
      return runEmbSelftest()
    },
    onSuccess: (r) => {
      setResult(r)
      setStage(null)
    },
    onSettled: () => setStage(null),
  })

  function saveKey(v: string) {
    setKeyState(v)
    setEmbKey(v)
  }

  const errMsg =
    detect.error instanceof Error
      ? detect.error.message
      : detect.isError
        ? '检测失败（嵌入服务未启动？端口 8766）'
        : null

  return (
    <Section
      title="嵌入模型自检"
      desc="对本地 Qwen3-VL 嵌入服务（端口 8766）跑官方标准输入，与满精度金标准比对，显示各向量余弦相似度与量化漂移。切换精度前可用于部署校验。"
    >
      <Card
        style={{
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <input
          type="password"
          aria-label="嵌入服务 API Key"
          value={key}
          onChange={(e) => saveKey(e.target.value)}
          placeholder="嵌入服务 API Key（tt_emb_…，本机保存）"
          className="tt-input"
        />

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
            <Cpu size={14} /> 精度
          </span>
          <select
            value={dtype}
            onChange={(e) => setDtype(e.target.value)}
            disabled={detect.isPending}
            className="tt-input"
            style={{ flex: '0 0 auto', width: 'auto', cursor: 'pointer' }}
          >
            {DTYPES.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
            onClick={() => {
              setResult(null)
              detect.mutate()
            }}
            loading={detect.isPending}
          >
            {!detect.isPending && <Activity size={14} />}
            {detect.isPending ? '检测中…' : '检测'}
          </Button>
          <span
            role="status"
            aria-live="polite"
            style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}
          >
            {stage && <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{stage}</span>}
            {errMsg && <span style={{ color: 'var(--error)', fontSize: 13 }}>{errMsg}</span>}
          </span>
        </div>

        {result && (
          <div role="status" aria-live="polite" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Chip solid color={result.verdict === 'PASS' ? 'var(--success)' : 'var(--error)'}>
                {result.verdict}
              </Chip>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {result.model} · {result.dtype} · dim {result.dim}
              </span>
            </div>

            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              最小余弦{' '}
              <strong style={{ color: cosColor(result.min_cosine), fontVariantNumeric: 'tabular-nums' }}>
                {result.min_cosine.toFixed(6)}
              </strong>{' '}
              · 阈值 {result.threshold} · 矩阵漂移 vs 金标准{' '}
              {result.matrix_max_abs_diff_vs_ref.toFixed(6)}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {result.per_vector.map((v) => (
                <div
                  key={v.label}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: '5px 10px',
                    background: 'var(--bg-raised)',
                    border: '1px solid var(--bg-border)',
                    borderRadius: 'var(--radius-md)',
                    fontSize: 13,
                  }}
                >
                  <span style={{ color: 'var(--text-muted)' }}>{v.label}</span>
                  <span style={{ fontVariantNumeric: 'tabular-nums', color: cosColor(v.cosine) }}>
                    {v.cosine.toFixed(6)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>
    </Section>
  )
}
