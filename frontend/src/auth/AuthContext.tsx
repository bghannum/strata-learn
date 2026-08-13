import { useEffect, useState, type ReactNode } from 'react'
import { getCurrentUser, type User } from '../api/client'
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

  return <AuthContext.Provider value={{ user, loading, setUser }}>{children}</AuthContext.Provider>
}
