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
  const response = await fetch(`${API_BASE_URL}${path}`, init)
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response))
  }
  return response.json()
}

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

export async function createRepo(input: CreateRepoFromGitUrl | CreateRepoFromZip): Promise<Repo> {
  const form = new FormData()
  form.set('source_type', input.sourceType)
  if (input.displayName) form.set('display_name', input.displayName)
  if (input.sourceType === 'git_url') {
    form.set('git_url', input.gitUrl)
  } else {
    form.set('file', input.file)
  }
  const response = await fetch(`${API_BASE_URL}/repos`, { method: 'POST', body: form })
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response))
  }
  return response.json()
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
 * hook's first render either way. */
export function useIndexingProgress(
  repoId: string | undefined,
  initialStatus?: SnapshotStatus,
): { status: SnapshotStatus | undefined; error: string | undefined } {
  const [status, setStatus] = useState<SnapshotStatus | undefined>(initialStatus)
  const [error, setError] = useState<string | undefined>(undefined)

  // initialStatus commonly arrives after mount — keep displaying it until a
  // live WS message takes over, rather than seeding once at mount (before
  // the caller's own fetch has resolved) and never updating again.
  useEffect(() => {
    setStatus((current) => current ?? initialStatus)
  }, [initialStatus])

  useEffect(() => {
    if (!repoId) return
    const socket = new WebSocket(progressWsUrl(repoId))
    socket.onmessage = (event) => {
      const message: ProgressMessage = JSON.parse(event.data)
      setStatus(message.status)
      setError(message.error)
      if (isTerminal(message.status)) socket.close()
    }
    return () => socket.close()
  }, [repoId])

  return { status, error }
}
