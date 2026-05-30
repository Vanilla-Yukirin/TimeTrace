/** A sparse, slow drift of cherry-blossom petals behind the whole app. Purely
 *  decorative (aria-hidden), sits at z-index:-1 so it shows through the
 *  transparent areas (timeline center, auth bg, gaps) but never over opaque
 *  panels. Hidden entirely under prefers-reduced-motion (see index.css). */

interface Petal {
  left: string
  size: number
  fall: number   // seconds for a full top→bottom drift
  delay: number  // negative → start mid-drift (no empty warm-up)
  sway: number   // px horizontal sway amplitude
  swaydur: number
  opacity: number
}

const PETALS: Petal[] = [
  { left: '7%',  size: 15, fall: 14, delay: -2,  sway: 12, swaydur: 4.5, opacity: 0.5 },
  { left: '21%', size: 10, fall: 17, delay: -8,  sway: 8,  swaydur: 5.5, opacity: 0.38 },
  { left: '38%', size: 17, fall: 12, delay: -5,  sway: 15, swaydur: 4,   opacity: 0.52 },
  { left: '54%', size: 11, fall: 16, delay: -11, sway: 9,  swaydur: 6,   opacity: 0.42 },
  { left: '69%', size: 14, fall: 13, delay: -1,  sway: 13, swaydur: 4.5, opacity: 0.48 },
  { left: '83%', size: 9,  fall: 18, delay: -6,  sway: 7,  swaydur: 6.5, opacity: 0.32 },
  { left: '93%', size: 12, fall: 15, delay: -10, sway: 11, swaydur: 5,   opacity: 0.4 },
]

export function SakuraPetals() {
  return (
    <div className="sakura-layer" aria-hidden="true">
      {PETALS.map((p, i) => (
        <span
          key={i}
          className="sakura-fall"
          style={{
            position: 'absolute',
            top: 0,
            left: p.left,
            animationDelay: `${p.delay}s`,
            ['--fall']: `${p.fall}s`,
          } as React.CSSProperties}
        >
          <span
            className="sakura-sway"
            style={{
              display: 'block',
              ['--sway']: `${p.sway}px`,
              ['--swaydur']: `${p.swaydur}s`,
            } as React.CSSProperties}
          >
            <svg
              width={p.size}
              height={p.size}
              viewBox="0 0 24 24"
              fill="var(--sakura)"
              style={{ display: 'block', opacity: p.opacity }}
            >
              <path d="M12 3 C9 7 6 11 8 15.5 C9.5 18.6 12 19 12 22 C12 19 14.5 18.6 16 15.5 C18 11 15 7 12 3 Z" />
            </svg>
          </span>
        </span>
      ))}
    </div>
  )
}
