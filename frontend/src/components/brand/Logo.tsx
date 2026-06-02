import catLogo from '@/assets/cat-mascot.png'

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
          // Lighter pastel badge (pink-purple → light blue) just for this logo
          // lockup; intentionally NOT the global --grad-brand (which stays the
          // deeper brand gradient used by the wordmark + other badges).
          background: 'linear-gradient(135deg, #e6c9ff 0%, #bcd4ff 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          boxShadow: 'var(--shadow-glow)',
          overflow: 'hidden',
        }}
      >
        <img
          src={catLogo}
          width={Math.round(size * 0.82)}
          height={Math.round(size * 0.82)}
          style={{ objectFit: 'contain', display: 'block' }}
          alt=""
          aria-hidden="true"
          draggable={false}
        />
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
