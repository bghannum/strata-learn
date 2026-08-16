// The one test file that touches the browser media globals directly. jsdom
// has no MediaRecorder or navigator.mediaDevices, so both are stubbed here
// — everything else in the app reaches them only through recorder.ts and
// mocks that module instead (see SpokenAnswer.test.tsx).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { isRecordingSupported, pickRecordingType, RecorderError, startRecording } from '../recorder'

class FakeTrack {
  stopped = false
  stop() {
    this.stopped = true
  }
}

class FakeStream {
  tracks = [new FakeTrack()]
  getTracks() {
    return this.tracks
  }
}

/** Minimal MediaRecorder: the test drives ondataavailable/onstop by hand. */
class FakeMediaRecorder {
  static supported = new Set(['audio/webm;codecs=opus'])
  static instances: FakeMediaRecorder[] = []
  static isTypeSupported(type: string) {
    return FakeMediaRecorder.supported.has(type)
  }
  state: 'inactive' | 'recording' = 'inactive'
  mimeType: string
  stream: FakeStream
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  constructor(stream: FakeStream, options?: { mimeType?: string }) {
    this.stream = stream
    this.mimeType = options?.mimeType ?? ''
    FakeMediaRecorder.instances.push(this)
  }
  start() {
    this.state = 'recording'
  }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['audio'], { type: this.mimeType }) })
    this.onstop?.()
  }
}

function installBrowserMedia(getUserMedia: () => Promise<FakeStream>) {
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true })
}

describe('recorder', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    FakeMediaRecorder.instances = []
    // navigator is a fixed jsdom global — undo the defineProperty by hand.
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
  })

  it('reports unsupported when the browser has no MediaRecorder', () => {
    expect(isRecordingSupported()).toBe(false)
    expect(pickRecordingType()).toBeUndefined()
  })

  it('negotiates the first container the browser can actually produce', () => {
    installBrowserMedia(async () => new FakeStream())
    expect(isRecordingSupported()).toBe(true)
    expect(pickRecordingType()).toBe('audio/webm;codecs=opus')
    // A Safari-shaped browser: no webm, only mp4.
    FakeMediaRecorder.supported = new Set(['audio/mp4'])
    expect(pickRecordingType()).toBe('audio/mp4')
    FakeMediaRecorder.supported = new Set(['audio/webm;codecs=opus'])
  })

  it('records, stops, releases the microphone, and hands back a typed clip', async () => {
    const stream = new FakeStream()
    installBrowserMedia(async () => stream)

    const active = await startRecording()
    expect(FakeMediaRecorder.instances[0].state).toBe('recording')

    const clip = await active.stop()
    expect(clip).not.toBeNull()
    expect(clip!.type).toBe('audio/webm;codecs=opus')
    // The mic indicator must go dark: every track stopped.
    expect(stream.tracks[0].stopped).toBe(true)
  })

  it('cancel releases the microphone without producing a clip', async () => {
    const stream = new FakeStream()
    installBrowserMedia(async () => stream)
    const active = await startRecording()
    active.cancel()
    expect(stream.tracks[0].stopped).toBe(true)
    expect(FakeMediaRecorder.instances[0].state).toBe('inactive')
  })

  it('classifies a denied permission so the UI can say so plainly', async () => {
    installBrowserMedia(async () => {
      throw Object.assign(new Error('denied'), { name: 'NotAllowedError' })
    })
    await expect(startRecording()).rejects.toMatchObject({ kind: 'permission_denied' })
  })

  it('classifies a missing device separately from a denial', async () => {
    installBrowserMedia(async () => {
      throw Object.assign(new Error('none'), { name: 'NotFoundError' })
    })
    const err = await startRecording().catch((e) => e)
    expect(err).toBeInstanceOf(RecorderError)
    expect(err.kind).toBe('no_microphone')
  })
})
