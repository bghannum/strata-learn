// Every browser media API the spoken-answer flow touches lives in this
// module and nowhere else: getUserMedia, MediaRecorder, isTypeSupported.
// jsdom has none of them, so keeping them behind one seam means component
// tests mock this module and exactly one focused test wrestles with the
// browser globals — the same containment RepoDetail.test.tsx uses for
// WebSocket.

/** Preferred first: webm/Opus is what Chrome and Firefox produce; Safari
 *  has no webm encoder and falls back to mp4/AAC. Both are accepted by both
 *  transcription backends without transcoding, so the choice here is
 *  purely "what can this browser actually record", never a quality call.
 *  Nothing downstream may assume ".webm" — the blob's own type is what
 *  travels (see client.ts's transcribeAnswer). */
const CANDIDATE_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']

export function isRecordingSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.MediaRecorder !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function'
  )
}

export function pickRecordingType(): string | undefined {
  if (typeof window === 'undefined' || typeof window.MediaRecorder === 'undefined') return undefined
  return CANDIDATE_TYPES.find((type) => window.MediaRecorder.isTypeSupported(type))
}

export type RecorderFailure = 'unsupported' | 'permission_denied' | 'no_microphone' | 'failed'

export class RecorderError extends Error {
  kind: RecorderFailure
  constructor(kind: RecorderFailure, message: string) {
    super(message)
    this.kind = kind
  }
}

/** Maps getUserMedia's DOMException names to something the UI can say
 *  plainly. Permission and no-device are the two a learner can act on. */
function classify(err: unknown): RecorderError {
  const name = (err as { name?: string } | null)?.name
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return new RecorderError('permission_denied', 'Microphone access was denied.')
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') {
    return new RecorderError('no_microphone', 'No microphone was found.')
  }
  return new RecorderError('failed', 'Could not start recording.')
}

export interface ActiveRecording {
  /** Stops the recorder and the underlying tracks and resolves with the
   *  finished clip. Resolves to null if nothing was captured. */
  stop: () => Promise<Blob | null>
  /** Aborts without producing a clip — used when the learner navigates away
   *  mid-recording. Always releases the microphone. */
  cancel: () => void
  mimeType: string
}

/** Requests the microphone and starts recording. Rejects with a
 *  RecorderError whose `kind` the UI switches on. */
export async function startRecording(): Promise<ActiveRecording> {
  if (!isRecordingSupported()) {
    throw new RecorderError('unsupported', 'This browser cannot record audio.')
  }
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (err) {
    throw classify(err)
  }

  const mimeType = pickRecordingType()
  const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
  const chunks: Blob[] = []
  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data)
  }

  const release = () => stream.getTracks().forEach((track) => track.stop())

  const stopped = new Promise<void>((resolve) => {
    recorder.onstop = () => resolve()
  })

  try {
    recorder.start()
  } catch (err) {
    release()
    throw classify(err)
  }

  return {
    mimeType: recorder.mimeType || mimeType || 'audio/webm',
    async stop() {
      if (recorder.state !== 'inactive') recorder.stop()
      await stopped
      release()
      if (chunks.length === 0) return null
      return new Blob(chunks, { type: recorder.mimeType || mimeType || 'audio/webm' })
    },
    cancel() {
      try {
        if (recorder.state !== 'inactive') recorder.stop()
      } finally {
        release()
      }
    },
  }
}
