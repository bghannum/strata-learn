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
  reindexRepo,
  useIndexingProgress,
  type AnalysisSnapshot,
  type FeedbackMode,
  type Quiz,
  type Repo,
  type SnapshotStatus,
  type StudyGuide,
} from '../api/client'
import IndexingProgress from '../components/IndexingProgress'
import Button from '../components/ui/Button'
import { cn } from '../components/ui/cn'

const QUIZ_TIMEOUT_MESSAGE = 'Quiz generation is taking longer than expected. Refresh the page to check its status.'

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
  // #37: end_of_quiz is ui-spec.md §6.5's own stated default ("closer to
  // genuine self-assessment") — found via Codex's PR #50 review: the field
  // existed end-to-end but nothing ever let a caller choose 'immediate', so
  // no quiz generated through the app could actually use it.
  const [feedbackMode, setFeedbackMode] = useState<FeedbackMode>('end_of_quiz')
  const [reindexing, setReindexing] = useState(false)
  const [reindexError, setReindexError] = useState<string | null>(null)

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
    generateQuiz(repoId, feedbackMode)
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

  // #26: retries a failed indexing run in place, replacing the earlier
  // "Try adding it again" fallback (which created a whole new Repo row
  // rather than reusing this one — the endpoint this needed didn't exist
  // yet in Phase 4a). A fresh snapshot means useIndexingProgress reconnects
  // its WebSocket the moment `snapshot` updates to a non-terminal status.
  function handleRetry() {
    if (!repoId) return
    setReindexing(true)
    setReindexError(null)
    reindexRepo(repoId)
      .then((updatedRepo) => {
        setRepo(updatedRepo)
        return getSnapshot(repoId)
      })
      .then(setSnapshot)
      .catch((err) => setReindexError(err instanceof ApiError ? err.message : 'Could not retry indexing.'))
      .finally(() => setReindexing(false))
  }

  if (!loaded) {
    return (
      <main className="mx-auto max-w-2xl p-7">
        <p className="text-sm opacity-70">Loading…</p>
      </main>
    )
  }

  if (loadError || !repo) {
    return (
      <main className="mx-auto max-w-2xl p-7">
        <div className="rounded-2xl bg-organic-danger-bg p-3.5">
          <p className="text-sm text-organic-danger">{loadError ?? 'Repository not found.'}</p>
        </div>
      </main>
    )
  }

  return (
    <main className="mx-auto max-w-2xl p-7">
      <Link to="/" className="text-[13px] opacity-60 hover:underline">
        ← All repositories
      </Link>
      <h1 className="mt-2 mb-1 text-[34px] leading-tight">{repo.display_name}</h1>
      <p className="font-mono text-xs opacity-55">{repo.source_uri}</p>

      <div className="mt-5.5 rounded-[32px] bg-organic-surface p-7">
        <IndexingProgress
          status={status}
          lastNonTerminalStatus={lastNonTerminalStatus}
          error={liveError}
          variant="stepper"
          onRetry={handleRetry}
          retrying={reindexing}
          retryError={reindexError}
        />
      </div>

      {status === 'ready' && (
        <div className="mt-5.5 flex flex-wrap items-center gap-3">
          {guide && (
            <Link to={`/study-guides/${guide.id}`}>
              <Button>View Study Guide</Button>
            </Link>
          )}
          {guideError && (
            <div className="rounded-2xl bg-organic-danger-bg p-3.5">
              <p className="text-sm text-organic-danger">{guideError}</p>
            </div>
          )}

          {guide && quizChecked && !quiz && (
            <>
              {/* Same has-[:checked]: segmented-control pattern as AddRepo.tsx's
              Git URL/Upload zip toggle. */}
              <div className="inline-flex overflow-hidden rounded-full border border-organic-divider">
                {(['end_of_quiz', 'immediate'] as const).map((mode, index) => (
                  <label
                    key={mode}
                    className={cn(
                      'cursor-pointer px-3 py-1.5 text-xs has-[:checked]:bg-organic-accent-700 has-[:checked]:text-organic-bg',
                      index > 0 && 'border-l border-organic-divider',
                    )}
                  >
                    <input
                      type="radio"
                      name="feedbackMode"
                      className="sr-only"
                      checked={feedbackMode === mode}
                      onChange={() => setFeedbackMode(mode)}
                    />
                    {mode === 'end_of_quiz' ? 'End of quiz' : 'As I go'}
                  </label>
                ))}
              </div>
              <Button variant="secondary" onClick={handleGenerateQuiz} disabled={quizPending}>
                {quizPending ? 'Generating quiz…' : 'Generate Quiz'}
              </Button>
            </>
          )}
          {quiz?.status === 'ready' && (
            <Link to={`/quizzes/${quiz.id}`}>
              <Button variant="secondary">Take Quiz</Button>
            </Link>
          )}
          {quiz?.status === 'failed' && (
            <div className="rounded-2xl bg-organic-danger-bg p-3.5">
              <p className="text-sm text-organic-danger">
                Quiz generation failed.{' '}
                <button type="button" onClick={handleGenerateQuiz} className="font-semibold underline">
                  Try again
                </button>
              </p>
            </div>
          )}
          {quizError && (
            <div className="rounded-2xl bg-organic-danger-bg p-3.5">
              <p className="text-sm text-organic-danger">{quizError}</p>
            </div>
          )}
        </div>
      )}

      <div className="mt-8 border-t border-organic-divider pt-4">
        <button
          type="button"
          onClick={() => setShowRaw((visible) => !visible)}
          className="text-sm opacity-60 hover:opacity-100"
        >
          {showRaw ? 'Hide' : 'View'} raw analysis
        </button>
        {showRaw && (
          <pre className="mt-2 max-h-96 overflow-auto rounded-2xl bg-organic-neutral-900 p-4 text-xs text-organic-neutral-200">
            {JSON.stringify(snapshot, null, 2)}
          </pre>
        )}
      </div>
    </main>
  )
}

export default RepoDetail
