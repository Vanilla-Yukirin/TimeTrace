import type { CSSProperties, ReactNode } from 'react'

/** Centered page column shared by the data pages (Audit/LlmLog had identical
 *  shells: maxWidth 1120 + same padding). */
export function PageShell({
  children,
  maxWidth = 1120,
  style,
}: {
  children: ReactNode
  maxWidth?: number
  style?: CSSProperties
}) {
  return (
    <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
      <div style={{ maxWidth, margin: '0 auto', padding: '20px 18px 40px', ...style }}>
        {children}
      </div>
    </div>
  )
}

/** Hairline divider (horizontal by default). */
export function Divider({
  vertical = false,
  style,
}: {
  vertical?: boolean
  style?: CSSProperties
}) {
  return (
    <div
      aria-hidden="true"
      style={
        vertical
          ? { width: 1, alignSelf: 'stretch', background: 'var(--bg-border)', ...style }
          : { height: 1, background: 'var(--bg-border)', ...style }
      }
    />
  )
}
