import { Cat } from 'lucide-react'

interface LogoProps {
  /** Badge size in px (the rounded gradient square). */
  size?: number
  /** Show the "TimeTrace" wordmark + tagline next to the badge. */
  withWordmark?: boolean
  /** Optional tagline under the wordmark. */
  tagline?: string
}

/** The TimeTrace brand lockup: a gradient cat badge + optional gradient
 *  wordmark. Reused in the top bar, sidebar and auth screens. */
export function Logo({ size = 30, withWordmark = false, tagline }: LogoProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div
        style={{
          width: size,
          height: size,
          borderRadius: Math.round(size * 0.32),
          background: 'var(--grad-brand)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          boxShadow: 'var(--shadow-glow)',
        }}
      >
        <Cat size={Math.round(size * 0.62)} color="#fff" strokeWidth={2.1} aria-hidden="true" />
      </div>
      {withWordmark && (
        <div style={{ lineHeight: 1.15 }}>
          <div
            className="text-gradient"
            style={{ fontSize: 17, fontWeight: 800, letterSpacing: '0.01em' }}
          >
            TimeTrace
          </div>
          {tagline && (
            <div style={{ fontSize: 10.5, color: 'var(--text-muted)', marginTop: 1 }}>
              {tagline}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
