import { afterEach, describe, expect, it, vi } from 'vitest'
import { getCurrentUser, listRepos, login, register, UNAUTHORIZED_EVENT } from '../client'

function unauthorizedResponse(): Response {
  return {
    ok: false,
    status: 401,
    json: async () => ({ detail: 'Invalid credentials' }),
  } as Response
}

/** Runs `call`, expecting it to reject, and reports whether the global
 *  session-expired event fired. */
async function firedUnauthorized(call: () => Promise<unknown>): Promise<boolean> {
  const listener = vi.fn()
  window.addEventListener(UNAUTHORIZED_EVENT, listener)
  try {
    await expect(call()).rejects.toThrow()
    return listener.mock.calls.length > 0
  } finally {
    window.removeEventListener(UNAUTHORIZED_EVENT, listener)
  }
}

describe('401 handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  // #44: /login isn't behind AppLayout's auth guard, so a signed-in user can
  // navigate there directly. A mistyped password used to clear their valid
  // AuthContext user even though the backend session cookie was untouched.
  it('does not report a failed login as an expired session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(unauthorizedResponse()))

    expect(await firedUnauthorized(() => login('a@example.com', 'wrong-password'))).toBe(false)
  })

  it('does not report a failed registration as an expired session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(unauthorizedResponse()))

    expect(await firedUnauthorized(() => register('a@example.com', 'pw', 'bad-secret'))).toBe(false)
  })

  // The behavior from #33 that this must not regress: a 401 from an
  // authenticated call really does mean the session died, and the UI has to
  // stop showing logged-in chrome.
  it('still reports a runtime 401 from an authenticated call', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(unauthorizedResponse()))

    expect(await firedUnauthorized(() => listRepos())).toBe(true)
    expect(await firedUnauthorized(() => getCurrentUser())).toBe(true)
  })
})
