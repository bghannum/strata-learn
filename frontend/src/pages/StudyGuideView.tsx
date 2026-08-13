import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import Markdown from 'react-markdown'
import { ApiError, getStudyGuide, type Citation, type StudyGuide } from '../api/client'
import MermaidDiagram from '../components/MermaidDiagram'
import CitationPanel from '../components/CitationPanel'

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
      <main className="mx-auto max-w-4xl p-6">
        <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>
      </main>
    )
  }

  if (error || !guide) {
    return (
      <main className="mx-auto max-w-4xl p-6">
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">
          {error ?? 'Study guide not found.'}
        </p>
      </main>
    )
  }

  return (
    <main className="mx-auto flex max-w-5xl gap-8 p-6">
      <nav className="sticky top-6 h-fit w-44 shrink-0">
        <ul className="space-y-1 text-sm">
          {guide.sections.map((section) => (
            <li key={section.id}>
              <a
                href={`#section-${section.section_type}`}
                className="block text-gray-600 hover:text-blue-600 dark:text-gray-400 dark:hover:text-blue-400"
              >
                {section.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="min-w-0 flex-1">
        {guide.sections.map((section) => (
          <details key={section.id} id={`section-${section.section_type}`} open className="border-b border-gray-200 py-4 dark:border-gray-700">
            <summary className="cursor-pointer text-lg font-semibold text-gray-900 dark:text-gray-100">
              {section.title}
            </summary>

            <div className="prose prose-sm dark:prose-invert mt-3 max-w-none">
              <Markdown>{section.content_md}</Markdown>
            </div>

            {section.diagram_mermaid && (
              <div className="mt-4">
                <MermaidDiagram chart={section.diagram_mermaid} />
              </div>
            )}

            {section.citations.length > 0 && (
              <div className="mt-4">
                <p className="text-xs font-medium tracking-wide text-gray-400 uppercase">Citations</p>
                <ul className="mt-1 space-y-1">
                  {section.citations.map((citation) => (
                    <li key={citation.id}>
                      <button
                        type="button"
                        onClick={() => setActiveCitation(citation)}
                        className="text-left text-sm text-blue-600 hover:underline dark:text-blue-400"
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
