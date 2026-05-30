import { useId } from 'react'

interface CatMascotProps {
  size?: number
  /** Idle floating animation (sidebar / empty states). */
  float?: boolean
  style?: React.CSSProperties
}

/** A small, friendly sitting-cat mascot. Drawn with a purple→blue brand
 *  gradient that reads on both light and dark surfaces; sleepy "^ ^" eyes keep
 *  the calm/focus mood. Used decoratively (sidebar foot, auth screens, empty
 *  states) — purely 氛围, never blocks interaction. */
export function CatMascot({ size = 96, float = false, style }: CatMascotProps) {
  const gid = useId().replace(/:/g, '')
  const grad = `cat-grad-${gid}`
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 120"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={float ? 'tt-float' : undefined}
      style={style}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={grad} x1="20" y1="10" x2="100" y2="115" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#9a86ff" />
          <stop offset="100%" stopColor="#5aa6ff" />
        </linearGradient>
      </defs>

      {/* tail */}
      <path
        d="M88 92 C112 92 112 60 96 58 C104 70 96 80 84 80 Z"
        fill={`url(#${grad})`}
        opacity="0.92"
      />

      {/* body (seated) */}
      <path
        d="M60 56 C82 56 92 76 92 92 C92 104 78 110 60 110 C42 110 28 104 28 92 C28 76 38 56 60 56 Z"
        fill={`url(#${grad})`}
      />

      {/* ears */}
      <path d="M40 40 L36 16 L58 32 Z" fill={`url(#${grad})`} />
      <path d="M80 40 L84 16 L62 32 Z" fill={`url(#${grad})`} />
      {/* inner ears */}
      <path d="M42 36 L40 23 L52 33 Z" fill="#ffd6ec" opacity="0.85" />
      <path d="M78 36 L80 23 L68 33 Z" fill="#ffd6ec" opacity="0.85" />

      {/* head */}
      <circle cx="60" cy="50" r="26" fill={`url(#${grad})`} />

      {/* sleepy happy eyes */}
      <path d="M46 50 q5 6 10 0" stroke="#1b2030" strokeWidth="3" strokeLinecap="round" fill="none" />
      <path d="M64 50 q5 6 10 0" stroke="#1b2030" strokeWidth="3" strokeLinecap="round" fill="none" />

      {/* nose */}
      <path d="M57 57 L63 57 L60 61 Z" fill="#ff9ec4" />

      {/* whiskers */}
      <g stroke="#ffffff" strokeWidth="1.6" strokeLinecap="round" opacity="0.65">
        <path d="M40 56 L28 53" />
        <path d="M40 60 L29 61" />
        <path d="M80 56 L92 53" />
        <path d="M80 60 L91 61" />
      </g>

      {/* little chest tuft */}
      <path d="M60 70 q4 6 0 14 q-4 -8 0 -14 Z" fill="#ffffff" opacity="0.35" />
    </svg>
  )
}
