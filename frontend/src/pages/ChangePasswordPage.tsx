import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'

/** Mirrors the server's ``validate_new_password`` so we give immediate
 *  feedback instead of bouncing off a 422. Server still enforces; this
 *  is just UX. */
function localValidate(newPassword: string, confirm: string): string | null {
  if (newPassword !== confirm) return '两次输入的新密码不一致'
  if (newPassword.length < 8) return '密码至少 8 个字符'
  if (!/[a-zA-Z]/.test(newPassword)) return '密码必须含字母'
  if (!/[0-9]/.test(newPassword)) return '密码必须含数字'
  return null
}

export function ChangePasswordPage() {
  const { user, refresh } = useAuth()
  const navigate = useNavigate()
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const localErr = newPassword || confirm ? localValidate(newPassword, confirm) : null
  const canSubmit = !!oldPassword && !!newPassword && !!confirm && !localErr

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    if (!canSubmit) {
      setError(localErr ?? '请填完所有字段')
      return
    }
    setSubmitting(true)
    try {
      await api.changePassword({ old_password: oldPassword, new_password: newPassword })
      refresh()
      navigate('/', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-app)',
        padding: 24,
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          width: '100%',
          maxWidth: 380,
          padding: 32,
          background: 'var(--bg-surface)',
          border: '1px solid var(--bg-border)',
          borderRadius: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <div style={{ marginBottom: 4 }}>
          <h1 style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            修改密码
          </h1>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            {user?.must_change_password
              ? '首次登录请修改默认密码'
              : '当前用户：' + (user?.username ?? '')}
          </div>
        </div>

        <Field
          label="当前密码"
          name="old_password"
          type="password"
          value={oldPassword}
          onChange={setOldPassword}
          autoComplete="current-password"
          autoFocus
        />
        <Field
          label="新密码（≥ 8 字符，含字母 + 数字）"
          name="new_password"
          type="password"
          value={newPassword}
          onChange={setNewPassword}
          autoComplete="new-password"
        />
        <Field
          label="再次输入新密码"
          name="confirm_password"
          type="password"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
        />

        {(localErr || error) && (
          <div style={{ fontSize: 12, color: '#ef4444' }}>{localErr ?? error}</div>
        )}

        <button
          type="submit"
          disabled={!canSubmit || submitting}
          style={{
            padding: '10px 16px',
            background: submitting ? 'var(--bg-raised)' : '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            fontSize: 14,
            fontWeight: 500,
            cursor: submitting ? 'wait' : 'pointer',
            opacity: !canSubmit ? 0.6 : 1,
          }}
        >
          {submitting ? '修改中...' : '修改密码'}
        </button>
      </form>
    </div>
  )
}

interface FieldProps {
  label: string
  name: string
  value: string
  onChange: (v: string) => void
  type?: string
  autoComplete?: string
  autoFocus?: boolean
}

function Field({ label, name, value, onChange, type = 'text', autoComplete, autoFocus }: FieldProps) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{label}</span>
      <input
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        style={{
          padding: '8px 12px',
          background: 'var(--bg-raised)',
          border: '1px solid var(--bg-border)',
          borderRadius: 6,
          color: 'var(--text-primary)',
          fontSize: 14,
          outline: 'none',
        }}
      />
    </label>
  )
}
