import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import RubricCoverage from '../RubricCoverage'

const RUBRIC = ['the pipeline is slow', 'a request would time out', 'status is persisted']

describe('RubricCoverage', () => {
  afterEach(cleanup)

  it('shows the count and marks each point covered or missed', () => {
    render(<RubricCoverage rubric={RUBRIC} hits={[true, false, true]} />)
    expect(screen.getByText(/Key points — 2 of 3/)).toBeInTheDocument()
    // Screen-reader text carries the verdict; the glyph is aria-hidden.
    const items = screen.getAllByRole('listitem').map((li) => li.textContent)
    expect(items[0]).toContain('Covered: the pipeline is slow')
    expect(items[1]).toContain('Missed: a request would time out')
    expect(items[2]).toContain('Covered: status is persisted')
  })

  it('renders the points without marks or a count while coverage is withheld', () => {
    // End-of-quiz mode: the rubric may be shown after completion even if
    // hits are absent, and it must not imply a score it doesn't have.
    render(<RubricCoverage rubric={RUBRIC} hits={null} />)
    expect(screen.getByText('Key points')).toBeInTheDocument()
    expect(screen.queryByText(/of 3/)).not.toBeInTheDocument()
    const items = screen.getAllByRole('listitem').map((li) => li.textContent ?? '')
    expect(items.some((t) => /Covered:|Missed:/.test(t))).toBe(false)
    expect(items[0]).toContain('the pipeline is slow')
  })
})
