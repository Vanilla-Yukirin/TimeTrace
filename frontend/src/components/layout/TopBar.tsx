export function TopBar() {
  return (
    <header
      className="flex items-center justify-between px-4 shrink-0"
      style={{
        height: 56,
        borderBottom: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
      }}
    >
      <span className="font-semibold text-base" style={{ color: 'var(--text-primary)', letterSpacing: '0.02em' }}>
        TimeTrace
      </span>
    </header>
  )
}
