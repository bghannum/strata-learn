import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isAbortError, pollQuiz, PollTimeoutError, type Quiz } from '../client'

function quizResponse(status: Quiz['status']): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({ id: 'quiz-1', repo_id: 'r', study_guide_id: 'g', status, questions: [] }),
  } as Response
}

describe('pollQuiz', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('resolves once the quiz reaches a terminal status', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(quizResponse('generating'))
      .mockResolvedValueOnce(quizResponse('ready'))
    vi.stubGlobal('fetch', fetchMock)

    const promise = pollQuiz('quiz-1', { intervalMs: 1000 })
    await vi.advanceTimersByTimeAsync(1000)
    const quiz = await promise

    expect(quiz.status).toBe('ready')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('rejects with PollTimeoutError once the deadline passes without a terminal status', async () => {
    const fetchMock = vi.fn().mockResolvedValue(quizResponse('generating'))
    vi.stubGlobal('fetch', fetchMock)

    const promise = pollQuiz('quiz-1', { intervalMs: 1000, timeoutMs: 3000 })
    const assertion = expect(promise).rejects.toBeInstanceOf(PollTimeoutError)
    await vi.advanceTimersByTimeAsync(4000)
    await assertion
  })

  it('enforces the deadline independently even when a single request never settles', async () => {
    // Found via Codex's PR #43 review: the deadline was previously only
    // checked inside the success callback, so a hung (or merely slow)
    // getQuiz call meant nothing ever timed out — polling wasn't actually
    // bounded. A hung fetch here proves the deadline fires on its own.
    const fetchMock = vi.fn().mockReturnValue(new Promise<Response>(() => {}))
    vi.stubGlobal('fetch', fetchMock)

    const promise = pollQuiz('quiz-1', { timeoutMs: 3000 })
    const assertion = expect(promise).rejects.toBeInstanceOf(PollTimeoutError)
    await vi.advanceTimersByTimeAsync(3000)
    await assertion

    // The hung request's own signal must be aborted so the browser actually
    // cancels it, not just so pollQuiz discards whatever it eventually returns.
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.signal as AbortSignal).aborted).toBe(true)
  })

  it('stops polling and rejects when the signal aborts', async () => {
    const fetchMock = vi.fn().mockResolvedValue(quizResponse('generating'))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    const promise = pollQuiz('quiz-1', { intervalMs: 1000, signal: controller.signal })
    controller.abort()

    let caught: unknown
    try {
      await promise
    } catch (err) {
      caught = err
    }
    expect(isAbortError(caught)).toBe(true)

    // No further timer chain should keep running after abort — an in-flight
    // fetch resolving late must not schedule another one (see the
    // signal?.aborted guard inside tick()'s .then callback).
    const callsAtAbort = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchMock.mock.calls.length).toBe(callsAtAbort)
  })

  it('rejects immediately for an already-aborted signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(quizResponse('generating'))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    controller.abort()

    await expect(pollQuiz('quiz-1', { signal: controller.signal })).rejects.toSatisfy(isAbortError)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
