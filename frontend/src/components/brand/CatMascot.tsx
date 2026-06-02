import catMascotUrl from '@/assets/cat-mascot.svg'

interface CatMascotProps {
  size?: number
  /** Idle floating animation (sidebar / empty states). */
  float?: boolean
  style?: React.CSSProperties
}

/** The TimeTrace cat mascot. A traced white cat with an ahoge, rendered from a
 *  static SVG asset via <img> so its internal ids/clip-paths stay isolated per
 *  instance (the trace ships a fixed clipPath id). Used decoratively (sidebar
 *  foot, auth screens, empty states) — purely 氛围, never blocks interaction. */
export function CatMascot({ size = 96, float = false, style }: CatMascotProps) {
  return (
    <img
      src={catMascotUrl}
      width={size}
      height={size}
      className={float ? 'tt-float' : undefined}
      style={{ display: 'block', objectFit: 'contain', ...style }}
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  )
}
