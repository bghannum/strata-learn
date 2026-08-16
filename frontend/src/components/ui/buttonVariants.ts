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
// Font size intentionally lives in SIZE_PADDING below, not here — stacking
// a second font-size utility from a different call site on top of one set
// here would hit the same "no guaranteed cascade order" problem the size
// variant exists to avoid (see SIZE_PADDING's comment).
// Border *width* only — the color belongs to each variant below. Having the
// base set `border-transparent` meant `secondary` stacked a second
// border-color utility on the same element, and Tailwind resolves that by
// stylesheet order rather than className order (the same trap SIZE_PADDING
// documents): the transparent one won, so the secondary button's outline
// never rendered.
const BASE =
  `inline-flex items-center justify-center gap-1.5 cursor-pointer no-underline ` +
  `font-organic-heading font-normal leading-[1.2] rounded-full ` +
  `border disabled:opacity-45 disabled:cursor-not-allowed ` +
  `[&>svg]:block ${FOCUS_RING}`

export type ButtonSize = 'md' | 'lg'

// The mockup repeats a larger CTA treatment (15px text, ~11px/22-24px
// padding) for several hero/empty-state primary actions — auth submit,
// "Add your first repo", "Sign back in". A real size variant, not a
// className override layered on top of `md`'s own arbitrary-value padding:
// Tailwind utilities have no guaranteed cascade order by className string
// position, only by their position in the generated stylesheet, so
// stacking a second padding utility on the same element is unreliable.
const SIZE_PADDING: Record<ButtonSize, string> = {
  md: 'py-[var(--spacing-organic-2)] px-[calc(var(--spacing-organic-3)*1.2)] text-sm',
  lg: 'py-[11px] px-[22px] text-[15px]',
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  // Found via Codex's PR #47 review: the mockup's literal accent-500 fill
  // with cream text is 3.03:1 — Organic's own readme says this exact pair
  // is "tuned to at least 3:1... not for body copy," i.e. never intended
  // for small text like a button label. Shifted one tier darker
  // (700/800/900 instead of 500/600/700) clears 4.5:1 (5.72:1) while
  // keeping the same "hover/press darkens further" progression.
  primary:
    'border-transparent bg-organic-accent-700 text-organic-bg hover:bg-organic-accent-800 active:bg-organic-accent-900',
  secondary: cn(
    'text-organic-text border-organic-divider',
    'hover:bg-[color-mix(in_srgb,var(--color-organic-text)_7%,transparent)]',
    'active:bg-[color-mix(in_srgb,var(--color-organic-text)_14%,transparent)]',
  ),
  // text-organic-accent-700, not the base accent (3.03:1 → 5.72:1) — same
  // fix as primary, and matches Organic's readme guidance to use a deep
  // ramp step for accent-colored text rather than the accent itself.
  ghost: cn(
    'border-transparent px-[var(--spacing-organic-1)] text-organic-accent-700',
    'hover:bg-[color-mix(in_srgb,var(--color-organic-accent)_10%,transparent)]',
    'active:bg-[color-mix(in_srgb,var(--color-organic-accent)_18%,transparent)]',
  ),
  // 36px, matching .btn-icon exactly (Tailwind's size-9 = 9 * 4px = 36px).
  icon: cn(
    'size-9 border-transparent p-0 text-organic-text',
    'hover:bg-[color-mix(in_srgb,var(--color-organic-text)_7%,transparent)]',
  ),
}

export function buttonClasses(variant: ButtonVariant = 'primary', block = false, size: ButtonSize = 'md'): string {
  return cn(
    BASE,
    variant !== 'icon' && SIZE_PADDING[size],
    VARIANT_CLASSES[variant],
    block && 'w-full mt-[var(--spacing-organic-2)]',
  )
}
