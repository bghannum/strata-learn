import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { ApiError, login } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { useAuthStatus } from '../auth/useAuthStatus'
import AuthSidePanel from '../components/AuthSidePanel'
import Button from '../components/ui/Button'
import { Field, Input } from '../components/ui/Field'

function Login() {
  const navigate = useNavigate()
  const { setUser } = useAuth()
  const { status, loading } = useAuthStatus()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A fresh install has no account to log in with — send the visitor to
  // first-run setup instead of a form they can't get past. While the status
  // is unknown (loading, or the API is unreachable) this renders normally.
  if (loading) return null
  if (status?.setup_required) return <Navigate to="/setup" replace />

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const user = await login(email, password)
      setUser(user)
      navigate('/')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <main className="mx-auto grid max-w-4xl grid-cols-1 items-center gap-10 p-6 py-16 lg:grid-cols-[1.05fr_.95fr] lg:gap-14">
      <div className="max-w-[420px]">
        <div className="mb-6 flex items-center gap-2.5">
          <span className="grid size-[34px] flex-none place-items-center rounded-full bg-organic-accent">
            <span className="block size-[13px] rounded-full border-[3px] border-organic-bg" />
          </span>
          <span className="font-organic-heading text-xl font-normal">Strata Learn</span>
        </div>
        <h1 className="mb-2.5 text-[38px] leading-tight">Log in</h1>
        <p className="mb-6 text-[15px] leading-relaxed opacity-70">
          Pick up where you left off — your repos, guides, and quiz history are all here.
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Field label="Email" htmlFor="email">
            <Input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </Field>

          <Field label="Password" htmlFor="password">
            <Input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </Field>

          {error && (
            <div className="rounded-2xl bg-organic-danger-bg p-3.5">
              <p className="text-sm text-organic-danger">{error}</p>
            </div>
          )}

          <Button type="submit" size="lg" disabled={submitting} className="self-start">
            {submitting ? 'Logging in…' : 'Log in'}
          </Button>
        </form>

        <p className="mt-4 text-sm opacity-70">
          Forgot your password? Run <code className="text-[13px]">./scripts/reset-password</code> where the app
          is installed.
        </p>
      </div>

      <AuthSidePanel />
    </main>
  )
}

export default Login
