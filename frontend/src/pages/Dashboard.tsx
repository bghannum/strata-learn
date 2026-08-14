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
import Button from '../components/ui/Button'

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
        className="flex items-center justify-between gap-4 rounded-2xl px-5 py-3.5 hover:bg-[color-mix(in_srgb,var(--color-organic-text)_5%,transparent)]"
      >
        <div className="min-w-0">
          <p className="truncate font-semibold">{repo.display_name}</p>
          <p className="text-sm opacity-60">{repo.source_type === 'git_url' ? 'Git URL' : 'Zip upload'}</p>
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

  function fetchRepos() {
    setLoading(true)
    setError(null)
    listRepos()
      .then(setRepos)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load repositories.'))
      .finally(() => setLoading(false))
  }

  useEffect(fetchRepos, [])

  return (
    <main className="mx-auto max-w-3xl p-7">
      {loading && (
        <div>
          <div className="mb-5.5 h-8 w-56 animate-pulse rounded-lg bg-organic-neutral-300 motion-reduce:animate-none" />
          <div className="flex flex-col gap-3.5 rounded-[32px] bg-organic-surface p-5.5">
            <div className="h-3.5 w-[90%] animate-pulse rounded-md bg-organic-neutral-300 motion-reduce:animate-none" />
            <div className="h-3.5 w-[70%] animate-pulse rounded-md bg-organic-neutral-300 motion-reduce:animate-none" />
            <div className="h-3.5 w-[80%] animate-pulse rounded-md bg-organic-neutral-300 motion-reduce:animate-none" />
          </div>
        </div>
      )}

      {!loading && error && (
        <div className="max-w-lg pt-14">
          <span className="mb-3.5 inline-block rounded-full bg-organic-danger-bg px-2.5 py-1 text-[13px] text-organic-danger">
            Connection lost
          </span>
          <h1 className="mb-2 text-[32px] leading-tight">We can't reach the API</h1>
          <p className="mb-4.5 text-sm leading-relaxed opacity-70">{error}</p>
          <Button onClick={fetchRepos}>Try again</Button>
        </div>
      )}

      {!loading && !error && repos.length === 0 && (
        <div className="max-w-lg pt-16">
          <div className="mb-4 grid size-24 place-items-center rounded-full bg-organic-accent-2-200">
            <div className="size-12 rounded-full border-[3px] border-dashed border-organic-accent-2-600" />
          </div>
          <h1 className="mb-1.5 text-[34px] leading-tight">Nothing on the shelf yet</h1>
          <p className="mb-4 text-[15px] leading-relaxed opacity-70">
            Point Strata Learn at a repository and it will read it for you — architecture, trade-offs, the parts
            worth understanding — then quiz you on it.
          </p>
          <Link to="/repos/new">
            <Button size="lg">Add your first repo</Button>
          </Link>
        </div>
      )}

      {!loading && !error && repos.length > 0 && (
        <div>
          <div className="mb-5 flex items-end gap-4">
            <div>
              <h1 className="mb-1 text-[34px] leading-tight">Your shelf</h1>
              <p className="text-sm opacity-60">
                {repos.length} {repos.length === 1 ? 'repository' : 'repositories'}
              </p>
            </div>
            <Link to="/repos/new" className="ml-auto">
              <Button>Add repo</Button>
            </Link>
          </div>

          <ul className="flex flex-col gap-1 rounded-[32px] bg-organic-surface p-2">
            {repos.map((repo) => (
              <RepoRow key={repo.id} repo={repo} />
            ))}
          </ul>
        </div>
      )}
    </main>
  )
}

export default Dashboard
