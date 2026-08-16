import { useEffect, useState } from 'react'
import { getAuthStatus, type AuthStatus } from '../api/client'

/** GET /auth/status, fetched once on mount by the two unauthenticated pages
 *  (Login, Setup) so each can send the visitor to the other when it's the
 *  wrong one: a fresh install has no account to log in with, and an
 *  installed one has nothing left to set up. `status` stays null while
 *  loading *and* if the request fails — an unreachable API is not a reason
 *  to redirect anywhere; the page just renders as-is and the real error
 *  surfaces on submit. */
export function useAuthStatus(): { status: AuthStatus | null; loading: boolean } {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getAuthStatus()
      .then((s) => {
        if (!cancelled) setStatus(s)
      })
      .catch(() => {
        if (!cancelled) setStatus(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { status, loading }
}
