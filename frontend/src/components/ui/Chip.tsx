import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface ChipProps {
  /** Tint color (any CSS color, usually a var(--cat-*) token). Drives the
   *  color-mix soft bg/border; falls back to the accent. */
  color?: string
  /** Solid fill instead of the soft color-mix (verdicts / emphasis). */
  solid?: boolean
  /** Show a small color dot before the label. */
  dot?: boolean
  children: ReactNode
  style?: CSSProperties
}

/** Unified soft pill — replaces the 5 hand-rolled pill variants (StatusChip,
 *  CategoryBadge, FlagChips, TurnView chips, Embedding verdict) whose padding/
 *  dot-size parameters had drifted apart. */
export function Chip({ color, solid = false, dot = false, children, style }: ChipProps) {
  return (
    <span
      className={cn('tt-chip', solid && 'tt-chip-solid')}
      style={{ ...(color ? { '--chip-color': color } : null), ...style } as CSSProperties}
    >
      {dot && (
        <span
          aria-hidden="true"
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: color ?? 'var(--accent)',
            flexShrink: 0,
          }}
        />
      )}
      {children}
    </span>
  )
}
