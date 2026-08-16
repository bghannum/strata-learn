import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi, type Mock } from 'vitest'
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

const ATTEMPTS = {
  items: [
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
  ],
  total: 2,
}

const VERSION_1 = {
  id: 'g1',
  version: 1,
  generated_at: '2026-08-15T10:00:00Z',
  snapshot_id: 's1',
  commit_hash: '4f1c9ab0000000000000000000000000000000000',
}

const VERSION_2 = {
  id: 'g2',
  version: 2,
  generated_at: '2026-08-16T10:00:00Z',
  snapshot_id: 's2',
  commit_hash: '9de3b81000000000000000000000000000000000',
}

const EMPTY_DIFF = {
  from_version: 1,
  to_version: 2,
  from_snapshot_id: 's1',
  to_snapshot_id: 's2',
  from_commit: VERSION_1.commit_hash,
  to_commit: VERSION_2.commit_hash,
  subsystems: { added: [], removed: [], changed: [] },
  tradeoffs: { added: [], removed: [], changed: [] },
  pattern: { changed: false, pattern_before: null, pattern_after: null, confidence_before: null, confidence_after: null },
  dependencies: { edges_added: [], edges_removed: [] },
}

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
      // One version by default — the diff panel needs two before it renders.
      if (url.endsWith('/study-guides')) return jsonResponse([VERSION_1])
      // Carries a ?limit, so this can't be an endsWith like the others.
      if (url.includes('/attempts?')) return jsonResponse(ATTEMPTS)
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
    stubApi({ '/attempts?': jsonResponse({ items: [], total: 0 }) })
    renderPage()

    await waitFor(() => expect(screen.getByText(/No completed quizzes yet/)).toBeInTheDocument())
  })

  it('bounds the history and says how much it is not showing', async () => {
    // #75: retakes are unlimited, so the panel renders a recent window rather
    // than every sitting ever taken.
    const page = Array.from({ length: 10 }, (_unused, index) => ({
      id: `a${index}`,
      quiz_id: 'q1',
      completed_at: new Date(Date.now() - index * 60_000).toISOString(),
      score: 0.8,
      question_count: 5,
    }))
    stubApi({ '/attempts?': jsonResponse({ items: page, total: 23 }) })
    renderPage()

    await waitFor(() => expect(screen.getByText('showing 10 of 23')).toBeInTheDocument())
    expect(screen.getAllByRole('link', { name: /questions/ })).toHaveLength(10)
    expect(screen.getByRole('button', { name: 'Show all 23' })).toBeInTheDocument()
  })

  it('asks for the ceiling, not for everything, when the history is shown in full', async () => {
    stubApi({ '/attempts?': jsonResponse({ items: [], total: 400 }) })
    renderPage()

    // Past the API's own ceiling, "show all" would be a promise the endpoint
    // can't keep — the label says what it will actually do instead.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Show 100 most recent' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Show 100 most recent' }))

    const requested = () => (globalThis.fetch as Mock).mock.calls.map(([input]) => String(input))
    // The first load is bounded and the expanded one is still capped — the
    // escape hatch raises the ask, it doesn't remove it.
    expect(requested().some((url) => url.includes('/attempts?limit=10'))).toBe(true)
    await waitFor(() => expect(requested().some((url) => url.includes('/attempts?limit=100'))).toBe(true))
  })

  it('keeps the diff panel off a repo with only one version', async () => {
    // A repo indexed once has no history to diff against, and an empty picker
    // would be permanent furniture for a feature that can't do anything yet.
    stubApi()
    renderPage()

    await waitFor(() => expect(screen.getByText('Quiz history')).toBeInTheDocument())
    expect(screen.queryByText('What changed')).not.toBeInTheDocument()
  })

  it('compares the two newest versions by default once a re-index has produced one', async () => {
    stubApi({
      [`${REPO_ID}/study-guides`]: jsonResponse([VERSION_2, VERSION_1]),
      '/diff/': jsonResponse({
        ...EMPTY_DIFF,
        subsystems: {
          added: [{ key: 'app/worker', name: 'Background worker' }],
          removed: [],
          changed: [],
        },
        dependencies: { edges_added: [{ source: 'app/api', target: 'app/worker' }], edges_removed: [] },
      }),
    })
    renderPage()

    await waitFor(() => expect(screen.getByText('Background worker')).toBeInTheDocument())
    // Newest against the one before it, which is what "what did the last
    // re-index change" means.
    expect(screen.getByLabelText('First version to compare')).toHaveValue('g1')
    expect(screen.getByLabelText('Second version to compare')).toHaveValue('g2')
    expect(screen.getByText('app/api → app/worker')).toBeInTheDocument()
  })

  it('reads an identical re-index as "nothing changed", not as a broken panel', async () => {
    stubApi({
      [`${REPO_ID}/study-guides`]: jsonResponse([VERSION_2, VERSION_1]),
      '/diff/': jsonResponse(EMPTY_DIFF),
    })
    renderPage()

    await waitFor(() => expect(screen.getByText(/No architectural changes between v1 and v2/)).toBeInTheDocument())
  })

  it('says the comparison failed rather than sitting on "Loading…"', async () => {
    stubApi({
      [`${REPO_ID}/study-guides`]: jsonResponse([VERSION_2, VERSION_1]),
      '/diff/': jsonResponse({ detail: 'snapshot not found' }, 404),
    })
    renderPage()

    await waitFor(() => expect(screen.getByText('snapshot not found')).toBeInTheDocument())
  })

  it('says the history failed to load instead of sitting on "Loading…"', async () => {
    // Found via Codex's review of this change: the panel's catch used to
    // swallow the failure, leaving a spinner-equivalent on screen forever.
    stubApi({ '/attempts': jsonResponse({ detail: 'database is on fire' }, 500) })
    renderPage()

    await waitFor(() => expect(screen.getByText('database is on fire')).toBeInTheDocument())
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
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
