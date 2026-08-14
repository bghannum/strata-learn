import { cn } from './cn'

// Translated from the Organic design system's styles.css `.btn`/.btn-*`
// rules (Phase 5.5) rather than copied as CSS classes — see Button.tsx's
// header comment for why. Split into its own module (rather than living in
// Button.tsx alongside the component) so link-styled actions (e.g.
// AppLayout.tsx's "Add repo" <Link>) can reuse the same classes without
// tripping oxlint's react/only-export-components rule.
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'icon'

const FOCUS_RING =
  'outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-organic-accent'

// `.btn`'s own font is the heading font (Caprasimo), not the body font —
// easy to miss reading the mockup visually. font-normal pairs with it per
// organic.css's documented heading-weight contract (Caprasimo is
// single-weight).
const BASE =
  `inline-flex items-center justify-center gap-1.5 cursor-pointer no-underline ` +
  `font-organic-heading font-normal text-sm leading-[1.2] rounded-full ` +
  `border border-transparent disabled:opacity-45 disabled:cursor-not-allowed ` +
  `[&>svg]:block ${FOCUS_RING}`

const BTN_PADDING = 'py-[var(--spacing-organic-2)] px-[calc(var(--spacing-organic-3)*1.2)]'

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: cn(
    BTN_PADDING,
    'bg-organic-accent text-organic-bg hover:bg-organic-accent-600 active:bg-organic-accent-700',
  ),
  secondary: cn(
    BTN_PADDING,
    'text-organic-text border-organic-divider',
    'hover:bg-[color-mix(in_srgb,var(--color-organic-text)_7%,transparent)]',
    'active:bg-[color-mix(in_srgb,var(--color-organic-text)_14%,transparent)]',
  ),
  ghost: cn(
    'py-[var(--spacing-organic-2)] px-[var(--spacing-organic-1)] text-organic-accent',
    'hover:bg-[color-mix(in_srgb,var(--color-organic-accent)_10%,transparent)]',
    'active:bg-[color-mix(in_srgb,var(--color-organic-accent)_18%,transparent)]',
  ),
  // 36px, matching .btn-icon exactly (Tailwind's size-9 = 9 * 4px = 36px).
  icon: cn('size-9 p-0 text-organic-text', 'hover:bg-[color-mix(in_srgb,var(--color-organic-text)_7%,transparent)]'),
}

export function buttonClasses(variant: ButtonVariant = 'primary', block = false): string {
  return cn(BASE, VARIANT_CLASSES[variant], block && 'w-full mt-[var(--spacing-organic-2)]')
}
