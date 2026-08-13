import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, getAttempt, getQuiz, type AttemptResults, type Quiz } from '../api/client'

function scoreLabel(score: number | null): string {
  if (score === null) return 'In progress'
  return `${Math.round(score * 100)}%`
}

function AttemptResultsPage() {
  const { attemptId } = useParams<{ attemptId: string }>()
  const [results, setResults] = useState<AttemptResults | null>(null)
  const [quiz, setQuiz] = useState<Quiz | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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

  if (loading) {
    return (
      <main className="mx-auto max-w-2xl p-6">
        <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>
      </main>
    )
  }

  if (error || !results) {
    return (
      <main className="mx-auto max-w-2xl p-6">
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
          {error ?? 'Results not found.'}
        </p>
      </main>
    )
  }

  return (
    <main className="mx-auto max-w-2xl p-6">
      {quiz && (
        <Link to={`/study-guides/${quiz.study_guide_id}`} className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Back to Study Guide
        </Link>
      )}
      <h1 className="mt-2 text-xl font-semibold text-gray-900 dark:text-gray-100">Quiz Results</h1>
      <p className="mt-1 text-3xl font-bold text-gray-900 dark:text-gray-100">{scoreLabel(results.score)}</p>

      <div className="mt-6 space-y-4">
        {results.questions.map((question, questionIndex) => (
          <div key={question.question_id} className="rounded-md border border-gray-200 p-4 dark:border-gray-700">
            <p className="text-xs font-medium tracking-wide text-gray-400 uppercase">Question {questionIndex + 1}</p>
            <p className="mt-1 text-sm whitespace-pre-wrap text-gray-900 dark:text-gray-100">{question.prompt}</p>

            {question.score !== null ? (
              <>
                <p
                  className={`mt-2 text-sm font-medium ${
                    question.score >= 1
                      ? 'text-green-700 dark:text-green-400'
                      : question.score > 0
                        ? 'text-yellow-700 dark:text-yellow-400'
                        : 'text-red-700 dark:text-red-400'
                  }`}
                >
                  {Math.round((question.score ?? 0) * 100)}%
                </p>
                {question.feedback && <p className="mt-1 text-sm text-gray-700 dark:text-gray-300">{question.feedback}</p>}
              </>
            ) : (
              <p className="mt-2 text-sm text-gray-400 dark:text-gray-500">Not answered</p>
            )}

            <p className="mt-2 font-mono text-xs text-gray-400 dark:text-gray-500">
              {question.file_path}:{question.line_start}-{question.line_end}
            </p>
          </div>
        ))}
      </div>
    </main>
  )
}

export default AttemptResultsPage
