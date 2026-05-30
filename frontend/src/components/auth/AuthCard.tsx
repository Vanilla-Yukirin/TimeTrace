import { CatMascot } from '@/components/brand/CatMascot'
import { ThemeToggle } from '@/components/ui/ThemeToggle'

/** Shared chrome for the login / change-password screens: full-bleed
 *  atmospheric background (body --app-glow), a corner theme toggle, and a
 *  centered elevated card with the mascot + gradient title. */
export function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
  footer?: React.ReactNode
}) {
  return (
    <div
      style={{
        position: 'relative',
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <div style={{ position: 'absolute', top: 18, right: 18 }}>
        <ThemeToggle />
      </div>

      <div
        className="tt-rise"
        style={{
          width: '100%',
          maxWidth: 384,
          padding: '32px 30px',
          background: 'var(--bg-surface)',
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-xl)',
          boxShadow: 'var(--shadow-lg)',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <div style={{ textAlign: 'center' }}>
          <CatMascot size={72} float style={{ margin: '0 auto' }} />
          <h1
            className="text-gradient"
            style={{ margin: '12px 0 0', fontSize: 24, fontWeight: 800, letterSpacing: '0.01em' }}
          >
            {title}
          </h1>
          {subtitle && (
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 6 }}>
              {subtitle}
            </div>
          )}
        </div>

        {children}

        {footer && (
          <div style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

export function AuthField({
  label,
  name,
  value,
  onChange,
  type = 'text',
  autoComplete,
  autoFocus,
}: {
  label: string
  name: string
  value: string
  onChange: (v: string) => void
  type?: string
  autoComplete?: string
  autoFocus?: boolean
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>{label}</span>
      <input
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        style={{
          padding: '10px 12px',
          background: 'var(--bg-raised)',
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-md)',
          color: 'var(--text-primary)',
          fontSize: 14,
          outline: 'none',
        }}
      />
    </label>
  )
}

export function AuthButton({
  children,
  disabled,
  busy,
}: {
  children: React.ReactNode
  disabled?: boolean
  busy?: boolean
}) {
  return (
    <button
      type="submit"
      disabled={disabled}
      style={{
        padding: '11px 16px',
        background: disabled ? 'var(--bg-raised)' : 'var(--grad-accent)',
        color: disabled ? 'var(--text-muted)' : '#fff',
        border: 'none',
        borderRadius: 'var(--radius-md)',
        fontSize: 14,
        fontWeight: 600,
        cursor: disabled ? (busy ? 'wait' : 'not-allowed') : 'pointer',
        boxShadow: disabled ? 'none' : 'var(--shadow-glow)',
        marginTop: 2,
      }}
    >
      {children}
    </button>
  )
}
