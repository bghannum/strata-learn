import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import ReadAloudButton from '../ReadAloudButton'

// jsdom's HTMLMediaElement.play/pause throw "Not implemented" and it has no
// URL.createObjectURL — both stubbed here, per test file, since this is the
// only component that plays audio.
const created: string[] = []
const revoked: string[] = []

describe('ReadAloudButton', () => {
  let playSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    created.length = 0
    revoked.length = 0
    playSpy = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockImplementation(async function (
      this: HTMLMediaElement,
    ) {
      this.dispatchEvent(new Event('play'))
    })
    vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
      this.dispatchEvent(new Event('pause'))
    })
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn((blob: Blob) => {
        const url = `blob:mock/${created.length}-${blob.size}`
        created.push(url)
        return url
      }),
      revokeObjectURL: vi.fn((url: string) => {
        revoked.push(url)
      }),
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('shows the AI-voice disclosure as visible text before anything is played', () => {
    render(<ReadAloudButton label="Architecture" fetchClip={async () => ({ blob: new Blob(), truncated: false })} />)
    expect(screen.getByText('AI-generated voice')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Read aloud: Architecture' })).toBeInTheDocument()
  })

  it('fetches, plays from an object URL, and toggles to Pause', async () => {
    const fetchClip = vi.fn(async () => ({ blob: new Blob(['mp3'], { type: 'audio/mpeg' }), truncated: false }))
    render(<ReadAloudButton label="Architecture" fetchClip={fetchClip} />)

    fireEvent.click(screen.getByRole('button', { name: /Read aloud/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: /^Pause/ })).toBeInTheDocument())

    expect(fetchClip).toHaveBeenCalledTimes(1)
    expect(created).toHaveLength(1)
    expect(playSpy).toHaveBeenCalled()
    // Replay only appears once there's something to replay.
    expect(screen.getByRole('button', { name: /Replay/ })).toBeInTheDocument()
  })

  it('says it is reading only the first part when the server marked truncation', async () => {
    render(
      <ReadAloudButton label="Big" fetchClip={async () => ({ blob: new Blob(['x']), truncated: true })} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Read aloud/ }))
    await waitFor(() => expect(screen.getByText(/Reading the first part/)).toBeInTheDocument())
  })

  it('renders the server detail on failure and offers to try again', async () => {
    render(
      <ReadAloudButton
        label="Architecture"
        fetchClip={async () => {
          throw new ApiError(503, 'read-aloud is temporarily unavailable — please try again')
        }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Read aloud/ }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/temporarily unavailable/))
    // The control is back to a fresh "Read aloud", not stuck.
    expect(screen.getByRole('button', { name: /Read aloud/ })).toBeEnabled()
    expect(created).toHaveLength(0)
  })

  it('revokes the object URL when it unmounts', async () => {
    const { unmount } = render(
      <ReadAloudButton label="A" fetchClip={async () => ({ blob: new Blob(['x']), truncated: false })} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Read aloud/ }))
    await waitFor(() => expect(created).toHaveLength(1))
    await act(async () => unmount())
    expect(revoked).toEqual(created)
  })
})
