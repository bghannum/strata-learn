import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ApiError,
  getSnapshot,
  listRepos,
  useIndexingProgress,
  type Repo,
  type SnapshotStatus,
} from '../api/client'
import IndexingProgress from '../components/IndexingProgress'

function isTerminal(status: SnapshotStatus | undefined): boolean {
  return status === 'ready' || status === 'failed'
}

function RepoRow({ repo }: { repo: Repo }) {
  const [initialStatus, setInitialStatus] = useState<SnapshotStatus | undefined>(undefined)
  const [initialStatusLoaded, setInitialStatusLoaded] = useState(false)

  useEffect(() => {
    if (!repo.latest_snapshot_id) {
      setInitialStatusLoaded(true)
      return
    }
    getSnapshot(repo.id)
      .then((snapshot) => setInitialStatus(snapshot.status))
      .finally(() => setInitialStatusLoaded(true))
  }, [repo.id, repo.latest_snapshot_id])

  // Only open a socket once we know the last status wasn't already
  // terminal — one connection per row that's actually still moving, not
  // one per row regardless of state.
  const shouldConnect = initialStatusLoaded && !isTerminal(initialStatus)
  const { status } = useIndexingProgress(shouldConnect ? repo.id : undefined, initialStatus)

  return (
    <li>
      <Link
        to={`/repos/${repo.id}`}
        className="flex items-center justify-between rounded-lg border border-gray-200 px-4 py-3 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
      >
        <div>
          <p className="font-medium text-gray-900 dark:text-gray-100">{repo.display_name}</p>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {repo.source_type === 'git_url' ? 'Git URL' : 'Zip upload'}
          </p>
        </div>
        {initialStatusLoaded && <IndexingProgress status={status} variant="chip" />}
      </Link>
    </li>
  )
}

function Dashboard() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listRepos()
      .then(setRepos)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load repositories.'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <main className="mx-auto max-w-2xl p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Repositories</h1>
        <Link
          to="/repos/new"
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          Add Repo
        </Link>
      </div>

      {loading && <p className="mt-6 text-sm text-gray-500 dark:text-gray-400">Loading…</p>}

      {error && (
        <p className="mt-6 rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}

      {!loading && !error && repos.length === 0 && (
        <p className="mt-6 text-sm text-gray-500 dark:text-gray-400">
          No repositories yet.{' '}
          <Link to="/repos/new" className="text-blue-600 hover:underline dark:text-blue-400">
            Add one
          </Link>{' '}
          to get started.
        </p>
      )}

      {repos.length > 0 && (
        <ul className="mt-6 space-y-2">
          {repos.map((repo) => (
            <RepoRow key={repo.id} repo={repo} />
          ))}
        </ul>
      )}
    </main>
  )
}

export default Dashboard
