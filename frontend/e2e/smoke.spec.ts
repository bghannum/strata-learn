import { expect, test } from '@playwright/test'

// Proves the Playwright pipeline end-to-end before any real coverage exists —
// mirrors how #11 proved Vitest with minimal tests first. The backend API
// isn't running in this job; a failed GET /auth/me resolves to "not logged
// in" the same as a real 401 (see AuthContext.tsx), so the redirect to
// /login still happens without it.
test('unauthenticated visitor is redirected to /login', async ({ page }) => {
  await page.goto('/')

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
})
