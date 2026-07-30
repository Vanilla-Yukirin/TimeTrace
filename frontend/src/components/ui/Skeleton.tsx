import type { CSSProperties } from 'react'

/** Single shimmer placeholder bar. Sizes come from the caller via style. */
export function Skeleton({ style }: { style?: CSSProperties }) {
  return <div className="tt-skeleton" aria-hidden="true" style={style} />
}

/** Stacked skeleton rows (table/list loading). */
export function SkeletonRows({
  rows = 5,
  height = 38,
  gap = 8,
  style,
}: {
  rows?: number
  height?: number
  gap?: number
  style?: CSSProperties
}) {
  return (
    <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap, ...style }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="tt-skeleton" style={{ height }} />
      ))}
    </div>
  )
}
