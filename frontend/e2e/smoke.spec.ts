import { expect, test } from '@playwright/test'

// Proves the Playwright pipeline end-to-end before any real coverage exists —
// mirrors how #11 proved Vitest with minimal tests first. The backend API
// isn't running in CI; a failed GET /auth/me resolves to "not logged in" the
// same as a real 401 (see AuthContext.tsx), so the redirect to /login still
// happens without it. Locally a developer's Compose stack usually *is* up,
// and if its database has no account yet, /login correctly bounces on to
// /setup — so the "no backend" premise is made explicit by aborting API
// calls rather than left to whatever happens to be listening on :8000.
test('unauthenticated visitor is redirected to /login', async ({ page }) => {
  await page.route('http://localhost:8000/**', (route) => route.abort())

  await page.goto('/')

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
})

test('a fresh install is sent from /login to first-run setup', async ({ page }) => {
  await page.route('http://localhost:8000/**', (route) => {
    if (route.request().url().endsWith('/auth/status')) {
      return route.fulfill({ json: { setup_required: true, secret_required: false } })
    }
    return route.abort()
  })

  await page.goto('/')

  await expect(page).toHaveURL(/\/setup$/)
  await expect(page.getByRole('heading', { name: 'Set up your account' })).toBeVisible()
  await expect(page.getByLabel('Registration secret')).toHaveCount(0)
})
