import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  checkForUpdates,
  generateQuiz,
  getMastery,
  getRepo,
  getRepoQuiz,
  getRepoStudyGuide,
  getSnapshot,
  getUpdateStatus,
  isAbortError,
  listRepoAttempts,
  pollQuiz,
  PollTimeoutError,
  reindexRepo,
  useIndexingProgress,
  type AnalysisSnapshot,
  type AttemptSummary,
  type FeedbackMode,
  type Mastery,
  type Quiz,
  type Repo,
  type SnapshotStatus,
  type StudyGuide,
  type UpdateStatus,
} from '../api/client'
import { useBreadcrumb } from '../components/breadcrumb'
import IndexingProgress from '../components/IndexingProgress'
import Button from '../components/ui/Button'
import Tag from '../components/ui/Tag'
import { cn } from '../components/ui/cn'

const QUIZ_TIMEOUT_MESSAGE = 'Quiz generation is taking longer than expected. Refresh the page to check its status.'

function isTerminal(status: SnapshotStatus | undefined): boolean {
  return status === 'ready' || status === 'failed'
}

/** "unknown" has several distinct causes and saying which one is the whole
 *  point — an unexplained shrug next to a "Check for updates" button that a
 *  zip-upload repo can never satisfy would just be confusing. */
const UNKNOWN_REASONS: Record<string, string> = {
  zip_upload: 'Uploaded zip — no remote to compare against',
  never_checked: 'Not checked yet',
  remote_unreachable: "Couldn't reach the remote",
  no_indexed_commit: 'No commit recorded for this index',
}

// Each unit paired with how many of it fit in the next one up — the loop below
// divides its way up the list until the number is small enough to read.
const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 7],
  ['week', 4.35],
  ['month', 12],
]

const RELATIVE_FORMAT = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/** "2 hours ago" — a guide's age is the useful fact here, not its wall-clock
 *  timestamp: the question this line answers is "is this current?". */
function relativeTime(iso: string): string {
  let value = (new Date(iso).getTime() - Date.now()) / 1000
  for (const [unit, perNext] of RELATIVE_UNITS) {
    if (Math.abs(value) < perNext) return RELATIVE_FORMAT.format(Math.round(value), unit)
    value /= perNext
  }
  return RELATIVE_FORMAT.format(Math.round(value), 'year')
}

/** "Today, 14:20" / "Yesterday, 09:05" / "3 Aug, 09:05" — quiz history is
 *  mostly read as a recent sequence, so the last two days get names and
 *  everything older gets a date. */
function sittingTimestamp(iso: string): string {
  const date = new Date(iso)
  const time = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const daysAgo = Math.floor((startOfToday.getTime() - date.getTime()) / 86_400_000) + 1
  if (daysAgo <= 0) return `Today, ${time}`
  if (daysAgo === 1) return `Yesterday, ${time}`
  return `${date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}, ${time}`
}

// accent-2 (sage) is this system's positive voice; the warm outlined tag marks
// a score worth revisiting. Deliberately *not* organic-danger, which is scoped
// to actual failures (see organic.css) — a 55% is a study prompt, not an error.
const PASSING_SCORE = 0.7

function MasterySection({ mastery }: { mastery: Mastery }) {
  if (mastery.completed_attempts === 0) return null

  return (
    <div className="mt-5.5 rounded-[32px] bg-organic-surface p-7">
      <h2 className="text-lg font-semibold">
        Mastery{' '}
        <span className="text-[12.5px] font-normal opacity-55">
          across {mastery.completed_attempts} completed {mastery.completed_attempts === 1 ? 'quiz' : 'quizzes'}
        </span>
      </h2>
      <ul className="mt-4 flex flex-col gap-2">
        {mastery.buckets.map((bucket) => {
          const percent = Math.round(bucket.average_score * 100)
          // First and last point of the bucket's own history — enough to say
          // "improving" or "slipping" without a charting dependency for a view
          // that usually has two or three data points.
          const first = bucket.history[0]
          const last = bucket.history[bucket.history.length - 1]
          const delta = bucket.history.length > 1 ? Math.round((last.average_score - first.average_score) * 100) : null
          return (
            <li key={bucket.subsystem_key} className="flex items-center gap-3 text-sm">
              <span className="w-28 shrink-0 truncate sm:w-44">{bucket.name}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-organic-divider">
                <span
                  className="block h-full rounded-full bg-organic-accent-700"
                  style={{ width: `${percent}%` }}
                />
              </span>
              <span className="w-10 shrink-0 text-right tabular-nums">{percent}%</span>
              {/* The trend column is the first thing to go on a phone — the
              score itself is the number being read. */}
              <span className="hidden w-24 shrink-0 text-right opacity-70 tabular-nums sm:block">
                {delta === null ? `${bucket.answered} answered` : `${delta >= 0 ? '+' : ''}${delta} pts`}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function UpdateStatusBadge({ status }: { status: UpdateStatus }) {
  if (status.status === 'stale') {
    return (
      <p className="text-sm">
        <span className="font-semibold text-organic-danger">New commits on the remote.</span>{' '}
        This guide describes an earlier version of the code.
      </p>
    )
  }
  if (status.status === 'up_to_date') {
    return <p className="text-sm opacity-70">Up to date with the remote.</p>
  }
  return <p className="text-sm opacity-70">{UNKNOWN_REASONS[status.reason ?? ''] ?? 'Update status unknown'}</p>
}

/** The mockup's chip row: one per named section, with deep-dives collapsed into
 *  a single count — a guide can carry several and listing each title turns the
 *  row into a wall. */
function sectionChips(guide: StudyGuide): string[] {
  const deepDives = guide.sections.filter((section) => section.section_type === 'deep_dive').length
  const chips = guide.sections
    .filter((section) => section.section_type !== 'deep_dive')
    .map((section) => section.title)
  if (deepDives > 0) chips.push(`${deepDives} deep ${deepDives === 1 ? 'dive' : 'dives'}`)
  return chips
}

function QuizHistory({ attempts }: { attempts: AttemptSummary[] | null }) {
  return (
    <section className="rounded-[32px] bg-organic-surface p-7">
      <h2 className="text-lg font-semibold">Quiz history</h2>
      {attempts === null && <p className="mt-3.5 text-[13px] opacity-55">Loading…</p>}
      {attempts !== null && attempts.length === 0 && (
        <p className="mt-3.5 text-[13px] leading-relaxed opacity-70">
          No completed quizzes yet. Scores and dates land here once you finish one.
        </p>
      )}
      {attempts !== null && attempts.length > 0 && (
        <ul className="mt-3.5 flex flex-col">
          {attempts.map((attempt) => {
            const percent = Math.round(attempt.score * 100)
            return (
              <li key={attempt.id} className="border-t border-organic-divider first:border-t-0">
                <Link
                  to={`/attempts/${attempt.id}`}
                  className="-mx-2 flex items-center gap-3 rounded-2xl px-2 py-3 text-sm hover:bg-[color-mix(in_srgb,var(--color-organic-text)_5%,transparent)]"
                >
                  <span className="min-w-0 flex-1 truncate">{sittingTimestamp(attempt.completed_at)}</span>
                  <span className="shrink-0 text-[12.5px] opacity-55">
                    {attempt.question_count} {attempt.question_count === 1 ? 'question' : 'questions'}
                  </span>
                  <Tag variant={attempt.score >= PASSING_SCORE ? 'accent-2' : 'outline'} className="tabular-nums">
                    {percent}%
                  </Tag>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
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
  const [mastery, setMastery] = useState<Mastery | null>(null)
  const [attempts, setAttempts] = useState<AttemptSummary[] | null>(null)
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null)
  const [checkingUpdates, setCheckingUpdates] = useState(false)
  const [updateError, setUpdateError] = useState<string | null>(null)

  useBreadcrumb(repo?.display_name)

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
  // Only the *cached* result on load — the backend's GET does no network I/O,
  // so this can't make the page wait on a third-party host. Actually asking the
  // remote is the button below (#62).
  useEffect(() => {
    if (status !== 'ready' || !repoId) return
    getUpdateStatus(repoId)
      .then(setUpdateStatus)
      .catch(() => {
        // Not being able to read a cached staleness answer shouldn't take the
        // page down with it — the guide is still perfectly readable.
      })
  }, [status, repoId])

  // Reloaded whenever a quiz result could have changed it — the value of this
  // view is watching it move, so a stale number is worse than none.
  useEffect(() => {
    if (status !== 'ready' || !repoId) return
    getMastery(repoId)
      .then(setMastery)
      .catch(() => {
        // A missing mastery summary shouldn't take the page down — everything
        // else on it still works.
      })
  }, [status, repoId, quiz])

  // Same reload trigger as mastery, for the same reason: finishing a quiz is
  // the one event that adds a row here.
  useEffect(() => {
    if (status !== 'ready' || !repoId) return
    listRepoAttempts(repoId)
      .then(setAttempts)
      .catch(() => {
        // An unreachable history panel shouldn't take the page down; it stays
        // in its loading state rather than claiming "no quizzes yet".
      })
  }, [status, repoId, quiz])

  function handleCheckUpdates() {
    if (!repoId) return
    setCheckingUpdates(true)
    setUpdateError(null)
    checkForUpdates(repoId)
      .then(setUpdateStatus)
      .catch((err) => setUpdateError(err instanceof ApiError ? err.message : 'Could not check for updates.'))
      .finally(() => setCheckingUpdates(false))
  }

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
      <main className="mx-auto max-w-5xl p-7">
        <p className="text-sm opacity-70">Loading…</p>
      </main>
    )
  }

  if (loadError || !repo) {
    return (
      <main className="mx-auto max-w-5xl p-7">
        <div className="rounded-2xl bg-organic-danger-bg p-3.5">
          <p className="text-sm text-organic-danger">{loadError ?? 'Repository not found.'}</p>
        </div>
      </main>
    )
  }

  const isReady = status === 'ready'

  return (
    <main className="mx-auto max-w-5xl p-7">
      <Link to="/" className="text-[13px] opacity-60 hover:underline">
        ← Back to shelf
      </Link>

      {/* Title and the two things you'd come here to do, on one line — the
      mockup's header treatment. The primary action is the guide; "view raw
      analysis" is the debug affordance ui-spec.md §6.4 asks for, so it stays
      secondary and reveals its panel at the foot of the page. */}
      <div className="mt-2 mb-5.5 flex flex-wrap items-end justify-between gap-x-4 gap-y-3.5">
        <div className="min-w-0">
          <h1 className="mb-1 text-[34px] leading-tight">{repo.display_name}</h1>
          <p className="truncate font-mono text-xs opacity-55">
            {repo.source_uri}
            {snapshot?.commit_hash && ` · @ ${snapshot.commit_hash.slice(0, 7)}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <Button variant="secondary" onClick={() => setShowRaw((visible) => !visible)} aria-expanded={showRaw}>
            {showRaw ? 'Hide raw analysis' : 'View raw analysis'}
          </Button>
          {isReady && guide && (
            <Link to={`/study-guides/${guide.id}`}>
              <Button>Open study guide</Button>
            </Link>
          )}
        </div>
      </div>

      <div className="rounded-[32px] bg-organic-surface p-7">
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

      {/* Below the stepper, not above it: the mockup's reading order is
      title → indexing → panels, and "up to date with the remote" is a
      footnote to the indexing state rather than a headline of its own. It
      still lands above the study guide, which is what matters when it's the
      "these commits are newer than this guide" version. */}
      {isReady && updateStatus && (
        <div className="mt-5.5 flex flex-wrap items-center gap-3 rounded-2xl border border-organic-divider p-3.5">
          <UpdateStatusBadge status={updateStatus} />
          {updateStatus.reason !== 'zip_upload' && (
            <Button variant="secondary" onClick={handleCheckUpdates} disabled={checkingUpdates}>
              {checkingUpdates ? 'Checking…' : 'Check for updates'}
            </Button>
          )}
          {/* #73: the banner used to say "new commits on the remote" and offer
          nothing to do about it. Re-index is the same mechanism as the failure
          Retry, but a different intent, so it gets its own wording. */}
          {updateStatus.status === 'stale' && (
            <Button onClick={handleRetry} disabled={reindexing}>
              {reindexing ? 'Re-indexing…' : 'Re-index'}
            </Button>
          )}
          {updateError && <p className="text-sm text-organic-danger">{updateError}</p>}
          {/* The stepper's own retryError only renders inside its failure
          card, which isn't showing for a ready repo — so a refused re-index
          would otherwise fail silently here. */}
          {reindexError && <p className="text-sm text-organic-danger">{reindexError}</p>}
        </div>
      )}

      {isReady && (
        // Two panels side by side once there's room: what this repo produced,
        // and how you've done on it. They stack on narrow viewports.
        <div className="mt-5.5 grid items-start gap-5.5 lg:grid-cols-[1.55fr_1fr]">
          <section className="rounded-[32px] bg-organic-surface p-7">
            <h2 className="text-lg font-semibold">Study guide</h2>

            {guideError && (
              <div className="mt-3.5 rounded-2xl bg-organic-danger-bg p-3.5">
                <p className="text-sm text-organic-danger">{guideError}</p>
              </div>
            )}

            {guide && (
              <>
                <p className="mt-1.5 text-[13px] opacity-70">
                  {guide.sections.length} {guide.sections.length === 1 ? 'section' : 'sections'}
                  {' · '}
                  {guide.sections.filter((section) => section.diagram_mermaid).length} diagrams
                  {' · '}
                  {guide.sections.reduce((total, section) => total + section.citations.length, 0)} citations
                  {' · generated '}
                  {relativeTime(guide.generated_at)}
                </p>

                <div className="mt-3.5 flex flex-wrap gap-2">
                  {sectionChips(guide).map((chip) => (
                    <Tag key={chip} className="text-[12px]">
                      {chip}
                    </Tag>
                  ))}
                </div>
              </>
            )}

            <div className="mt-5.5 flex flex-wrap items-center gap-3">
              {guide && (
                <Link to={`/study-guides/${guide.id}`}>
                  <Button>Read it</Button>
                </Link>
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
                    {quizPending ? 'Generating quiz…' : 'Generate quiz'}
                  </Button>
                </>
              )}
              {quiz?.status === 'ready' && (
                <Link to={`/quizzes/${quiz.id}`}>
                  <Button variant="secondary">Take quiz</Button>
                </Link>
              )}
            </div>

            {quiz?.status === 'failed' && (
              <div className="mt-3.5 rounded-2xl bg-organic-danger-bg p-3.5">
                <p className="text-sm text-organic-danger">
                  Quiz generation failed.{' '}
                  <button type="button" onClick={handleGenerateQuiz} className="font-semibold underline">
                    Try again
                  </button>
                </p>
              </div>
            )}
            {quizError && (
              <div className="mt-3.5 rounded-2xl bg-organic-danger-bg p-3.5">
                <p className="text-sm text-organic-danger">{quizError}</p>
              </div>
            )}
          </section>

          <QuizHistory attempts={attempts} />
        </div>
      )}

      {isReady && mastery && <MasterySection mastery={mastery} />}

      {showRaw && (
        <pre className="mt-5.5 max-h-96 overflow-auto rounded-2xl bg-organic-neutral-900 p-4 text-xs text-organic-neutral-200">
          {JSON.stringify(snapshot, null, 2)}
        </pre>
      )}
    </main>
  )
}

export default RepoDetail
