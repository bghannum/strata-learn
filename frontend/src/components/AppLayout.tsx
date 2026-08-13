import { Navigate, Outlet } from 'react-router-dom'
import { logout as apiLogout } from '../api/client'
import { useAuth } from '../auth/useAuth'

// Both the auth gate and the shared chrome for authenticated routes — one
// wrapper, since react-router's nested-route <Outlet /> pattern makes a
// combined layout+guard simpler than two separate wrapper components.
function AppLayout() {
  const { user, loading, setUser } = useAuth()

  if (loading) {
    return <p className="p-6 text-sm text-gray-500 dark:text-gray-400">Loading…</p>
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  async function handleLogout() {
    await apiLogout()
    setUser(null)
  }

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-gray-200 px-6 py-3 dark:border-gray-700">
        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Strata Learn</span>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500 dark:text-gray-400">{user.email}</span>
          <button
            type="button"
            onClick={handleLogout}
            className="text-sm text-blue-600 hover:underline dark:text-blue-400"
          >
            Log out
          </button>
        </div>
      </header>
      <Outlet />
    </div>
  )
}

export default AppLayout
