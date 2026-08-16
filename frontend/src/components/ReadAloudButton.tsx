import { useEffect, useRef, useState } from 'react'
import { ApiError, type SpeechClip } from '../api/client'
import Button from './ui/Button'
import Tag from './ui/Tag'

type PlayState = 'idle' | 'loading' | 'playing' | 'paused' | 'error'

interface ReadAloudButtonProps {
  /** Fetches the audio. Passed in rather than the ids so this one control
   *  serves both study-guide sections and quiz feedback. */
  fetchClip: () => Promise<SpeechClip>
  /** Screen-reader context: "Read aloud: Architecture". */
  label: string
}

/** Play / pause / replay for one piece of persisted text (ADR-010). Fetches
 *  to a Blob and plays it from an object URL — see client.ts's requestBlob
 *  for why not <audio src>. The "AI-generated voice" disclosure is visible
 *  text, not just an aria-label; the plan calls for a *clear* disclosure and
 *  a tooltip isn't one. */
function ReadAloudButton({ fetchClip, label }: ReadAloudButtonProps) {
  const [state, setState] = useState<PlayState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  // One <audio> element for the component's lifetime, created up front. On
  // Safari a play() call that happens inside a .then() after an await can
  // lose its user-gesture attribution and be refused; keeping the element
  // stable and calling play() from the click path where possible avoids
  // the most common form of that.
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const objectUrlRef = useRef<string | null>(null)

  useEffect(() => {
    const audio = new Audio()
    audio.preload = 'auto'
    audio.onended = () => setState('idle')
    audio.onpause = () => {
      // ended also fires pause; the ended handler wins by running first
      // and setting idle, so only mark paused when there's time left.
      if (!audio.ended && audio.currentTime > 0) setState('paused')
    }
    audio.onplay = () => setState('playing')
    audioRef.current = audio
    return () => {
      audio.pause()
      audio.src = ''
      // Object URLs are not garbage-collected with the blob — each one
      // pins its bytes until revoked, and a leak here is invisible until
      // twenty sections in.
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
      objectUrlRef.current = null
    }
  }, [])

  async function loadAndPlay() {
    const audio = audioRef.current
    if (!audio) return
    setState('loading')
    setError(null)
    try {
      const clip = await fetchClip()
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
      const url = URL.createObjectURL(clip.blob)
      objectUrlRef.current = url
      setTruncated(clip.truncated)
      audio.src = url
      await audio.play()
    } catch (err) {
      setState('error')
      setError(err instanceof ApiError ? err.message : "Couldn't play this section.")
    }
  }

  function handlePrimary() {
    const audio = audioRef.current
    if (!audio) return
    if (state === 'playing') {
      audio.pause()
      return
    }
    if (state === 'paused') {
      // Resume: no fetch, so play() runs synchronously in the click path.
      void audio.play().catch(() => setState('error'))
      return
    }
    void loadAndPlay()
  }

  function handleReplay() {
    const audio = audioRef.current
    if (!audio || !objectUrlRef.current) return
    audio.currentTime = 0
    void audio.play().catch(() => setState('error'))
  }

  const primaryLabel =
    state === 'loading' ? 'Loading…' : state === 'playing' ? 'Pause' : state === 'paused' ? 'Resume' : 'Read aloud'

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        variant="ghost"
        onClick={handlePrimary}
        disabled={state === 'loading'}
        aria-label={`${primaryLabel}: ${label}`}
      >
        <span aria-hidden>{state === 'playing' ? '❚❚' : '▶'}</span> {primaryLabel}
      </Button>
      {(state === 'playing' || state === 'paused') && (
        <Button type="button" variant="ghost" onClick={handleReplay} aria-label={`Replay: ${label}`}>
          Replay
        </Button>
      )}
      <Tag variant="outline" className="text-[10.5px]">
        AI-generated voice
      </Tag>
      {truncated && state !== 'idle' && state !== 'error' && (
        <span className="text-[12px] opacity-60">Reading the first part of this section</span>
      )}
      {state === 'error' && error && (
        <span className="text-[12.5px] text-organic-danger" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

export default ReadAloudButton
