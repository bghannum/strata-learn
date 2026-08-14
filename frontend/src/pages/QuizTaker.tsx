import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  completeAttempt,
  createAttempt,
  getAttempt,
  getQuiz,
  submitAnswer,
  type AnswerResult,
  type Attempt,
  type Quiz,
} from '../api/client'

function QuizTaker() {
  const { quizId } = useParams<{ quizId: string }>()
  const navigate = useNavigate()

  const [quiz, setQuiz] = useState<Quiz | null>(null)
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [index, setIndex] = useState(0)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [answerText, setAnswerText] = useState('')
  const [result, setResult] = useState<AnswerResult | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [finishing, setFinishing] = useState(false)

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
        const answeredIds = new Set(results.questions.filter((q) => q.score !== null).map((q) => q.question_id))
        let resumeIndex = 0
        while (resumeIndex < fetchedQuiz.questions.length && answeredIds.has(fetchedQuiz.questions[resumeIndex].id)) {
          resumeIndex += 1
        }
        setIndex(Math.min(resumeIndex, fetchedQuiz.questions.length - 1))
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : (err as Error).message || 'Could not start this quiz.'))
      .finally(() => setLoading(false))
  }, [quizId])

  if (loading) {
    return (
      <main className="mx-auto max-w-2xl p-6">
        <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>
      </main>
    )
  }

  if (error || !quiz || !attempt) {
    return (
      <main className="mx-auto max-w-2xl p-6">
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
          {error ?? 'Quiz not found.'}
        </p>
      </main>
    )
  }

  const question = quiz.questions[index]
  const isLast = index === quiz.questions.length - 1

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
      .then(setResult)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not submit that answer.'))
      .finally(() => setSubmitting(false))
  }

  function handleNext() {
    setIndex((i) => i + 1)
    setSelectedIndex(null)
    setAnswerText('')
    setResult(null)
  }

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

  return (
    <main className="mx-auto max-w-2xl p-6">
      <p className="text-sm text-gray-500 dark:text-gray-400">
        Question {index + 1} of {quiz.questions.length}
      </p>
      <h1 className="mt-1 text-lg font-semibold whitespace-pre-wrap text-gray-900 dark:text-gray-100">
        {question.prompt}
      </h1>

      {question.question_type === 'mcq' && question.choices && (
        <fieldset className="mt-4 space-y-2" disabled={!!result}>
          {question.choices.map((choice, choiceIndex) => (
            <label
              key={choiceIndex}
              className="flex cursor-pointer items-start gap-2 rounded-md border border-gray-200 p-3 text-sm text-gray-800 has-checked:border-blue-500 has-checked:bg-blue-50 dark:border-gray-700 dark:text-gray-200 dark:has-checked:bg-blue-950"
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

      {question.question_type === 'fill_blank' && (
        <div className="mt-4">
          <input
            type="text"
            value={answerText}
            onChange={(event) => setAnswerText(event.target.value)}
            disabled={!!result}
            placeholder="Your answer"
            className="w-full rounded-md border border-gray-300 p-2 text-sm text-gray-900 disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
          />
        </div>
      )}

      {error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">{error}</p>
      )}

      {!result && (
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting || (question.question_type === 'mcq' ? selectedIndex === null : !answerText.trim())}
          className="mt-6 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting ? 'Submitting…' : 'Submit Answer'}
        </button>
      )}

      {result && (
        <div className="mt-6 rounded-md border border-gray-200 p-4 dark:border-gray-700">
          <p className={`text-sm font-medium ${result.score > 0 ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
            {result.score >= 1 ? 'Correct' : result.score > 0 ? 'Partially correct' : 'Incorrect'}
          </p>
          <p className="mt-1 text-sm text-gray-700 dark:text-gray-300">{result.feedback}</p>
          {result.correct_answer && result.score < 1 && (
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              Correct answer: <span className="font-medium">{result.correct_answer}</span>
            </p>
          )}

          {isLast ? (
            <button
              type="button"
              onClick={handleFinish}
              disabled={finishing}
              className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {finishing ? 'Finishing…' : 'Finish Quiz'}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleNext}
              className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
            >
              Next Question
            </button>
          )}
        </div>
      )}
    </main>
  )
}

export default QuizTaker
