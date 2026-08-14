import type { InputHTMLAttributes, LabelHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react'
import { cn } from './cn'

// Translated from Organic's `.field`/.input` rules (Phase 5.5) — see
// Button.tsx's header comment for why this is a Tailwind composition, not a
// copied CSS class.
const INPUT_CLASSES = cn(
  'w-full min-h-9 py-[6px] px-[14px] text-sm text-organic-text caret-organic-accent',
  'bg-organic-surface border border-organic-divider rounded-full',
  'hover:border-[color-mix(in_srgb,var(--color-organic-text)_45%,transparent)]',
  'outline-none focus-visible:border-organic-accent focus-visible:outline-2',
  'focus-visible:outline-organic-accent focus-visible:outline-offset-0',
  'disabled:opacity-45 disabled:cursor-not-allowed',
)

interface FieldProps {
  label: ReactNode
  htmlFor: string
  labelProps?: LabelHTMLAttributes<HTMLLabelElement>
  children: ReactNode
}

/** Label + control wrapper — pass an Input/Textarea (or any control) as
 * children, already wired to htmlFor/id by the caller. */
export function Field({ label, htmlFor, labelProps, children }: FieldProps) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        {...labelProps}
        className={cn(
          'mb-[5px] block text-xs text-[color-mix(in_srgb,var(--color-organic-text)_70%,transparent)]',
          labelProps?.className,
        )}
      >
        {label}
      </label>
      {children}
    </div>
  )
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(INPUT_CLASSES, className)} {...props} />
}

// .input's border-radius: 999px pill is overridden back to --radius-organic-md
// for the multi-line case — a pill-shaped textarea would look broken once it
// grows past one line, and the source stylesheet doesn't actually style
// textarea.input's radius differently, but a literal pill on a tall box reads
// as a bug rather than the intended "over-round" look.
export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(INPUT_CLASSES, 'min-h-[90px] resize-y rounded-organic-md', className)} {...props} />
}
