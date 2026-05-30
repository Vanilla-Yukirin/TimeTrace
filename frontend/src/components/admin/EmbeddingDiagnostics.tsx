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

const DTYPES = ['bfloat16', 'float16', 'int8', 'int4'] as const

function cosColor(c: number): string {
  if (c >= 0.999) return '#16a34a'
  if (c >= 0.99) return '#ca8a04'
  return '#dc2626'
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
    <div>
      <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 12 }}>
        嵌入模型自检
      </h3>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
        对本地 Qwen3-VL 嵌入服务（端口 8766）跑官方标准输入，与满精度金标准比对，
        显示各向量余弦相似度与量化漂移。切换精度前可用于部署校验。
      </p>

      <div
        style={{
          padding: 16,
          background: 'var(--bg-surface)',
          borderRadius: 8,
          border: '1px solid var(--bg-border)',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <input
          type="password"
          value={key}
          onChange={(e) => saveKey(e.target.value)}
          placeholder="嵌入服务 API Key（tt_emb_…，本机保存）"
          style={inputStyle}
        />

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)', fontSize: 13 }}>
            <Cpu size={14} /> 精度
          </span>
          <select
            value={dtype}
            onChange={(e) => setDtype(e.target.value)}
            disabled={detect.isPending}
            style={{ ...inputStyle, flex: '0 0 auto', width: 'auto', cursor: 'pointer' }}
          >
            {DTYPES.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <button
            onClick={() => {
              setResult(null)
              detect.mutate()
            }}
            disabled={detect.isPending}
            style={{ ...primaryButton, opacity: detect.isPending ? 0.6 : 1 }}
          >
            <Activity size={14} /> {detect.isPending ? '检测中…' : '检测'}
          </button>
          {stage && <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{stage}</span>}
          {errMsg && <span style={{ color: '#dc2626', fontSize: 13 }}>{errMsg}</span>}
        </div>

        {result && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span
                style={{
                  padding: '2px 10px',
                  borderRadius: 999,
                  fontSize: 12,
                  fontWeight: 600,
                  color: '#fff',
                  background: result.verdict === 'PASS' ? '#16a34a' : '#dc2626',
                }}
              >
                {result.verdict}
              </span>
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
                    borderRadius: 6,
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
      </div>
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: '8px 12px',
  background: 'var(--bg-raised)',
  border: '1px solid var(--bg-border)',
  borderRadius: 6,
  color: 'var(--text-primary)',
  fontSize: 13,
  outline: 'none',
}

const primaryButton: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '8px 14px',
  background: '#2563eb',
  color: '#fff',
  border: 'none',
  borderRadius: 6,
  fontSize: 13,
  cursor: 'pointer',
}
