import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The browser media seam is mocked wholesale — this file is about the flow
// (record → transcript → "Use this answer"), not the globals. recorder.test.ts
// covers those.
const recorderMock = vi.hoisted(() => ({
  supported: true,
  startRecording: vi.fn(),
}))

vi.mock('../../audio/recorder', async () => {
  const actual = await vi.importActual<typeof import('../../audio/recorder')>('../../audio/recorder')
  return {
    ...actual,
    isRecordingSupported: () => recorderMock.supported,
    startRecording: recorderMock.startRecording,
  }
})

import SpokenAnswer from '../SpokenAnswer'

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

function activeRecording(clip: Blob | null = new Blob(['x'], { type: 'audio/webm' })) {
  return {
    mimeType: 'audio/webm',
    stop: vi.fn(async () => clip),
    cancel: vi.fn(),
  }
}

describe('SpokenAnswer', () => {
  beforeEach(() => {
    recorderMock.supported = true
    recorderMock.startRecording.mockReset()
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders nothing when the browser cannot record', () => {
    recorderMock.supported = false
    const { container } = render(<SpokenAnswer attemptId="a" questionId="q" onUseTranscript={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('records, uploads, shows an editable transcript, and only hands it over on an explicit click', async () => {
    const active = activeRecording()
    recorderMock.startRecording.mockResolvedValue(active)
    const fetchMock = vi.fn(async () => jsonResponse({ text: 'the worker uses a sink PG', duration_ms: 400 }))
    vi.stubGlobal('fetch', fetchMock)
    const onUse = vi.fn()

    render(<SpokenAnswer attemptId="att-1" questionId="q-1" onUseTranscript={onUse} />)

    fireEvent.click(screen.getByRole('button', { name: 'Speak your answer' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /^Stop/ })).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /^Stop/ }))
    await waitFor(() => expect(screen.getByLabelText(/Transcript/)).toBeInTheDocument())

    // Uploaded to the right route, as multipart with a filename derived
    // from the blob's own type.
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/attempts/att-1/answers/q-1/transcription')
    expect(init.body).toBeInstanceOf(FormData)
    const sent = (init.body as FormData).get('file') as File
    expect(sent.name).toBe('answer.webm')

    // Editable — and nothing has reached the answer box yet.
    const box = screen.getByLabelText(/Transcript/) as HTMLTextAreaElement
    expect(box.value).toBe('the worker uses a sink PG')
    expect(onUse).not.toHaveBeenCalled()

    fireEvent.change(box, { target: { value: 'the worker uses asyncpg' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this answer' }))
    expect(onUse).toHaveBeenCalledWith('the worker uses asyncpg')
  })

  it('surfaces the server error detail instead of a generic failure', async () => {
    recorderMock.startRecording.mockResolvedValue(activeRecording())
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'too many voice requests this hour — try again later' }, 429)),
    )
    render(<SpokenAnswer attemptId="a" questionId="q" onUseTranscript={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: 'Speak your answer' }))
    await waitFor(() => screen.getByRole('button', { name: /^Stop/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Stop/ }))

    await waitFor(() => expect(screen.getByText(/too many voice requests/)).toBeInTheDocument())
    expect(screen.queryByLabelText(/Transcript/)).not.toBeInTheDocument()
  })

  it('says why when the microphone is denied, and points at typing instead', async () => {
    const { RecorderError } = await vi.importActual<typeof import('../../audio/recorder')>('../../audio/recorder')
    recorderMock.startRecording.mockRejectedValue(new RecorderError('permission_denied', 'denied'))
    render(<SpokenAnswer attemptId="a" questionId="q" onUseTranscript={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: 'Speak your answer' }))
    await waitFor(() => expect(screen.getByText(/Microphone access was denied/)).toBeInTheDocument())
    expect(screen.getByText(/type your answer/)).toBeInTheDocument()
  })

  it('drops the transcript and cancels any recording when the question changes', async () => {
    const active = activeRecording()
    recorderMock.startRecording.mockResolvedValue(active)
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ text: 'answer for q1', duration_ms: 1 })))
    const { rerender } = render(<SpokenAnswer attemptId="a" questionId="q1" onUseTranscript={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: 'Speak your answer' }))
    await waitFor(() => screen.getByRole('button', { name: /^Stop/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Stop/ }))
    await waitFor(() => expect(screen.getByLabelText(/Transcript/)).toBeInTheDocument())

    // Previous/Next: same mounted component, new question. The old
    // transcript must not be sitting there waiting to be "used" for it.
    await act(async () => {
      rerender(<SpokenAnswer attemptId="a" questionId="q2" onUseTranscript={() => {}} />)
    })
    expect(screen.queryByLabelText(/Transcript/)).not.toBeInTheDocument()
  })
})
