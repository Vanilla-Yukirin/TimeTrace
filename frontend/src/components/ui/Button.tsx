import { forwardRef, type ButtonHTMLAttributes, type CSSProperties } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

type ButtonVariant = 'primary' | 'ghost' | 'danger'

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: 'tt-btn-primary',
  ghost: 'tt-btn-ghost',
  danger: 'tt-btn-danger',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  /** Show a spinner and block interaction while an async action runs. */
  loading?: boolean
  /** Active/selected styling for toggle-like ghost buttons (aria-pressed). */
  active?: boolean
  style?: CSSProperties
}

/** The one button. Visual states (hover/focus/disabled) live in the .tt-btn-*
 *  classes in index.css so they work in both themes with zero per-call-site
 *  styling. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'ghost', loading = false, active, disabled, className, children, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      className={cn(VARIANT_CLASS[variant], className)}
      disabled={disabled || loading}
      data-active={active || undefined}
      aria-pressed={active}
      {...rest}
    >
      {loading && <Loader2 size={14} className="tt-spin" aria-hidden="true" />}
      {children}
    </button>
  )
})
