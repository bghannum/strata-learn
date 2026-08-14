import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  generateQuiz,
  getRepo,
  getRepoQuiz,
  getRepoStudyGuide,
  getSnapshot,
  isAbortError,
  pollQuiz,
  PollTimeoutError,
  useIndexingProgress,
  type AnalysisSnapshot,
  type Quiz,
  type Repo,
  type SnapshotStatus,
  type StudyGuide,
} from '../api/client'

const QUIZ_TIMEOUT_MESSAGE = 'Quiz generation is taking longer than expected. Refresh the page to check its status.'
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
  const [quiz, setQuiz] = useState<Quiz | null>(null)
  const [quizChecked, setQuizChecked] = useState(false)
  const [quizPending, setQuizPending] = useState(false)
  const [quizError, setQuizError] = useState<string | null>(null)

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

  // Recovers an already-enqueued or already-ready quiz after a reload, a
  // second tab, or navigating away and back — without this, the page would
  // only ever offer "Generate Quiz" again, enqueuing a second paid job on
  // top of one that may already be running or done.
  //
  // The AbortController stops pollQuiz's timer chain on unmount/re-run (#38)
  // — without it, navigating away mid-poll left the recursive setTimeout
  // running and calling setState on an unmounted component.
  useEffect(() => {
    if (!guide || !repoId) return
    const controller = new AbortController()
    let foundQuiz: Quiz | null = null
    getRepoQuiz(repoId)
      .then((found) => {
        foundQuiz = found
        return found.status === 'generating' ? pollQuiz(found.id, { signal: controller.signal }) : found
      })
      .then(setQuiz)
      .catch((err) => {
        if (isAbortError(err)) return
        if (err instanceof PollTimeoutError) {
          // Retain the (still-generating) quiz reference rather than
          // leaving `quiz` unset — otherwise the !quiz guard below lets
          // "Generate Quiz" render again and enqueue a duplicate paid job
          // for a job that may still be running server-side (found via
          // Codex's PR #43 review). A page refresh re-runs this effect and
          // fetches the real status via getRepoQuiz.
          if (foundQuiz) setQuiz(foundQuiz)
          setQuizError(QUIZ_TIMEOUT_MESSAGE)
          return
        }
        if (!(err instanceof ApiError && err.status === 404)) {
          setQuizError(err instanceof ApiError ? err.message : 'Could not check for an existing quiz.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setQuizChecked(true)
      })
    return () => controller.abort()
  }, [guide, repoId])

  // Tracks the in-flight poll so unmount can abort it (see the effect above
  // for why this matters).
  const pollControllerRef = useRef<AbortController | null>(null)
  useEffect(() => {
    return () => pollControllerRef.current?.abort()
  }, [])

  function handleGenerateQuiz() {
    if (!repoId) return
    setQuizPending(true)
    setQuizError(null)
    const controller = new AbortController()
    pollControllerRef.current = controller
    let createdQuiz: Quiz | null = null
    generateQuiz(repoId)
      .then((created) => {
        createdQuiz = created
        return pollQuiz(created.id, { signal: controller.signal })
      })
      .then(setQuiz)
      .catch((err) => {
        if (isAbortError(err)) return
        if (err instanceof PollTimeoutError) {
          // Same reasoning as the recovery effect above: retain the
          // still-generating quiz so !quiz doesn't let this button
          // reappear and enqueue a second paid job on top of one that may
          // still finish server-side (found via Codex's PR #43 review).
          if (createdQuiz) setQuiz(createdQuiz)
          setQuizError(QUIZ_TIMEOUT_MESSAGE)
          return
        }
        setQuizError(err instanceof ApiError ? err.message : 'Could not generate a quiz.')
      })
      .finally(() => {
        if (!controller.signal.aborted) setQuizPending(false)
      })
  }

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
        <div className="mt-6 flex flex-wrap items-center gap-3">
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

          {guide && quizChecked && !quiz && (
            <button
              type="button"
              onClick={handleGenerateQuiz}
              disabled={quizPending}
              className="inline-block rounded-md border border-blue-600 px-4 py-2 text-sm font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-50 dark:hover:bg-blue-950"
            >
              {quizPending ? 'Generating quiz…' : 'Generate Quiz'}
            </button>
          )}
          {quiz?.status === 'ready' && (
            <Link
              to={`/quizzes/${quiz.id}`}
              className="inline-block rounded-md border border-blue-600 px-4 py-2 text-sm font-medium text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950"
            >
              Take Quiz
            </Link>
          )}
          {quiz?.status === 'failed' && (
            <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
              Quiz generation failed.{' '}
              <button type="button" onClick={handleGenerateQuiz} className="underline">
                Try again
              </button>
            </p>
          )}
          {quizError && (
            <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
              {quizError}
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
