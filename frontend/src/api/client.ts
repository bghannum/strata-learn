import { useEffect, useState } from 'react'

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export async function getHealth(): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return response.json()
}

// --- Types, mirroring backend/app/db/models.py and api/study_guides.py field names exactly ---

export type SourceType = 'git_url' | 'zip_upload'
export type SnapshotStatus = 'pending' | 'parsing' | 'analyzing' | 'generating' | 'ready' | 'failed'
export type SectionType = 'overview' | 'architecture' | 'tradeoffs' | 'glossary' | 'deep_dive'

export interface Repo {
  id: string
  user_id: string | null
  source_type: SourceType
  source_uri: string
  display_name: string
  created_at: string
  latest_snapshot_id: string | null
}

export interface EntryPoint {
  file: string
  kind: string
  reason?: string
}

export interface AnalysisSnapshot {
  id: string
  repo_id: string
  commit_hash: string | null
  indexed_at: string
  status: SnapshotStatus
  file_count: number
  language_summary: Record<string, number>
  dependency_graph: { nodes: unknown[]; edges: unknown[] }
  entry_points: EntryPoint[]
}

export interface Citation {
  id: string
  file_path: string
  line_start: number
  line_end: number
  claim_excerpt: string
  snippet_text: string
}

export interface Section {
  id: string
  section_type: SectionType
  title: string
  order: number
  content_md: string
  diagram_mermaid: string | null
  citations: Citation[]
}

export interface StudyGuide {
  id: string
  repo_id: string
  snapshot_id: string
  version: number
  generated_at: string
  sections: Section[]
}

// --- Requests ---

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function parseErrorDetail(response: Response): Promise<string> {
  // FastAPI's HTTPException body shape is {"detail": "..."} — fall back to a
  // generic message for anything else (a non-JSON 502 from a proxy, etc.).
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') return body.detail
  } catch {
    // not JSON — use the generic fallback below
  }
  return `Request failed: ${response.status}`
}

// AuthProvider's initial GET /auth/me 401 is handled locally (a fresh visit
// just means "no user yet"), but nothing previously cleared auth state after
// a *runtime* 401 — a session expiring mid-use left the UI still showing the
// logged-in chrome against a dead cookie. request() is the one chokepoint
// every authenticated call passes through, so it's the natural place to
// detect this; a DOM event (rather than a direct import) keeps this API
// module independent of React/AuthContext.
export const UNAUTHORIZED_EVENT = 'strata:unauthorized'

function notifyUnauthorized(): void {
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
}

// A 401 from an endpoint that *checks* credentials means "those credentials
// were wrong", not "your session died" — the two are indistinguishable at the
// status code alone, and treating them the same logs out a perfectly valid
// session (#44). /login isn't behind AppLayout's auth guard, so an
// already-signed-in user can navigate there directly; one mistyped password
// used to clear their AuthContext user while the backend session cookie stayed
// untouched, leaving the client convinced it was logged out when it wasn't.
interface RequestOptions {
  /** Dispatch UNAUTHORIZED_EVENT on a 401. Default true; false for endpoints
   *  where a 401 is an expected answer rather than an expired session. */
  notifyOn401?: boolean
}

async function request<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  // credentials: 'include' — every endpoint requires the session cookie as
  // of Phase 4b, and the API runs on a different port than the frontend
  // dev server, so the browser won't attach it without being told to.
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: 'include' })
  if (!response.ok) {
    if (response.status === 401 && options?.notifyOn401 !== false) notifyUnauthorized()
    throw new ApiError(response.status, await parseErrorDetail(response))
  }
  return response.json()
}

/** request()'s sibling for binary bodies — audio for read-aloud. Shares the
 *  401 handling and ApiError shape; only `.blob()` differs from `.json()`.
 *
 *  Why not `<audio src={url}>` like the export link? That link is a top-level
 *  navigation, where cookies attach under navigation rules. An <audio> tag
 *  here is a *cross-origin subresource* (frontend :5173, API :8000) — it only
 *  carries the session cookie with crossOrigin="use-credentials" and matching
 *  CORS headers, and it breaks silently when either changes. Worse, its
 *  `error` event carries a MediaError code and nothing else: a 429, a 503,
 *  and a codec fault would all render "Could not play audio", and a 401
 *  would never fire UNAUTHORIZED_EVENT. Fetching gives every failure the
 *  server's own detail string, like everywhere else in the app. */
export async function requestBlob(path: string, init?: RequestInit): Promise<{ blob: Blob; headers: Headers }> {
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: 'include' })
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized()
    throw new ApiError(response.status, await parseErrorDetail(response))
  }
  return { blob: await response.blob(), headers: response.headers }
}

// --- Auth ---

export interface User {
  id: string
  email: string
  created_at: string
}

export interface AuthStatus {
  /** No account exists yet — send the visitor to /setup, not /login. */
  setup_required: boolean
  /** The server has REGISTRATION_SECRET set, so setup must ask for it. */
  secret_required: boolean
}

// Unauthenticated: it's what decides where an unauthenticated visitor goes.
export function getAuthStatus(): Promise<AuthStatus> {
  return request('/auth/status')
}

// registrationSecret is only sent when the server asks for it (AuthStatus
// above); the backend ignores it otherwise.
export function register(email: string, password: string, registrationSecret?: string): Promise<User> {
  return request(
    '/auth/register',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        password,
        ...(registrationSecret !== undefined ? { registration_secret: registrationSecret } : {}),
      }),
    },
    { notifyOn401: false },
  )
}

export function login(email: string, password: string): Promise<User> {
  return request(
    '/auth/login',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    },
    { notifyOn401: false },
  )
}

export async function logout(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/logout`, { method: 'POST', credentials: 'include' })
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized()
    throw new ApiError(response.status, await parseErrorDetail(response))
  }
}

export function getCurrentUser(): Promise<User> {
  return request('/auth/me')
}

// --- Repos ---

export function listRepos(): Promise<Repo[]> {
  return request('/repos')
}

export function getRepo(repoId: string): Promise<Repo> {
  return request(`/repos/${repoId}`)
}

// Only valid when the repo's latest snapshot is `failed` (backend 409s
// otherwise) — retries indexing from scratch, not a resume from checkpoint.
/** Retries a failed run, or re-indexes a ready repo to pick up new commits —
 *  one mechanism, two intents. `force` overrides the backend's refusal to
 *  re-run the (paid) pipeline when the remote hasn't actually moved. */
export function reindexRepo(repoId: string, force = false): Promise<Repo> {
  return request(`/repos/${repoId}/reindex`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  })
}

export function getSnapshot(repoId: string): Promise<AnalysisSnapshot> {
  return request(`/repos/${repoId}/snapshot`)
}

export interface UpdateStatus {
  status: 'up_to_date' | 'stale' | 'unknown'
  checked_at: string | null
  remote_commit: string | null
  indexed_commit: string | null
  reason: string | null
}

/** Reads the last check's result. No network I/O on the backend — safe on load. */
export function getUpdateStatus(repoId: string): Promise<UpdateStatus> {
  return request(`/repos/${repoId}/update-status`)
}

/** Asks the remote for its current HEAD. This is the one that goes over the
 *  network and can take a few seconds, so it's only ever user-initiated. */
export function checkForUpdates(repoId: string): Promise<UpdateStatus> {
  return request(`/repos/${repoId}/check-updates`, { method: 'POST' })
}

export interface MasteryPoint {
  completed_at: string
  answered: number
  average_score: number
}

export interface MasteryBucket {
  subsystem_key: string
  name: string
  attempts: number
  answered: number
  average_score: number
  history: MasteryPoint[]
}

export interface Mastery {
  completed_attempts: number
  buckets: MasteryBucket[]
}

export function getMastery(repoId: string): Promise<Mastery> {
  return request(`/repos/${repoId}/mastery`)
}

/** Absolute URL rather than a fetch: the browser handles the download itself,
 *  honouring the Content-Disposition filename the API sets. Same-origin cookie
 *  auth applies to a plain navigation, so no token plumbing is needed. */
export function studyGuideExportUrl(studyGuideId: string): string {
  return `${API_BASE_URL}/study-guides/${studyGuideId}/export.md`
}

// GET /repos/{id}/study-guide redirects to GET /study-guides/{id} — fetch()
// follows redirects by default, so this returns the guide directly.
export function getRepoStudyGuide(repoId: string): Promise<StudyGuide> {
  return request(`/repos/${repoId}/study-guide`)
}

export function getStudyGuide(studyGuideId: string): Promise<StudyGuide> {
  return request(`/study-guides/${studyGuideId}`)
}

// --- Versions and the architectural diff (#72) ---

/** One generated version, without its sections — enough to label a picker
 *  entry ("v2 · a1b2c3d"), which is all the diff's version selectors need. */
export interface StudyGuideVersion {
  id: string
  version: number
  generated_at: string
  snapshot_id: string
  commit_hash: string | null
}

/** Newest first, by version — the same ordering the diff endpoint uses to
 *  decide which side is "before". */
export function listStudyGuideVersions(repoId: string): Promise<StudyGuideVersion[]> {
  return request(`/repos/${repoId}/study-guides`)
}

export interface SubsystemRef {
  key: string
  name: string
}

export interface SubsystemMembershipChange {
  key: string
  name: string
  files_added: string[]
  files_removed: string[]
}

export interface TradeoffChange {
  decision_before: string
  decision_after: string
  reasoning_before: string
  reasoning_after: string
  cost_before: string
  cost_after: string
  evidence_paths: string[]
}

export interface SubsystemEdge {
  source: string
  target: string
}

/** Structure only, no prose summary — see backend/app/generation/diffing.py for
 *  why nothing here matches on generated text. */
export interface StudyGuideDiff {
  from_version: number
  to_version: number
  from_snapshot_id: string
  to_snapshot_id: string
  from_commit: string | null
  to_commit: string | null
  subsystems: {
    added: SubsystemRef[]
    removed: SubsystemRef[]
    changed: SubsystemMembershipChange[]
  }
  tradeoffs: {
    added: string[]
    removed: string[]
    changed: TradeoffChange[]
  }
  pattern: {
    changed: boolean
    pattern_before: string | null
    pattern_after: string | null
    confidence_before: string | null
    confidence_after: string | null
  }
  dependencies: {
    edges_added: SubsystemEdge[]
    edges_removed: SubsystemEdge[]
  }
}

/** Direction is decided server-side by version, not by argument order — the
 *  lower-versioned guide is always "before" whichever id is passed first. */
export function getStudyGuideDiff(studyGuideId: string, otherStudyGuideId: string): Promise<StudyGuideDiff> {
  return request(`/study-guides/${studyGuideId}/diff/${otherStudyGuideId}`)
}

// --- Quizzes ---

export type QuizStatus = 'generating' | 'ready' | 'failed'
// fill_blank still exists for quizzes generated before short_answer replaced
// it in the mix; new quizzes are mcq + short_answer.
export type QuestionType = 'mcq' | 'fill_blank' | 'short_answer'
export type FillBlankMode = 'code' | 'concept'

// No answer key here (correct_index, correct_answer, explanation) — see
// backend/app/api/quizzes.py's module docstring for why: it only appears in
// submitAnswer's response, after the student has answered that question.
export interface Question {
  id: string
  question_type: QuestionType
  order: number
  prompt: string
  choices: string[] | null
  fill_blank_mode: FillBlankMode | null
}

export type FeedbackMode = 'immediate' | 'end_of_quiz'

export interface Quiz {
  id: string
  repo_id: string
  study_guide_id: string
  status: QuizStatus
  feedback_mode: FeedbackMode
  questions: Question[]
}

// feedback_mode defaults server-side to 'end_of_quiz' (ui-spec.md §6.5) if
// omitted. If an already-`generating` quiz exists for this study guide, the
// backend reuses it and returns *that* quiz's feedback_mode, not this call's
// argument — it's the same generation job, not a new one.
export function generateQuiz(repoId: string, feedbackMode?: FeedbackMode): Promise<Quiz> {
  return request(`/quizzes/${repoId}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback_mode: feedbackMode }),
  })
}

// Optional signal lets pollQuiz actually cancel an in-flight/hung request
// once its deadline or an abort fires, instead of just discarding the
// eventual result.
export function getQuiz(quizId: string, signal?: AbortSignal): Promise<Quiz> {
  return request(`/quizzes/${quizId}`, { signal })
}

// GET /repos/{id}/quiz redirects to GET /quizzes/{id} — mirrors
// getRepoStudyGuide. Lets RepoDetail recover an already-enqueued or
// already-ready quiz after a reload instead of only offering "Generate
// Quiz" again (which would enqueue a second paid job). Throws ApiError(404)
// if this repo has no quiz yet — callers should treat that as "none exists".
export function getRepoQuiz(repoId: string): Promise<Quiz> {
  return request(`/repos/${repoId}/quiz`)
}

// --- Attempts ---

export type AttemptStatus = 'in_progress' | 'completed'

export interface Attempt {
  id: string
  quiz_id: string
  status: AttemptStatus
  score: number | null
}

/** One completed sitting, for RepoDetail.tsx's quiz-history panel. Distinct
 *  from Mastery, which aggregates answers by subsystem across attempts and so
 *  can't say "80% on a 5-question quiz on Tuesday". */
export interface AttemptSummary {
  id: string
  quiz_id: string
  completed_at: string
  score: number
  question_count: number
}

/** A bounded page plus the full count (#75): retakes are unlimited, so `items`
 *  is the recent window and `total` is what it's a window onto. */
export interface AttemptHistory {
  items: AttemptSummary[]
  total: number
}

/** Matches the backend's own default and ceiling — the panel needs both to
 *  label its "show all" affordance honestly. */
export const DEFAULT_ATTEMPT_PAGE_SIZE = 10
export const MAX_ATTEMPT_PAGE_SIZE = 100

/** Newest first; completed attempts only. */
export function listRepoAttempts(repoId: string, limit = DEFAULT_ATTEMPT_PAGE_SIZE): Promise<AttemptHistory> {
  return request(`/repos/${repoId}/attempts?limit=${limit}`)
}

export interface AnswerResult {
  question_id: string
  // #37: null in end_of_quiz mode — the backend withholds these entirely
  // rather than grading-but-hiding client-side, since inspecting the network
  // response would otherwise reveal correctness immediately regardless of
  // what the UI chooses to render.
  score: number | null
  feedback: string | null
  correct_index: number | null
  correct_answer: string | null
  // short_answer only, revealed on the same terms as feedback: the key
  // points and which ones the judge found — score is hits/total, so the UI
  // can show coverage rather than a bare number.
  rubric: string[] | null
  rubric_hits: boolean[] | null
}

export interface QuestionResult {
  question_id: string
  question_type: QuestionType
  prompt: string
  // #37: true whenever a submission exists, independent of whether score
  // below is currently revealed — use this, not `score !== null`, to know
  // whether a question has already been answered.
  answered: boolean
  score: number | null
  feedback: string | null
  file_path: string
  line_start: number
  line_end: number
  citation_claim_excerpt: string | null
  citation_snippet_text: string | null
  // #34: display text (an mcq choice's own text, not its index) — both null
  // until the attempt is completed, regardless of whether this particular
  // question has already been answered (see the backend's QuestionResultOut
  // for why: revealing this mid-quiz could leak upcoming correct answers).
  submitted_answer: string | null
  correct_answer: string | null
  // short_answer only. rubric follows correct_answer's gate (completed only —
  // the key points *are* the answer key); rubric_hits follows feedback's.
  rubric: string[] | null
  rubric_hits: boolean[] | null
}

export interface AttemptResults {
  id: string
  quiz_id: string
  status: AttemptStatus
  score: number | null
  questions: QuestionResult[]
}

export function createAttempt(quizId: string): Promise<Attempt> {
  return request('/attempts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ quiz_id: quizId }),
  })
}

export function submitAnswer(
  attemptId: string,
  questionId: string,
  answer: { selected_index: number } | { answer_text: string },
): Promise<AnswerResult> {
  return request(`/attempts/${attemptId}/answers/${questionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(answer),
  })
}

export function completeAttempt(attemptId: string): Promise<AttemptResults> {
  return request(`/attempts/${attemptId}/complete`, { method: 'POST' })
}

export function getAttempt(attemptId: string): Promise<AttemptResults> {
  return request(`/attempts/${attemptId}`)
}

// --- Voice (Phase 8, ADR-010) ---

/** Booleans only, never a backend name — which backend a deployment uses is
 *  operator config the UI is deliberately kept ignorant of. The frontend
 *  reads this once and hides any control whose capability is off, so a
 *  learner is never shown a mic button that 503s on click. */
export interface VoiceCapabilities {
  transcription: boolean
  speech: boolean
}

export function getVoiceCapabilities(): Promise<VoiceCapabilities> {
  return request('/voice/capabilities')
}

/** Header the speech routes set when the section was cut at the provider's
 *  input limit, so the control can say "reading the first part". */
export const SPEECH_TRUNCATED_HEADER = 'X-Speech-Truncated'

export interface SpeechClip {
  blob: Blob
  truncated: boolean
}

export async function getSectionSpeech(studyGuideId: string, sectionId: string): Promise<SpeechClip> {
  const { blob, headers } = await requestBlob(`/study-guides/${studyGuideId}/sections/${sectionId}/speech`)
  return { blob, truncated: headers.get(SPEECH_TRUNCATED_HEADER) === '1' }
}

export async function getFeedbackSpeech(attemptId: string, questionId: string): Promise<SpeechClip> {
  const { blob, headers } = await requestBlob(`/attempts/${attemptId}/answers/${questionId}/feedback-speech`)
  return { blob, truncated: headers.get(SPEECH_TRUNCATED_HEADER) === '1' }
}

export interface Transcription {
  text: string
  duration_ms: number
}

/** Uploads a finished recording and returns an *editable* transcript. Writes
 *  nothing server-side: the learner corrects the text and only the confirmed
 *  version goes through submitAnswer, exactly like a typed answer. Plain
 *  fetch + FormData rather than createRepo's XHR — a mic clip is small enough
 *  that upload progress isn't worth the extra machinery, and the browser sets
 *  the multipart boundary itself when no Content-Type header is given. */
export function transcribeAnswer(attemptId: string, questionId: string, recording: Blob): Promise<Transcription> {
  const form = new FormData()
  // The extension is derived from the blob's own type — the backend sniffs
  // the container from the bytes anyway, but the SDK on the far side reads
  // the format from the filename, so it has to be right rather than a
  // hardcoded ".webm" that Safari's mp4 recordings would then contradict.
  form.append('file', recording, `answer.${extensionFor(recording.type)}`)
  return request(`/attempts/${attemptId}/answers/${questionId}/transcription`, { method: 'POST', body: form })
}

function extensionFor(mimeType: string): string {
  const base = mimeType.split(';')[0].trim().toLowerCase()
  if (base === 'audio/mp4' || base === 'video/mp4') return 'mp4'
  if (base === 'audio/wav' || base === 'audio/x-wav') return 'wav'
  if (base === 'audio/ogg') return 'ogg'
  return 'webm'
}

export class PollTimeoutError extends Error {}

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

export interface PollQuizOptions {
  intervalMs?: number
  /** Caller-supplied ceiling on total wait — rejects with PollTimeoutError
   * past this point rather than polling forever if the quiz never reaches a
   * terminal status. */
  timeoutMs?: number
  /** Lets a caller stop the loop (component unmount, a newer poll
   * superseding an older one) without waiting for the next tick. */
  signal?: AbortSignal
}

/** Polls GET /quizzes/{id} until status is terminal (ready/failed) — no
 * progress WebSocket for quiz generation (see worker/quiz_pipeline.py's
 * docstring): a bounded handful of cheap-tier LLM calls finishes fast
 * enough that polling is simpler and good enough.
 *
 * Found via issue #38: the original recursive setTimeout had no
 * cancellation, deadline, or unmount cleanup — a component unmounting
 * mid-poll left the timer chain running and calling setState on nothing,
 * and a quiz stuck in "generating" would poll forever. */
export function pollQuiz(quizId: string, options: PollQuizOptions = {}): Promise<Quiz> {
  const { intervalMs = 2000, timeoutMs = 5 * 60 * 1000, signal } = options

  return new Promise((resolve, reject) => {
    let settled = false
    let intervalTimer: ReturnType<typeof setTimeout> | undefined
    let deadlineTimer: ReturnType<typeof setTimeout> | undefined
    // A fetch's own AbortController, distinct from the caller's `signal` —
    // this is what actually cancels an in-flight/hung request once the
    // deadline or an abort fires, rather than just ignoring its result.
    const fetchController = new AbortController()

    function cleanup() {
      settled = true
      if (intervalTimer !== undefined) clearTimeout(intervalTimer)
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer)
      signal?.removeEventListener('abort', onAbort)
      fetchController.abort()
    }
    function onAbort() {
      cleanup()
      reject(new DOMException('Poll aborted', 'AbortError'))
    }
    if (signal) {
      if (signal.aborted) {
        onAbort()
        return
      }
      signal.addEventListener('abort', onAbort)
    }

    // Found via Codex's PR #43 review: the deadline was previously only
    // checked inside the success callback, so it never fired if a single
    // getQuiz call hung or simply took longer than timeoutMs to settle —
    // polling wasn't actually bounded. An independent timer enforces it
    // regardless of what the in-flight request is doing, and aborts that
    // request via fetchController so it stops consuming a connection.
    deadlineTimer = setTimeout(() => {
      cleanup()
      reject(new PollTimeoutError(`Quiz ${quizId} did not finish generating within ${timeoutMs}ms`))
    }, timeoutMs)

    function tick() {
      getQuiz(quizId, fetchController.signal)
        .then((quiz) => {
          // Deadline/abort can settle the promise while this fetch was
          // in flight — without this check, a late-arriving response would
          // still schedule another timer for a poll nothing awaits anymore.
          if (settled) return
          if (quiz.status === 'ready' || quiz.status === 'failed') {
            cleanup()
            resolve(quiz)
          } else {
            intervalTimer = setTimeout(tick, intervalMs)
          }
        })
        .catch((err) => {
          if (settled) return
          cleanup()
          reject(err)
        })
    }
    tick()
  })
}

export interface CreateRepoFromGitUrl {
  sourceType: 'git_url'
  gitUrl: string
  displayName?: string
}

export interface CreateRepoFromZip {
  sourceType: 'zip_upload'
  file: File
  displayName?: string
}

// Mirrors backend/app/config.py's zip_upload_max_bytes default — the server
// is still the source of truth (this is a UX pre-check, not a security
// boundary), but rejecting an obviously-oversized file before spending
// bandwidth on it needs to know the same number the server enforces.
export const MAX_ZIP_UPLOAD_BYTES = 50 * 1024 * 1024

export async function createRepo(
  input: CreateRepoFromGitUrl | CreateRepoFromZip,
  onUploadProgress?: (percent: number) => void,
): Promise<Repo> {
  const form = new FormData()
  form.set('source_type', input.sourceType)
  if (input.displayName) form.set('display_name', input.displayName)

  if (input.sourceType === 'git_url') {
    form.set('git_url', input.gitUrl)
    const response = await fetch(`${API_BASE_URL}/repos`, { method: 'POST', body: form, credentials: 'include' })
    if (!response.ok) {
      if (response.status === 401) notifyUnauthorized()
      throw new ApiError(response.status, await parseErrorDetail(response))
    }
    return response.json()
  }

  form.set('file', input.file)
  // XMLHttpRequest, not fetch — fetch exposes no upload-progress event, and
  // a zip can be large enough (up to MAX_ZIP_UPLOAD_BYTES) that "Adding…"
  // alone leaves no feedback for a real transfer.
  return new Promise<Repo>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE_URL}/repos`)
    xhr.withCredentials = true // send the session cookie, same reason as request()'s credentials: 'include'
    xhr.upload.onprogress = (event) => {
      if (onUploadProgress && event.lengthComputable) {
        onUploadProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText))
        return
      }
      if (xhr.status === 401) notifyUnauthorized()
      let detail = `Request failed: ${xhr.status}`
      try {
        const body = JSON.parse(xhr.responseText)
        if (typeof body?.detail === 'string') detail = body.detail
      } catch {
        // not JSON — use the generic fallback above
      }
      reject(new ApiError(xhr.status, detail))
    }
    xhr.onerror = () => reject(new ApiError(0, 'Network error while uploading'))
    xhr.send(form)
  })
}

// --- Progress WebSocket ---

function progressWsUrl(repoId: string): string {
  const wsBase = API_BASE_URL.replace(/^http/, 'ws')
  return `${wsBase}/repos/${repoId}/progress`
}

interface ProgressMessage {
  status: SnapshotStatus
  error?: string
}

function isTerminal(status: SnapshotStatus | undefined): boolean {
  return status === 'ready' || status === 'failed'
}

/** Opens WS /repos/{id}/progress and tracks live status — closes itself once
 * the server reaches a terminal status (it stops publishing after that
 * anyway) or `repoId` becomes undefined. The caller decides whether to open
 * a connection at all: pass `repoId: undefined` for a repo whose last known
 * status is already terminal (Dashboard uses this to keep one socket per
 * non-terminal row, not one per row unconditionally) — this hook doesn't
 * make that call itself, since `initialStatus` is typically only known
 * after an async fetch the caller runs on its own, arriving after this
 * hook's first render either way.
 *
 * `lastNonTerminalStatus` is tracked separately from `status` so a caller
 * can show *which* stage failed instead of just a generic failure — once
 * `status` becomes `failed` it overwrites what came before, but the last
 * real progress stage is exactly the point IndexingProgress's stepper needs
 * to mark as the failure point. */
export function useIndexingProgress(
  repoId: string | undefined,
  initialStatus?: SnapshotStatus,
): { status: SnapshotStatus | undefined; error: string | undefined; lastNonTerminalStatus: SnapshotStatus | undefined } {
  const [status, setStatus] = useState<SnapshotStatus | undefined>(initialStatus)
  const [error, setError] = useState<string | undefined>(undefined)
  const [lastNonTerminalStatus, setLastNonTerminalStatus] = useState<SnapshotStatus | undefined>(
    isTerminal(initialStatus) ? undefined : initialStatus,
  )

  // initialStatus commonly arrives after mount — keep displaying it until a
  // live WS message takes over, rather than seeding once at mount (before
  // the caller's own fetch has resolved) and never updating again.
  useEffect(() => {
    setStatus((current) => current ?? initialStatus)
    setLastNonTerminalStatus((current) => current ?? (isTerminal(initialStatus) ? undefined : initialStatus))
  }, [initialStatus])

  useEffect(() => {
    if (!repoId) return
    const socket = new WebSocket(progressWsUrl(repoId))
    socket.onmessage = (event) => {
      const message: ProgressMessage = JSON.parse(event.data)
      setStatus(message.status)
      setError(message.error)
      if (isTerminal(message.status)) {
        socket.close()
      } else {
        setLastNonTerminalStatus(message.status)
      }
    }
    return () => socket.close()
  }, [repoId])

  return { status, error, lastNonTerminalStatus }
}
