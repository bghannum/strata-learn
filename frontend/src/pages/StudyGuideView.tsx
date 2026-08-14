import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import Markdown from 'react-markdown'
import { ApiError, getStudyGuide, type Citation, type StudyGuide } from '../api/client'
import MermaidDiagram from '../components/MermaidDiagram'
import CitationPanel from '../components/CitationPanel'
import { buttonClasses } from '../components/ui/buttonVariants'

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function StudyGuideView() {
  const { studyGuideId } = useParams<{ studyGuideId: string }>()
  const [guide, setGuide] = useState<StudyGuide | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null)

  useEffect(() => {
    if (!studyGuideId) return
    getStudyGuide(studyGuideId)
      .then(setGuide)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the study guide.'))
      .finally(() => setLoading(false))
  }, [studyGuideId])

  if (loading) {
    return (
      <main className="mx-auto max-w-4xl p-7">
        <p className="text-sm opacity-70">Loading…</p>
      </main>
    )
  }

  if (error || !guide) {
    return (
      <main className="mx-auto max-w-4xl p-7">
        <div className="rounded-2xl bg-organic-danger-bg p-3.5">
          <p className="text-sm text-organic-danger">{error ?? 'Study guide not found.'}</p>
        </div>
      </main>
    )
  }

  return (
    <main className="mx-auto flex max-w-5xl gap-8 p-7">
      <nav className="sticky top-20 flex h-fit w-[190px] shrink-0 flex-col gap-1">
        {guide.sections.map((section) => (
          <a
            key={section.id}
            href={`#section-${section.section_type}`}
            className="rounded-full px-3 py-1.5 text-sm opacity-70 hover:bg-[color-mix(in_srgb,var(--color-organic-text)_6%,transparent)] hover:opacity-100"
          >
            {section.title}
          </a>
        ))}
        {/* The full generate-quiz flow (poll for readiness, recover an
        in-flight generation, etc.) already lives on RepoDetail.tsx — this
        links there rather than duplicating that state management, while
        still giving ui-spec §6.3's "persistent entry point to quiz
        generation, visible while reading" from this screen too. */}
        <Link to={`/repos/${guide.repo_id}`} className={`mt-4 ${buttonClasses('primary')}`}>
          Generate quiz
        </Link>
      </nav>

      <div className="min-w-0 flex-1">
        {guide.sections.map((section) => (
          <details
            key={section.id}
            id={`section-${section.section_type}`}
            open
            className="border-b border-organic-divider py-5"
          >
            <summary className="cursor-pointer text-lg font-semibold">{section.title}</summary>

            <div className="prose prose-sm mt-3 max-w-none">
              <Markdown>{section.content_md}</Markdown>
            </div>

            {section.diagram_mermaid && (
              <div className="mt-4">
                <MermaidDiagram chart={section.diagram_mermaid} />
              </div>
            )}

            {/* Per docs/design/ui-spec.md §5.1: citations render as a
            per-section list, not inline markers anchored to the claim text
            — generated claim_excerpt values can't always be anchored to a
            literal substring. A known, documented gap, not something this
            phase's visual pass resolves. */}
            {section.citations.length > 0 && (
              <div className="mt-4">
                <p className="text-[10px] font-medium tracking-[0.1em] text-organic-accent-700 uppercase">
                  Citations
                </p>
                <ul className="mt-1.5 flex flex-col gap-1">
                  {section.citations.map((citation) => (
                    <li key={citation.id}>
                      <button
                        type="button"
                        onClick={() => setActiveCitation(citation)}
                        className="cursor-pointer border-0 bg-transparent p-0 text-left text-sm text-organic-accent-700 hover:underline"
                      >
                        <span className="font-mono">
                          {citation.file_path}:{citation.line_start}-{citation.line_end}
                        </span>{' '}
                        — {truncate(citation.claim_excerpt, 80)}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </details>
        ))}
      </div>

      <CitationPanel citation={activeCitation} onClose={() => setActiveCitation(null)} />
    </main>
  )
}

export default StudyGuideView
