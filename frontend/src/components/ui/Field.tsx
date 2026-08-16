import type {
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
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

// The source system has no select rule of its own, so this borrows .input's —
// a picker and a text field sit next to each other in the same row (the diff's
// version selectors) and would read as two different systems otherwise.
// `appearance-none` drops the platform arrow so the pill shape survives on
// WebKit, which otherwise renders its own square control and ignores the
// radius; the right padding leaves room for the chevron drawn as a background
// image.
export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        INPUT_CLASSES,
        'appearance-none cursor-pointer bg-no-repeat pr-9',
        "bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2212%22%20height%3D%228%22%20viewBox%3D%220%200%2012%208%22%3E%3Cpath%20d%3D%22M1%201l5%205%205-5%22%20stroke%3D%22%23201e1d%22%20stroke-width%3D%221.6%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%2F%3E%3C%2Fsvg%3E')]",
        'bg-[position:right_14px_center] bg-[size:12px_8px]',
        className,
      )}
      {...props}
    />
  )
}

// .input's border-radius: 999px pill is overridden back to --radius-organic-md
// for the multi-line case — a pill-shaped textarea would look broken once it
// grows past one line, and the source stylesheet doesn't actually style
// textarea.input's radius differently, but a literal pill on a tall box reads
// as a bug rather than the intended "over-round" look.
export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(INPUT_CLASSES, 'min-h-[90px] resize-y rounded-organic-md', className)} {...props} />
}
