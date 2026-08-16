import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { ApiError, register } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { useAuthStatus } from '../auth/useAuthStatus'
import AuthSidePanel from '../components/AuthSidePanel'
import Button from '../components/ui/Button'
import { Field, Input } from '../components/ui/Field'

// First-run screen: creates the app's single account (ADR-007). Reached
// automatically — Login redirects here while GET /auth/status says
// setup_required — so a fresh `docker compose up` lands on a form that
// works, not a login form with no account behind it. Once the account
// exists this page sends visitors back to /login; there is nothing left to
// set up. The registration-secret field appears only when the server has
// REGISTRATION_SECRET set (the hosted-deployment posture); the default local
// install never sees it.
function Setup() {
  const navigate = useNavigate()
  const { setUser } = useAuth()
  const { status, loading } = useAuthStatus()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [registrationSecret, setRegistrationSecret] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const secretRequired = status?.secret_required ?? false

  if (loading) return null
  if (status !== null && !status.setup_required) return <Navigate to="/login" replace />

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const user = await register(email, password, secretRequired ? registrationSecret : undefined)
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
        <h1 className="mb-2.5 text-[38px] leading-tight">Set up your account</h1>
        <p className="mb-6 text-[15px] leading-relaxed opacity-70">
          This is a one-time step. Strata Learn keeps a single account per install, and this is it — pick the
          email and password you'll log in with from now on.
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
              maxLength={72}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </Field>

          {secretRequired && (
            <>
              <Field label="Registration secret" htmlFor="registrationSecret">
                <Input
                  id="registrationSecret"
                  type="password"
                  required
                  value={registrationSecret}
                  onChange={(e) => setRegistrationSecret(e.target.value)}
                />
              </Field>
              <p className="-mt-2 text-xs opacity-70">
                This server requires REGISTRATION_SECRET from its .env to create the account.
              </p>
            </>
          )}

          {error && (
            <div className="rounded-2xl bg-organic-danger-bg p-3.5">
              <p className="text-sm text-organic-danger">{error}</p>
            </div>
          )}

          <Button type="submit" size="lg" disabled={submitting} className="self-start">
            {submitting ? 'Creating account…' : 'Create account'}
          </Button>
        </form>
      </div>

      <AuthSidePanel />
    </main>
  )
}

export default Setup
