import { forwardRef, type ButtonHTMLAttributes, type CSSProperties } from 'react'
import { cn } from '@/lib/utils'

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: icon-only buttons must announce themselves to screen readers. */
  'aria-label': string
  style?: CSSProperties
}

/** Square icon-only button (ghost language). The aria-label is mandatory at
 *  the type level — several hand-rolled copies previously shipped with only
 *  a title tooltip. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { className, type, ...rest },
  ref,
) {
  return (
    <button ref={ref} type={type ?? 'button'} className={cn('tt-btn-icon', className)} {...rest} />
  )
})
