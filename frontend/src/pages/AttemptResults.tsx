import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  generateQuiz,
  getAttempt,
  getFeedbackSpeech,
  getQuiz,
  getVoiceCapabilities,
  isAbortError,
  pollQuiz,
  type AttemptResults,
  type Quiz,
} from '../api/client'
import ReadAloudButton from '../components/ReadAloudButton'
import Button from '../components/ui/Button'
import Tag from '../components/ui/Tag'

function scoreLabel(score: number | null): string {
  if (score === null) return 'In progress'
  return `${Math.round(score * 100)}%`
}

function questionBadge(score: number | null): { label: string; variant: 'accent-2' | 'accent' | 'danger' } {
  if (score === null) return { label: 'Not answered', variant: 'danger' }
  if (score >= 1) return { label: 'Correct', variant: 'accent-2' }
  if (score > 0) return { label: 'Partial', variant: 'accent' }
  return { label: 'Incorrect', variant: 'danger' }
}

function AttemptResultsPage() {
  const { attemptId } = useParams<{ attemptId: string }>()
  const navigate = useNavigate()
  const [results, setResults] = useState<AttemptResults | null>(null)
  const [quiz, setQuiz] = useState<Quiz | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Same capability gate as StudyGuideView: no read-aloud control until the
  // deployment says it can speak (ADR-010).
  const [canSpeak, setCanSpeak] = useState(false)

  useEffect(() => {
    getVoiceCapabilities()
      .then((caps) => setCanSpeak(caps.speech))
      .catch(() => {
        // Voice is an addition to a text-first flow; its absence is not an error.
      })
  }, [])
  const [retaking, setRetaking] = useState(false)
  const [retakeError, setRetakeError] = useState<string | null>(null)
  const pollControllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => pollControllerRef.current?.abort()
  }, [])

  useEffect(() => {
    if (!attemptId) return
    getAttempt(attemptId)
      .then((fetchedResults) => {
        setResults(fetchedResults)
        return getQuiz(fetchedResults.quiz_id)
      })
      .then(setQuiz)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load these results.'))
      .finally(() => setLoading(false))
  }, [attemptId])

  // #36: a fresh quiz from the same study guide (new question selection,
  // not a review of the same one) — POST /quizzes/{repo_id}/generate is the
  // same call RepoDetail.tsx's "Generate Quiz" button already makes.
  //
  // Polls until ready before navigating — found via Codex's PR #50 review:
  // generateQuiz's response is a `generating` quiz with no questions yet
  // (the worker hasn't run), and QuizTaker.tsx rejects any non-ready quiz
  // outright with no polling of its own. Navigating immediately landed on
  // "This quiz is not ready yet" while the paid job kept running in the
  // background. Same pollQuiz + AbortController-on-unmount pattern
  // RepoDetail.tsx already uses for this exact situation.
  function handleRetake() {
    if (!quiz) return
    setRetaking(true)
    setRetakeError(null)
    const controller = new AbortController()
    pollControllerRef.current = controller
    generateQuiz(quiz.repo_id)
      .then((newQuiz) => pollQuiz(newQuiz.id, { signal: controller.signal }))
      .then((readyQuiz) => {
        if (readyQuiz.status === 'failed') {
          throw new Error('Quiz generation failed.')
        }
        navigate(`/quizzes/${readyQuiz.id}`)
      })
      .catch((err) => {
        if (isAbortError(err)) return
        setRetakeError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Could not start a new quiz.')
        setRetaking(false)
      })
  }

  if (loading) {
    return (
      <main className="mx-auto max-w-3xl p-7">
        <p className="text-sm opacity-70">Loading…</p>
      </main>
    )
  }

  if (error || !results) {
    return (
      <main className="mx-auto max-w-3xl p-7">
        <div className="rounded-2xl bg-organic-danger-bg p-3.5">
          <p className="text-sm text-organic-danger">{error ?? 'Results not found.'}</p>
        </div>
      </main>
    )
  }

  const pct = results.score === null ? 0 : Math.round(results.score * 100)
  const correctCount = results.questions.filter((q) => (q.score ?? 0) >= 1).length

  return (
    <main className="mx-auto max-w-3xl p-7">
      <div className="flex items-center gap-6.5 rounded-[32px] bg-organic-surface p-7">
        <div
          className="grid size-[104px] shrink-0 place-items-center rounded-full"
          style={{
            background: `conic-gradient(var(--color-organic-accent-700) ${pct * 3.6}deg, var(--color-organic-neutral-300) 0deg)`,
          }}
        >
          <div className="grid size-20 place-items-center rounded-full bg-organic-surface text-center">
            <div>
              <div className="text-xl font-semibold">{scoreLabel(results.score)}</div>
              <div className="text-[10px] opacity-60">
                {correctCount}/{results.questions.length}
              </div>
            </div>
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="mb-1.5 text-2xl leading-tight">Quiz Results</h1>
          <p className="text-sm leading-relaxed opacity-70">
            {results.status === 'completed' ? "Here's how it went." : 'This attempt is still in progress.'}
          </p>
        </div>
        <div className="flex flex-none flex-col gap-2.5">
          <Button onClick={handleRetake} disabled={retaking || !quiz}>
            {retaking ? 'Starting…' : 'Retake'}
          </Button>
          {quiz && (
            <Link to={`/study-guides/${quiz.study_guide_id}`}>
              <Button variant="secondary" className="w-full">
                Back to guide
              </Button>
            </Link>
          )}
        </div>
      </div>
      {retakeError && (
        <div className="mt-3.5 rounded-2xl bg-organic-danger-bg p-3.5">
          <p className="text-sm text-organic-danger">{retakeError}</p>
        </div>
      )}

      <h2 className="mt-7.5 mb-3.5 text-lg font-semibold">Question by question</h2>
      <div className="flex flex-col gap-3">
        {results.questions.map((question, questionIndex) => {
          const badge = questionBadge(question.score)
          return (
            <div key={question.question_id} className="rounded-[28px] bg-organic-surface p-5.5">
              <div className="mb-2.5 flex items-center gap-2.5">
                <Tag variant={badge.variant}>{badge.label}</Tag>
                <span className="text-xs opacity-55">Question {questionIndex + 1}</span>
                <Tag variant="neutral" className="ml-auto">
                  {question.question_type === 'mcq' ? 'Multiple choice' : 'Fill in the blank'}
                </Tag>
              </div>
              <p className="mb-3 text-[15.5px] font-semibold whitespace-pre-wrap">{question.prompt}</p>

              {(question.submitted_answer !== null || question.correct_answer !== null) && (
                <div className="mb-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                  <div className="rounded-2xl bg-organic-neutral-100 p-3.5">
                    <div className="mb-1 text-[10px] tracking-[0.1em] text-organic-neutral-700 uppercase">
                      You said
                    </div>
                    {question.submitted_answer ?? <span className="opacity-55">Not answered</span>}
                  </div>
                  <div className="rounded-2xl bg-organic-accent-2-100 p-3.5">
                    <div className="mb-1 text-[10px] tracking-[0.1em] text-organic-accent-2-700 uppercase">
                      Correct
                    </div>
                    {question.correct_answer ?? <span className="opacity-55">—</span>}
                  </div>
                </div>
              )}

              {question.feedback && (
                <div className="mb-3">
                  <p className="text-sm leading-relaxed opacity-80">{question.feedback}</p>
                  {canSpeak && attemptId && (
                    <div className="mt-1.5">
                      <ReadAloudButton
                        label={`feedback for question ${questionIndex + 1}`}
                        fetchClip={() => getFeedbackSpeech(attemptId, question.question_id)}
                      />
                    </div>
                  )}
                </div>
              )}

              <details className="rounded-2xl border border-organic-divider">
                <summary className="cursor-pointer px-3.5 py-2.5 font-mono text-xs opacity-60">
                  {question.file_path}:{question.line_start}-{question.line_end}
                </summary>
                <div className="border-t border-organic-divider px-3.5 py-3">
                  {question.citation_claim_excerpt && (
                    <p className="text-sm leading-relaxed">{question.citation_claim_excerpt}</p>
                  )}
                  {question.citation_snippet_text && (
                    <pre className="mt-2 overflow-auto rounded-2xl bg-organic-neutral-900 p-3.5 text-xs text-organic-neutral-200">
                      {question.citation_snippet_text}
                    </pre>
                  )}
                </div>
              </details>
            </div>
          )
        })}
      </div>
    </main>
  )
}

export default AttemptResultsPage
