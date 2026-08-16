import { useEffect, useRef, useState } from 'react'
import { ApiError, transcribeAnswer } from '../api/client'
import { useAudioRecorder } from '../audio/useAudioRecorder'
import Button from './ui/Button'
import { Textarea } from './ui/Field'

const MAX_SECONDS = 60

const FAILURE_MESSAGES: Record<string, string> = {
  unsupported: "This browser can't record audio — type your answer instead.",
  permission_denied: 'Microphone access was denied. Allow it in the browser and try again, or type your answer.',
  no_microphone: 'No microphone was found — type your answer instead.',
  failed: "Couldn't start recording — type your answer instead.",
}

interface SpokenAnswerProps {
  attemptId: string
  questionId: string
  /** The learner's explicit "use this" — the only way transcript text ever
   *  reaches the answer box. Nothing here submits or grades. */
  onUseTranscript: (text: string) => void
  disabled?: boolean
}

/** Spoken answers for a fill-blank question (ADR-010). Record → upload →
 *  an *editable* transcript → the learner clicks "Use this answer" and it
 *  lands in the ordinary answer box, to be submitted through the ordinary
 *  Submit button. Never auto-submits: an ASR model mishears identifiers, and
 *  the whole point of the confirmation step is that only text the learner
 *  has read and approved is ever graded. */
function SpokenAnswer({ attemptId, questionId, onUseTranscript, disabled = false }: SpokenAnswerProps) {
  const [transcript, setTranscript] = useState<string | null>(null)
  const [transcribing, setTranscribing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The question this transcript belongs to. Previous/Next remount nothing
  // here (the parent keeps this component mounted across questions), so a
  // transcript from question 3 must not sit in the panel when question 4
  // renders — see the reset effect below.
  const questionRef = useRef(questionId)

  const recorder = useAudioRecorder(MAX_SECONDS, (clip) => {
    if (clip) void upload(clip)
  })

  const cancelRecording = recorder.cancel
  useEffect(() => {
    if (questionRef.current === questionId) return
    questionRef.current = questionId
    cancelRecording()
    setTranscript(null)
    setTranscribing(false)
    setError(null)
  }, [questionId, cancelRecording])

  async function upload(clip: Blob) {
    setTranscribing(true)
    setError(null)
    try {
      const result = await transcribeAnswer(attemptId, questionId, clip)
      setTranscript(result.text)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't transcribe that recording.")
    } finally {
      setTranscribing(false)
    }
  }

  async function handleToggle() {
    if (recorder.state === 'recording') {
      const clip = await recorder.stop()
      if (clip) await upload(clip)
      else setError('Nothing was recorded — try again.')
      return
    }
    setTranscript(null)
    setError(null)
    await recorder.start()
  }

  if (!recorder.supported) {
    return null
  }

  const failureMessage = recorder.failure ? FAILURE_MESSAGES[recorder.failure] : null
  const isRecording = recorder.state === 'recording'
  const remaining = Math.max(0, MAX_SECONDS - recorder.elapsed)

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <Button
          type="button"
          variant={isRecording ? 'primary' : 'secondary'}
          onClick={handleToggle}
          disabled={disabled || transcribing || recorder.state === 'stopping'}
          aria-pressed={isRecording}
        >
          {isRecording ? (
            <>
              <span aria-hidden className="inline-block size-2 animate-pulse rounded-full bg-organic-bg" />
              Stop ({remaining}s)
            </>
          ) : transcribing ? (
            'Transcribing…'
          ) : (
            'Speak your answer'
          )}
        </Button>
        {isRecording && (
          <span className="text-[12.5px] opacity-70" role="status">
            Recording — stops on its own at {MAX_SECONDS}s
          </span>
        )}
      </div>

      {(failureMessage || error) && (
        <div className="mt-2.5 rounded-2xl bg-organic-danger-bg p-3">
          <p className="text-sm text-organic-danger">{failureMessage ?? error}</p>
        </div>
      )}

      {transcript !== null && (
        <div className="mt-3 rounded-2xl border border-organic-divider p-3.5">
          <label htmlFor={`transcript-${questionId}`} className="block text-xs opacity-70">
            Transcript — check it, fix any misheard identifiers, then use it as your answer.
          </label>
          <Textarea
            id={`transcript-${questionId}`}
            className="mt-1.5 min-h-[64px]"
            value={transcript}
            onChange={(event) => setTranscript(event.target.value)}
            disabled={disabled}
          />
          <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
            <Button
              type="button"
              variant="secondary"
              onClick={() => onUseTranscript(transcript.trim())}
              disabled={disabled || !transcript.trim()}
            >
              Use this answer
            </Button>
            <Button type="button" variant="ghost" onClick={handleToggle} disabled={disabled}>
              Re-record
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

export default SpokenAnswer
