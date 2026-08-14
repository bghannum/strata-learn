import { useEffect, useState, type ReactNode } from 'react'
import { getCurrentUser, UNAUTHORIZED_EVENT, type User } from '../api/client'
import { AuthContext } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // Checked once on app load — GET /auth/me succeeds if the session cookie
  // is still valid, 401s (caught below, left as "no user") otherwise.
  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  // A *runtime* 401 (session expires mid-use, not just the initial check
  // above) previously left `user` stale — the UI kept showing logged-in
  // chrome against a dead cookie until the next full reload. client.ts's
  // request() dispatches this event from the one chokepoint every
  // authenticated call passes through; clearing `user` here lets
  // AppLayout's existing `!user` guard redirect to /login on the next
  // render, with no separate navigation logic needed.
  useEffect(() => {
    function handleUnauthorized() {
      setUser(null)
    }
    window.addEventListener(UNAUTHORIZED_EVENT, handleUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handleUnauthorized)
  }, [])

  return <AuthContext.Provider value={{ user, loading, setUser }}>{children}</AuthContext.Provider>
}
