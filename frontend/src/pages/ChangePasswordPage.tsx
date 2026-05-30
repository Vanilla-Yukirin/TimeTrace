import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { AuthCard, AuthField, AuthButton } from '@/components/auth/AuthCard'

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
  const { user, refetch } = useAuth()
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
      // Backend keeps THIS session (revokes the others) and clears the
      // must_change flag. MUST await refetch so AuthContext sees
      // must_change_password=false BEFORE we navigate to '/', otherwise
      // RequireAuth reads the stale must_change=true and redirects right back
      // here ("改密后不跳转" bug).
      toast.success('密码已修改', { description: '其他设备的登录已失效' })
      await refetch()
      navigate('/', { replace: true })
    } catch (err) {
      const msg = (err as Error).message
      setError(msg)
      toast.error('修改失败', { description: msg })
      setSubmitting(false)
    }
  }

  return (
    <AuthCard
      title="修改密码"
      subtitle={
        user?.must_change_password
          ? '首次登录请修改默认密码'
          : '当前用户：' + (user?.username ?? '')
      }
    >
      <form onSubmit={onSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <AuthField
          label="当前密码"
          name="old_password"
          type="password"
          value={oldPassword}
          onChange={setOldPassword}
          autoComplete="current-password"
          autoFocus
        />
        <AuthField
          label="新密码（≥ 8 字符，含字母 + 数字）"
          name="new_password"
          type="password"
          value={newPassword}
          onChange={setNewPassword}
          autoComplete="new-password"
        />
        <AuthField
          label="再次输入新密码"
          name="confirm_password"
          type="password"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
        />

        {(localErr || error) && (
          <div style={{ fontSize: 12, color: 'var(--error)' }}>{localErr ?? error}</div>
        )}

        <AuthButton disabled={!canSubmit || submitting} busy={submitting}>
          {submitting ? '修改中…' : '修改密码'}
        </AuthButton>
      </form>
    </AuthCard>
  )
}
