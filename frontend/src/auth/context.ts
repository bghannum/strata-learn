import { createContext } from 'react'
import type { User } from '../api/client'

export interface AuthContextValue {
  user: User | null
  loading: boolean
  setUser: (user: User | null) => void
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined)
