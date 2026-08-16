import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  completeAttempt,
  createAttempt,
  getAttempt,
  getQuiz,
  getVoiceCapabilities,
  submitAnswer,
  type AnswerResult,
  type Attempt,
  type Quiz,
} from '../api/client'
import RubricCoverage from '../components/RubricCoverage'
import SpokenAnswer from '../components/SpokenAnswer'
import Button from '../components/ui/Button'
import { Input, Textarea } from '../components/ui/Field'
import Tag from '../components/ui/Tag'

// #35: the earlier-answered question's own submission + graded result,
// cached client-side as the user moves forward — GET /attempts/{id}
// deliberately withholds correct_index/correct_answer for an in_progress
// attempt (so a savvy user can't fetch upcoming answers before reaching
// them), so a "Previous" action can't just re-fetch this from the backend.
// Scoped to *this session's* forward progress: a question answered before a
// reload isn't recoverable this way either, for the same reason.
interface CachedAnswer {
  result: AnswerResult
  selectedIndex: number | null
  answerText: string
}

function QuizTaker() {
  const { quizId } = useParams<{ quizId: string }>()
  const navigate = useNavigate()

  const [quiz, setQuiz] = useState<Quiz | null>(null)
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [index, setIndex] = useState(0)
  const [readyToFinish, setReadyToFinish] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [answerText, setAnswerText] = useState('')
  const [result, setResult] = useState<AnswerResult | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [finishing, setFinishing] = useState(false)
  const [answeredCache, setAnsweredCache] = useState<Record<string, CachedAnswer>>({})
  // Whether this deployment can transcribe at all. Read once; the mic
  // control isn't rendered until this says yes, so a learner is never shown
  // a button that 503s (ADR-010). A failed read just means no mic button —
  // the quiz works exactly as before.
  const [canTranscribe, setCanTranscribe] = useState(false)

  useEffect(() => {
    getVoiceCapabilities()
      .then((caps) => setCanTranscribe(caps.transcription))
      .catch(() => {
        // Voice is an addition to a text-first flow; its absence is not an error.
      })
  }, [])

  useEffect(() => {
    if (!quizId) return
    let fetchedQuiz: Quiz

    getQuiz(quizId)
      .then((quiz) => {
        fetchedQuiz = quiz
        setQuiz(quiz)
        if (quiz.status !== 'ready') {
          throw new Error('This quiz is not ready yet.')
        }
        return createAttempt(quiz.id)
      })
      .then((createdAttempt) => {
        setAttempt(createdAttempt)
        return getAttempt(createdAttempt.id)
      })
      .then((results) => {
        // createAttempt is idempotent per (user, quiz) — a reload, a second
        // tab, or React StrictMode's double mount-effect invocation can all
        // resume the same in-progress attempt rather than starting a fresh
        // one. Skip past whatever's already been graded so a resume doesn't
        // re-ask (and doesn't lose) already-answered questions.
        //
        // q.answered, not q.score !== null — found via Codex's PR #50
        // review: in end_of_quiz mode the backend now withholds score
        // entirely pre-completion (#37), so score alone can no longer tell
        // "answered" from "unanswered". answered is a separate signal for
        // exactly this.
        const answeredIds = new Set(results.questions.filter((q) => q.answered).map((q) => q.question_id))
        let resumeIndex = 0
        while (resumeIndex < fetchedQuiz.questions.length && answeredIds.has(fetchedQuiz.questions[resumeIndex].id)) {
          resumeIndex += 1
        }
        if (resumeIndex >= fetchedQuiz.questions.length) {
          // Every question already has a graded submission (e.g. the page
          // reloaded after the last answer saved but before Finish was
          // clicked) — go straight to a Finish prompt instead of clamping
          // back into the last question and forcing a resubmit, which would
          // also repeat a paid judge call for a concept-mode question
          // (found via the Phase 5 Codex review, second pass).
          setReadyToFinish(true)
        } else {
          setIndex(resumeIndex)
        }
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : (err as Error).message || 'Could not start this quiz.'))
      .finally(() => setLoading(false))
  }, [quizId])

  if (loading) {
    return (
      <main className="mx-auto max-w-2xl p-7">
        <p className="text-sm opacity-70">Loading…</p>
      </main>
    )
  }

  if (error || !quiz || !attempt) {
    return (
      <main className="mx-auto max-w-2xl p-7">
        <div className="rounded-2xl bg-organic-danger-bg p-3.5">
          <p className="text-sm text-organic-danger">{error ?? 'Quiz not found.'}</p>
        </div>
      </main>
    )
  }

  const isImmediate = quiz.feedback_mode === 'immediate'

  function handleFinish() {
    if (!attempt) return
    setFinishing(true)
    completeAttempt(attempt.id)
      .then(() => navigate(`/attempts/${attempt.id}`))
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : 'Could not finish this quiz.')
        setFinishing(false)
      })
  }

  if (readyToFinish) {
    return (
      <main className="mx-auto max-w-2xl p-7">
        <p className="text-sm opacity-70">You've already answered every question.</p>
        {error && (
          <div className="mt-3 rounded-2xl bg-organic-danger-bg p-3.5">
            <p className="text-sm text-organic-danger">{error}</p>
          </div>
        )}
        <Button className="mt-4" onClick={handleFinish} disabled={finishing}>
          {finishing ? 'Finishing…' : 'Finish Quiz'}
        </Button>
      </main>
    )
  }

  const question = quiz.questions[index]
  const isLast = index === quiz.questions.length - 1

  function goToIndex(targetIndex: number) {
    setIndex(targetIndex)
    const targetQuestion = quiz!.questions[targetIndex]
    const cached = targetQuestion ? answeredCache[targetQuestion.id] : undefined
    setSelectedIndex(cached?.selectedIndex ?? null)
    setAnswerText(cached?.answerText ?? '')
    // end_of_quiz mode never reveals a per-question result, even one
    // reached again via Previous — only immediate mode restores it.
    setResult(isImmediate ? (cached?.result ?? null) : null)
  }

  function handlePrevious() {
    if (index === 0) return
    goToIndex(index - 1)
  }

  function handleNext() {
    goToIndex(index + 1)
  }

  function afterGraded(res: AnswerResult, chosenSelectedIndex: number | null, chosenAnswerText: string) {
    setAnsweredCache((cache) => ({
      ...cache,
      [question.id]: { result: res, selectedIndex: chosenSelectedIndex, answerText: chosenAnswerText },
    }))
    if (isImmediate) {
      setResult(res)
      return
    }
    // #37, end_of_quiz mode: submit doubles as "advance" since there's no
    // per-question result to review in between — straight to Finish on the
    // last question rather than bouncing through the readyToFinish prompt.
    if (isLast) {
      handleFinish()
    } else {
      goToIndex(index + 1)
    }
  }

  function handleSubmit() {
    if (!attempt || !question) return
    const answer =
      question.question_type === 'mcq'
        ? selectedIndex !== null
          ? { selected_index: selectedIndex }
          : null
        : answerText.trim()
          ? { answer_text: answerText.trim() }
          : null
    if (!answer) return

    setSubmitting(true)
    setError(null)
    submitAnswer(attempt.id, question.id, answer)
      .then((res) => afterGraded(res, selectedIndex, answerText))
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not submit that answer.'))
      .finally(() => setSubmitting(false))
  }

  const isWritten = question.question_type === 'fill_blank' || question.question_type === 'short_answer'
  const canSubmit = question.question_type === 'mcq' ? selectedIndex !== null : !!answerText.trim()
  const showingResult = isImmediate && !!result
  const primaryLabel = showingResult
    ? isLast
      ? 'Finish Quiz'
      : 'Next Question'
    : submitting || finishing
      ? isImmediate
        ? 'Submitting…'
        : isLast
          ? 'Finishing…'
          : 'Submitting…'
      : isImmediate
        ? 'Submit Answer'
        : isLast
          ? 'Finish Quiz'
          : 'Next Question'

  function handlePrimaryAction() {
    if (showingResult) {
      if (isLast) handleFinish()
      else handleNext()
    } else {
      handleSubmit()
    }
  }

  return (
    <main className="mx-auto max-w-2xl p-7">
      <div className="mb-2.5 flex items-center gap-3.5">
        <p className="text-[13px] opacity-60">
          Question {index + 1} of {quiz.questions.length}
        </p>
        <Tag variant="neutral" className="ml-auto">
          {isImmediate ? 'As I go' : 'End of quiz'}
        </Tag>
      </div>
      <div className="mb-6.5 h-1.5 overflow-hidden rounded-full bg-organic-neutral-300">
        <div
          className="h-full rounded-full bg-organic-accent-700 transition-all"
          style={{ width: `${((index + 1) / quiz.questions.length) * 100}%` }}
        />
      </div>

      <div className="rounded-[32px] bg-organic-surface p-7.5">
        <h1 className="text-xl leading-snug font-semibold whitespace-pre-wrap">{question.prompt}</h1>

        {question.question_type === 'mcq' && question.choices && (
          <fieldset className="mt-4.5 flex flex-col gap-2.5" disabled={!!result}>
            {question.choices.map((choice, choiceIndex) => (
              <label
                key={choiceIndex}
                className="flex cursor-pointer items-start gap-2.5 rounded-2xl border border-organic-divider p-3.5 text-sm has-[:checked]:border-organic-accent-700 has-[:checked]:bg-organic-accent-100"
              >
                <input
                  type="radio"
                  name="choice"
                  checked={selectedIndex === choiceIndex}
                  onChange={() => setSelectedIndex(choiceIndex)}
                  className="mt-0.5"
                />
                {choice}
              </label>
            ))}
          </fieldset>
        )}

        {isWritten && (
          <div className="mt-4.5">
            {question.question_type === 'short_answer' ? (
              <>
                {/* A paragraph, not a term: the question asks how or why, and
                the judge grades ideas against a rubric — so the box invites a
                few sentences and says so. */}
                <Textarea
                  value={answerText}
                  onChange={(event) => setAnswerText(event.target.value)}
                  disabled={!!result}
                  placeholder="Answer in a sentence or three, in your own words…"
                  className="min-h-[110px]"
                  aria-label="Your answer"
                />
                <p className="mt-1.5 text-[12px] opacity-60">
                  Explain the reasoning — you're graded on the ideas, not the wording.
                </p>
              </>
            ) : (
              <Input
                type="text"
                value={answerText}
                onChange={(event) => setAnswerText(event.target.value)}
                disabled={!!result}
                placeholder="Your answer"
              />
            )}
            {/* The transcript never goes anywhere on its own: "Use this
            answer" copies it into the same answerText the field above is
            bound to, and the ordinary Submit button grades it. Keyed on
            question.id so a transcript can't bleed across Previous/Next. */}
            {canTranscribe && attempt && (
              <SpokenAnswer
                attemptId={attempt.id}
                questionId={question.id}
                onUseTranscript={setAnswerText}
                disabled={!!result}
              />
            )}
          </div>
        )}

        {error && (
          <div className="mt-3.5 rounded-2xl bg-organic-danger-bg p-3.5">
            <p className="text-sm text-organic-danger">{error}</p>
          </div>
        )}

        {/* showingResult implies isImmediate, which server-side means the
        backend actually revealed score/feedback (see AnswerResult's client.ts
        comment) — score is never really null here, ?? 0 is just for
        TypeScript's benefit since it can't see that server-side guarantee. */}
        {showingResult && result && (
          <div className="mt-5.5 rounded-2xl bg-organic-bg p-4.5">
            <p
              className={`text-sm font-semibold ${(result.score ?? 0) > 0 ? 'text-organic-accent-2-700' : 'text-organic-danger'}`}
            >
              {(result.score ?? 0) >= 1 ? 'Correct' : (result.score ?? 0) > 0 ? 'Partially correct' : 'Incorrect'}
              {question.question_type === 'short_answer' && result.rubric_hits && (
                <span className="ml-2 font-normal opacity-70">
                  {result.rubric_hits.filter(Boolean).length} of {result.rubric_hits.length} key points
                </span>
              )}
            </p>
            <p className="mt-1 text-sm opacity-80">{result.feedback}</p>
            {result.rubric && result.rubric.length > 0 && (
              <div className="mt-3">
                <RubricCoverage rubric={result.rubric} hits={result.rubric_hits} />
              </div>
            )}
            {question.question_type === 'short_answer' && result.correct_answer && (
              <div className="mt-3">
                <p className="text-[10px] font-medium tracking-[0.1em] uppercase opacity-70">A strong answer</p>
                <p className="mt-1 text-sm leading-relaxed opacity-80">{result.correct_answer}</p>
              </div>
            )}
            {question.question_type !== 'short_answer' && result.correct_answer && (result.score ?? 0) < 1 && (
              <p className="mt-1 text-sm opacity-60">
                Correct answer: <span className="font-medium">{result.correct_answer}</span>
              </p>
            )}
          </div>
        )}
      </div>

      <div className="mt-5.5 flex items-center gap-3">
        <Button variant="secondary" onClick={handlePrevious} disabled={index === 0 || submitting || finishing}>
          Previous
        </Button>
        <span className="text-[12.5px] opacity-55">Answers save as you go</span>
        <div className="ml-auto">
          <Button onClick={handlePrimaryAction} disabled={!showingResult && (submitting || finishing || !canSubmit)}>
            {primaryLabel}
          </Button>
        </div>
      </div>
    </main>
  )
}

export default QuizTaker
