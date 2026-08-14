import type { ButtonHTMLAttributes } from 'react'
import { buttonClasses, type ButtonSize, type ButtonVariant } from './buttonVariants'
import { cn } from './cn'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Full-width, matching .btn-block. */
  block?: boolean
}

function Button({ variant = 'primary', size = 'md', block = false, className, type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonClasses(variant, block, size), className)} {...props} />
}

export default Button
