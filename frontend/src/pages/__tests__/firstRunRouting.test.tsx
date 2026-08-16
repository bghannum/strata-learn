import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AuthStatus } from '../../api/client'
import { AuthContext } from '../../auth/context'
import Login from '../Login'
import Setup from '../Setup'

// Login and Setup each ask GET /auth/status once and hand the visitor to the
// other page when they're the wrong one: a fresh install has no account to
// log in with, and an installed one has nothing to set up. Both pages are
// rendered here without AppLayout (neither sits behind its auth guard).

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

function stubStatus(status: AuthStatus | 'unreachable') {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/auth/status')) {
        return status === 'unreachable' ? Promise.reject(new TypeError('Failed to fetch')) : Promise.resolve(jsonResponse(status))
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected call' }, 500))
    }),
  )
}

function renderAt(path: string) {
  return render(
    <AuthContext.Provider value={{ user: null, loading: false, setUser: () => {} }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/setup" element={<Setup />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('first-run routing between /login and /setup', () => {
  afterEach(() => {
    // Testing Library only auto-registers its cleanup when a global afterEach
    // exists (globals: false here), so unmount explicitly.
    cleanup()
    vi.unstubAllGlobals()
  })

  it('sends a fresh install from /login to /setup', async () => {
    stubStatus({ setup_required: true, secret_required: false })
    renderAt('/login')
    await waitFor(() => screen.getByRole('heading', { name: 'Set up your account' }))
  })

  it('sends an installed app from /setup back to /login', async () => {
    stubStatus({ setup_required: false, secret_required: false })
    renderAt('/setup')
    await waitFor(() => screen.getByRole('heading', { name: 'Log in' }))
  })

  it('stays on /login when the account exists', async () => {
    stubStatus({ setup_required: false, secret_required: false })
    renderAt('/login')
    await waitFor(() => screen.getByRole('heading', { name: 'Log in' }))
    expect(screen.queryByRole('link', { name: 'Register' })).toBeNull()
  })

  it('renders the login form as-is when the API is unreachable', async () => {
    // Nothing to redirect on; the real error surfaces on submit. This is
    // also the state the Playwright smoke test runs in (no backend).
    stubStatus('unreachable')
    renderAt('/login')
    await waitFor(() => screen.getByRole('heading', { name: 'Log in' }))
  })

  it('asks for the registration secret only when the server requires it', async () => {
    stubStatus({ setup_required: true, secret_required: false })
    renderAt('/setup')
    await waitFor(() => screen.getByRole('heading', { name: 'Set up your account' }))
    expect(screen.queryByLabelText('Registration secret')).toBeNull()
    cleanup()

    stubStatus({ setup_required: true, secret_required: true })
    renderAt('/setup')
    await waitFor(() => screen.getByLabelText('Registration secret'))
  })
})
