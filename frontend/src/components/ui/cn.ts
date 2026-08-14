/** Joins conditional class names. No conflict resolution (no two variants in
 * this component set ever target the same CSS property at once), so a
 * plain filter+join is enough — not worth a dependency like clsx/tailwind-merge
 * for a handful of primitives. */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}
