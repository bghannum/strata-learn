import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../AuthContext'
import { useAuth } from '../useAuth'
import { UNAUTHORIZED_EVENT } from '../../api/client'

function Probe() {
  const { user, loading } = useAuth()
  if (loading) return <p>loading</p>
  return <p>{user ? `logged in as ${user.email}` : 'logged out'}</p>
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

describe('AuthProvider', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('clears the logged-in user when a runtime 401 fires elsewhere in the app', async () => {
    // AuthProvider's own initial GET /auth/me succeeds here — this test is
    // specifically about a *later* runtime 401 from some other authenticated
    // call, which client.ts's request() reports via UNAUTHORIZED_EVENT
    // (found via issue #33: nothing previously cleared auth state after a
    // session expired mid-use, leaving the UI showing logged-in chrome
    // against a dead cookie).
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ id: '1', email: 'a@example.com', created_at: 'now' })),
    )

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await waitFor(() => screen.getByText('logged in as a@example.com'))

    act(() => {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    })

    await waitFor(() => screen.getByText('logged out'))
  })
})

describe('client.ts request()', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('dispatches UNAUTHORIZED_EVENT when an authenticated call gets a 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'Not authenticated' }, 401)))
    const { getCurrentUser, UNAUTHORIZED_EVENT: eventName } = await import('../../api/client')

    const handler = vi.fn()
    window.addEventListener(eventName, handler)
    await expect(getCurrentUser()).rejects.toThrow()

    expect(handler).toHaveBeenCalledTimes(1)
    window.removeEventListener(eventName, handler)
  })
})
