import type { HTMLAttributes } from 'react'
import { cn } from './cn'

// Translated from Organic's `.tag`/.tag-*` rules (Phase 5.5) — see
// Button.tsx's header comment for why this is a Tailwind composition, not a
// copied CSS class. `danger` isn't in the source system (see organic.css's
// --color-organic-danger comment) — scoped narrowly to failed/error states.
export type TagVariant = 'accent' | 'accent-2' | 'neutral' | 'outline' | 'danger'

const VARIANT_CLASSES: Record<TagVariant, string> = {
  accent: 'bg-organic-accent-100 text-organic-accent-800',
  'accent-2': 'bg-organic-accent-2-100 text-organic-accent-2-800',
  neutral: 'bg-organic-neutral-100 text-organic-neutral-800',
  outline: 'border border-organic-accent text-organic-accent',
  danger: 'bg-organic-danger-bg text-organic-danger',
}

interface TagProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: TagVariant
}

function Tag({ variant = 'neutral', className, children, ...props }: TagProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-[10px] py-[3px] text-[11px] tracking-[0.02em]',
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  )
}

export default Tag
