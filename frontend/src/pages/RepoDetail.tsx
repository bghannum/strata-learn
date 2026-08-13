import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  getRepo,
  getRepoStudyGuide,
  getSnapshot,
  useIndexingProgress,
  type AnalysisSnapshot,
  type Repo,
  type SnapshotStatus,
  type StudyGuide,
} from '../api/client'
import IndexingProgress from '../components/IndexingProgress'

function isTerminal(status: SnapshotStatus | undefined): boolean {
  return status === 'ready' || status === 'failed'
}

function RepoDetail() {
  const { repoId } = useParams<{ repoId: string }>()
  const [repo, setRepo] = useState<Repo | null>(null)
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [showRaw, setShowRaw] = useState(false)
  const [guide, setGuide] = useState<StudyGuide | null>(null)
  const [guideError, setGuideError] = useState<string | null>(null)

  useEffect(() => {
    if (!repoId) return
    Promise.all([getRepo(repoId), getSnapshot(repoId)])
      .then(([fetchedRepo, fetchedSnapshot]) => {
        setRepo(fetchedRepo)
        setSnapshot(fetchedSnapshot)
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Could not load this repository.'))
      .finally(() => setLoaded(true))
  }, [repoId])

  const shouldConnect = loaded && !isTerminal(snapshot?.status)
  const {
    status,
    error: liveError,
    lastNonTerminalStatus,
  } = useIndexingProgress(shouldConnect ? repoId : undefined, snapshot?.status)

  useEffect(() => {
    if (status !== 'ready' || !repoId) return
    getRepoStudyGuide(repoId)
      .then(setGuide)
      .catch((err) => setGuideError(err instanceof ApiError ? err.message : 'Could not load the study guide.'))
  }, [status, repoId])

  if (!loaded) {
    return (
      <main className="mx-auto max-w-2xl p-6">
        <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>
      </main>
    )
  }

  if (loadError || !repo) {
    return (
      <main className="mx-auto max-w-2xl p-6">
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
          {loadError ?? 'Repository not found.'}
        </p>
      </main>
    )
  }

  return (
    <main className="mx-auto max-w-2xl p-6">
      <Link to="/" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
        ← All repositories
      </Link>
      <h1 className="mt-2 text-xl font-semibold text-gray-900 dark:text-gray-100">{repo.display_name}</h1>
      <p className="text-sm text-gray-500 dark:text-gray-400">{repo.source_uri}</p>

      <div className="mt-6">
        <IndexingProgress
          status={status}
          lastNonTerminalStatus={lastNonTerminalStatus}
          error={liveError}
          variant="stepper"
        />
      </div>

      {status === 'ready' && (
        <div className="mt-6">
          {guide && (
            <Link
              to={`/study-guides/${guide.id}`}
              className="inline-block rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
            >
              View Study Guide
            </Link>
          )}
          {guideError && (
            <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
              {guideError}
            </p>
          )}
        </div>
      )}

      {status === 'failed' && (
        // No POST /repos/{id}/reindex endpoint exists yet — retrying means
        // adding the repo again rather than re-triggering this same job.
        <Link to="/repos/new" className="mt-6 inline-block text-sm text-blue-600 hover:underline dark:text-blue-400">
          Try adding it again
        </Link>
      )}

      <div className="mt-8 border-t border-gray-200 pt-4 dark:border-gray-700">
        <button
          type="button"
          onClick={() => setShowRaw((visible) => !visible)}
          className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        >
          {showRaw ? 'Hide' : 'View'} raw analysis
        </button>
        {showRaw && (
          <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-gray-900 p-3 text-xs text-gray-100">
            {JSON.stringify(snapshot, null, 2)}
          </pre>
        )}
      </div>
    </main>
  )
}

export default RepoDetail
