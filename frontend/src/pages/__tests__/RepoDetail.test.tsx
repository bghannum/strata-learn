import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RepoDetail from '../RepoDetail'

const REPO_ID = '11111111-1111-1111-1111-111111111111'

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

const REPO = {
  id: REPO_ID,
  user_id: 'u1',
  source_type: 'git_url',
  source_uri: 'https://github.com/bghannum/control-loop-sim',
  display_name: 'control-loop-sim',
  created_at: '2026-08-15T10:00:00Z',
  latest_snapshot_id: 's1',
}

const SNAPSHOT = {
  id: 's1',
  repo_id: REPO_ID,
  commit_hash: '4f1c9ab0000000000000000000000000000000000',
  indexed_at: '2026-08-15T10:00:00Z',
  status: 'ready',
  file_count: 12,
  language_summary: {},
  dependency_graph: { nodes: [], edges: [] },
  entry_points: [],
}

function section(id: string, type: string, title: string, diagram: string | null, citations: number) {
  return {
    id,
    section_type: type,
    title,
    order: 0,
    content_md: '#',
    diagram_mermaid: diagram,
    citations: Array.from({ length: citations }, (_unused, index) => ({
      id: `${id}-c${index}`,
      file_path: 'a.py',
      line_start: 1,
      line_end: 2,
      claim_excerpt: 'claim',
      snippet_text: 'code',
    })),
  }
}

const GUIDE = {
  id: 'g1',
  repo_id: REPO_ID,
  snapshot_id: 's1',
  version: 1,
  generated_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  sections: [
    section('s-a', 'overview', 'Overview', null, 3),
    section('s-b', 'architecture', 'Architecture', 'graph TD;', 4),
    section('s-c', 'deep_dive', 'The scheduler', null, 2),
    section('s-d', 'deep_dive', 'The event loop', null, 1),
  ],
}

const ATTEMPTS = [
  {
    id: 'a1',
    quiz_id: 'q1',
    completed_at: new Date().toISOString(),
    score: 0.8,
    question_count: 5,
  },
  {
    id: 'a2',
    quiz_id: 'q1',
    completed_at: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
    score: 0.5,
    question_count: 8,
  },
]

/** Every request the page makes on mount for a ready repo. `overrides` swaps
 *  individual responses without restating the happy path. */
function stubApi(overrides: Record<string, Response> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      for (const [fragment, response] of Object.entries(overrides)) {
        if (url.includes(fragment)) return response
      }
      if (url.endsWith('/snapshot')) return jsonResponse(SNAPSHOT)
      if (url.endsWith('/study-guide')) return jsonResponse(GUIDE)
      if (url.endsWith('/attempts')) return jsonResponse(ATTEMPTS)
      if (url.endsWith('/mastery')) return jsonResponse({ completed_attempts: 0, buckets: [] })
      if (url.endsWith('/update-status')) return jsonResponse({ status: 'up_to_date', reason: null })
      // No quiz generated yet — the page treats a 404 here as "none exists".
      if (url.endsWith('/quiz')) return jsonResponse({ detail: 'no quiz generated yet' }, 404)
      return jsonResponse(REPO)
    }),
  )
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/repos/${REPO_ID}`]}>
      <Routes>
        <Route path="/repos/:repoId" element={<RepoDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RepoDetail', () => {
  afterEach(() => {
    // Explicit because vitest.config.ts sets globals: false — Testing
    // Library only auto-registers its cleanup when a global afterEach exists,
    // so without this each render's DOM stacks up and every query that should
    // match once matches once per test run so far.
    cleanup()
    vi.unstubAllGlobals()
  })

  it('summarizes the guide and collapses deep-dives into one chip', async () => {
    stubApi()
    renderPage()

    // 4 sections, 1 of them carrying a diagram, 10 citations between them.
    await waitFor(() => expect(screen.getByText(/4 sections · 1 diagrams · 10 citations/)).toBeInTheDocument())
    expect(screen.getByText(/generated 2 hours ago/)).toBeInTheDocument()
    expect(screen.getByText('Overview')).toBeInTheDocument()
    expect(screen.getByText('Architecture')).toBeInTheDocument()
    // Titles of individual deep-dives would turn the chip row into a wall.
    expect(screen.getByText('2 deep dives')).toBeInTheDocument()
    expect(screen.queryByText('The scheduler')).not.toBeInTheDocument()
  })

  it('lists quiz history newest first, linking each sitting to its results', async () => {
    stubApi()
    renderPage()

    await waitFor(() => expect(screen.getByText(/^Today,/)).toBeInTheDocument())
    const rows = screen.getAllByRole('link', { name: /questions/ })
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveAttribute('href', '/attempts/a1')
    expect(rows[0]).toHaveTextContent('5 questions')
    expect(rows[0]).toHaveTextContent('80%')
    expect(rows[1]).toHaveTextContent(/^Yesterday,/)
    expect(rows[1]).toHaveTextContent('50%')
  })

  it('says so plainly when no quiz has been completed', async () => {
    stubApi({ '/attempts': jsonResponse([]) })
    renderPage()

    await waitFor(() => expect(screen.getByText(/No completed quizzes yet/)).toBeInTheDocument())
  })

  it('offers the guide from the header as well as the panel', async () => {
    stubApi()
    renderPage()

    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'Open study guide' })).toHaveAttribute('href', '/study-guides/g1'),
    )
    expect(screen.getByRole('link', { name: 'Read it' })).toHaveAttribute('href', '/study-guides/g1')
  })

  it('keeps the guide and history panels off a repo that is still indexing', async () => {
    stubApi({ '/snapshot': jsonResponse({ ...SNAPSHOT, status: 'analyzing' }) })
    // A non-terminal status opens the progress WebSocket — stubbed, since
    // jsdom has no WebSocket and this test is about what renders, not the feed.
    vi.stubGlobal(
      'WebSocket',
      class {
        close() {}
      },
    )
    renderPage()

    await waitFor(() => expect(screen.getByText('Indexing')).toBeInTheDocument())
    expect(screen.queryByText('Study guide')).not.toBeInTheDocument()
    expect(screen.queryByText('Quiz history')).not.toBeInTheDocument()
  })
})
