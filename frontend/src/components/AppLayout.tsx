import { useState } from 'react'
import { Link, Navigate, Outlet } from 'react-router-dom'
import { logout as apiLogout } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { BreadcrumbContext } from './breadcrumb'
import { buttonClasses } from './ui/buttonVariants'

// The mockup's avatar shows two-letter initials from a name field the User
// model doesn't have (id/email/password_hash/created_at only, per
// docs/design/original-project-plan.md §7) — derived from the email's local
// part instead of fabricating a name.
function initials(email: string): string {
  return email.split('@')[0]!.slice(0, 2).toUpperCase()
}

// Both the auth gate and the shared chrome for authenticated routes — one
// wrapper, since react-router's nested-route <Outlet /> pattern makes a
// combined layout+guard simpler than two separate wrapper components.
function AppLayout() {
  const { user, loading, setUser } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const [breadcrumb, setBreadcrumb] = useState<string | null>(null)

  if (loading) {
    return <p className="p-6 text-sm text-organic-text opacity-70">Loading…</p>
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  async function handleLogout() {
    await apiLogout()
    setUser(null)
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 flex items-center gap-[18px] border-b border-organic-divider bg-organic-bg px-7 py-3.5">
        <Link to="/" className="flex items-center gap-2.5 text-organic-text no-underline">
          <span className="grid size-[26px] flex-none place-items-center rounded-full bg-organic-accent">
            <span className="block size-2.5 rounded-full border-[2.75px] border-organic-bg" />
          </span>
          <span className="font-organic-heading text-[17px] font-normal tracking-tight">Strata Learn</span>
        </Link>

        {breadcrumb && (
          // Hidden on phones: at ~390px the trail squeezes the wordmark itself
          // onto two lines, and the page it names is the one already on screen.
          <p className="hidden min-w-0 truncate text-sm opacity-55 md:block">
            <span aria-hidden="true" className="mr-[18px]">
              /
            </span>
            {breadcrumb}
          </p>
        )}

        <div className="ml-auto flex items-center gap-2.5">
          <Link to="/repos/new" className={buttonClasses('secondary')}>
            Add repo
          </Link>

          <div className="relative">
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-label="Account menu"
              aria-expanded={menuOpen}
              className="grid size-7 place-items-center rounded-full border-0 bg-organic-accent-2-300 font-organic-heading text-[11px] font-normal text-organic-accent-2-800 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-organic-accent"
            >
              {initials(user.email)}
            </button>

            {menuOpen && (
              <>
                {/* Click-outside backdrop, same pattern as CitationPanel.tsx —
                tabIndex={-1} found via Codex's PR #47 review: without it, a
                keyboard user opening the menu and pressing Tab landed here
                first (a full-screen, visually empty target) instead of
                "Sign out", with the focus outline off at the viewport edge.
                Excluded from the tab order; still clickable by mouse. */}
                <button
                  type="button"
                  tabIndex={-1}
                  aria-label="Close account menu"
                  className="fixed inset-0 z-10 cursor-default"
                  onClick={() => setMenuOpen(false)}
                />
                <div className="absolute top-[38px] right-0 z-20 flex w-[212px] flex-col gap-2.5 rounded-organic-md bg-organic-bg p-3.5 shadow-organic-lg">
                  <p className="truncate text-[13.5px] font-semibold text-organic-text">{user.email}</p>
                  <div className="h-px bg-organic-divider" />
                  <button
                    type="button"
                    onClick={handleLogout}
                    className="cursor-pointer border-0 bg-transparent p-0 text-left font-organic-body text-[13.5px] font-semibold text-organic-accent-700 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-organic-accent"
                  >
                    Sign out
                  </button>
                  {/* opacity-70, not -52 — found via Codex's PR #47 review: -52
                  is 3.32:1 on organic-bg, below the 4.5:1 small-text
                  requirement; -70 clears it (5.67:1). */}
                  <p className="m-0 text-[11.5px] leading-normal text-organic-text opacity-70">
                    Signing out clears the session cookie on this device.
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      </header>
      <BreadcrumbContext value={setBreadcrumb}>
        <Outlet />
      </BreadcrumbContext>
    </div>
  )
}

export default AppLayout
