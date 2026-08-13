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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // credentials: 'include' — every endpoint requires the session cookie as
  // of Phase 4b, and the API runs on a different port than the frontend
  // dev server, so the browser won't attach it without being told to.
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: 'include' })
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response))
  }
  return response.json()
}

// --- Auth ---

export interface User {
  id: string
  email: string
  created_at: string
}

export function register(email: string, password: string): Promise<User> {
  return request('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

export function login(email: string, password: string): Promise<User> {
  return request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

export async function logout(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/logout`, { method: 'POST', credentials: 'include' })
  if (!response.ok) {
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

export function getSnapshot(repoId: string): Promise<AnalysisSnapshot> {
  return request(`/repos/${repoId}/snapshot`)
}

// GET /repos/{id}/study-guide redirects to GET /study-guides/{id} — fetch()
// follows redirects by default, so this returns the guide directly.
export function getRepoStudyGuide(repoId: string): Promise<StudyGuide> {
  return request(`/repos/${repoId}/study-guide`)
}

export function getStudyGuide(studyGuideId: string): Promise<StudyGuide> {
  return request(`/study-guides/${studyGuideId}`)
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
