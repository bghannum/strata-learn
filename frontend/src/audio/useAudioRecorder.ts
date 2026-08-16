import { useCallback, useEffect, useRef, useState } from 'react'
import { isRecordingSupported, startRecording, RecorderError, type ActiveRecording, type RecorderFailure } from './recorder'

export type RecorderState = 'idle' | 'recording' | 'stopping'

export interface AudioRecorder {
  state: RecorderState
  supported: boolean
  /** Seconds recorded so far, for the countdown. */
  elapsed: number
  /** Why the last start failed, if it did. Cleared on the next start. */
  failure: RecorderFailure | null
  start: () => Promise<void>
  /** Resolves with the finished clip, or null if nothing was captured. */
  stop: () => Promise<Blob | null>
  /** Drops any in-flight recording without producing a clip. */
  cancel: () => void
}

/** Wraps recorder.ts in React state. `maxSeconds` is the client-side
 *  countdown after which recording stops on its own — a UX bound so a
 *  learner can't wander off mid-answer and upload ten minutes of room tone;
 *  the byte cap on the server is the real limit (see the backend's
 *  audio_upload_max_bytes comment). */
export function useAudioRecorder(maxSeconds: number, onAutoStop?: (clip: Blob | null) => void): AudioRecorder {
  const [state, setState] = useState<RecorderState>('idle')
  const [elapsed, setElapsed] = useState(0)
  const [failure, setFailure] = useState<RecorderFailure | null>(null)
  const activeRef = useRef<ActiveRecording | null>(null)
  const timerRef = useRef<number | null>(null)
  const startedAtRef = useRef<number>(0)
  const onAutoStopRef = useRef(onAutoStop)
  onAutoStopRef.current = onAutoStop

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const stop = useCallback(async (): Promise<Blob | null> => {
    const active = activeRef.current
    if (!active) return null
    setState('stopping')
    clearTimer()
    activeRef.current = null
    try {
      return await active.stop()
    } finally {
      setState('idle')
      setElapsed(0)
    }
  }, [clearTimer])

  const cancel = useCallback(() => {
    clearTimer()
    activeRef.current?.cancel()
    activeRef.current = null
    setState('idle')
    setElapsed(0)
  }, [clearTimer])

  const start = useCallback(async () => {
    if (activeRef.current) return
    setFailure(null)
    try {
      const active = await startRecording()
      activeRef.current = active
      startedAtRef.current = Date.now()
      setElapsed(0)
      setState('recording')
      timerRef.current = window.setInterval(() => {
        const seconds = Math.floor((Date.now() - startedAtRef.current) / 1000)
        setElapsed(seconds)
        if (seconds >= maxSeconds) {
          // Auto-stop hands the clip to the caller the same way a manual
          // stop would, so the flow past this point is identical.
          void stop().then((clip) => onAutoStopRef.current?.(clip))
        }
      }, 250)
    } catch (err) {
      setFailure(err instanceof RecorderError ? err.kind : 'failed')
      setState('idle')
    }
  }, [maxSeconds, stop])

  // Releases the microphone if the component unmounts mid-recording — the
  // browser's recording indicator staying lit after navigating away is the
  // kind of thing that erodes trust in a mic feature fast.
  useEffect(() => cancel, [cancel])

  return { state, supported: isRecordingSupported(), elapsed, failure, start, stop, cancel }
}
