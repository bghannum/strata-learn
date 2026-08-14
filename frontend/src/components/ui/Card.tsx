import type { HTMLAttributes } from 'react'
import { cn } from './cn'

// Translated from Organic's `.card`/.card-*`/.elev-*` rules (Phase 5.5) —
// see Button.tsx's header comment for why this is a Tailwind composition,
// not a copied CSS class.
export type Elevation = 'sm' | 'md' | 'lg'

const ELEVATION_CLASSES: Record<Elevation, string> = {
  sm: 'shadow-organic-sm',
  md: 'shadow-organic-md',
  lg: 'shadow-organic-lg',
}

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  elevation?: Elevation
}

function Card({ elevation, className, children, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-[var(--spacing-organic-2)] p-[var(--spacing-organic-3)]',
        // The base --radius-organic-md is overridden to a rounder value for
        // cards specifically in the source stylesheet's final "rounded
        // frame" pass (calc(--radius-lg * 1.15)).
        'rounded-[calc(var(--radius-organic-lg)*1.15)] bg-organic-surface',
        elevation && ELEVATION_CLASSES[elevation],
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

export function CardKicker({ className, children, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn('text-[10px] tracking-[0.1em] text-organic-accent uppercase', className)} {...props}>
      {children}
    </span>
  )
}

export function CardTitle({ className, children, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3 className={cn('font-organic-heading text-[17px] leading-[1.2] font-normal', className)} {...props}>
      {children}
    </h3>
  )
}

export function CardBody({ className, children, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={cn('flex-1 text-[13px] opacity-80', className)} {...props}>
      {children}
    </p>
  )
}

export function CardMeta({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'flex items-center gap-[6px] text-[11px] text-[color-mix(in_srgb,var(--color-organic-text)_50%,transparent)]',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

export default Card
