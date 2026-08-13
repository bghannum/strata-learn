import { useEffect, useId, useState } from 'react'
import mermaid from 'mermaid'

let initialized = false
function ensureInitialized() {
  if (initialized) return
  // securityLevel 'strict' (mermaid's own default, set explicitly here) HTML
  // -encodes label content before rendering — the diagram text is
  // LLM-generated (diagram_builder.py's node labels), so this is what makes
  // the dangerouslySetInnerHTML below safe against a crafted label.
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' })
  initialized = true
}

interface MermaidDiagramProps {
  chart: string
}

function MermaidDiagram({ chart }: MermaidDiagramProps) {
  const rawId = useId()
  const renderId = `mermaid-${rawId.replace(/:/g, '')}`
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    ensureInitialized()
    let cancelled = false
    setSvg(null)
    setError(null)
    mermaid
      .render(renderId, chart)
      .then(({ svg: rendered }) => {
        if (!cancelled) setSvg(rendered)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not render diagram.')
      })
    return () => {
      cancelled = true
    }
  }, [chart, renderId])

  if (error) {
    // Belt-and-suspenders: Phase 3's tests guard Mermaid syntax validity at
    // generation time, but the text is still LLM-influenced — a render
    // failure here shows the raw source instead of a blank, silent gap.
    return (
      <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
        <p>Could not render this diagram: {error}</p>
        <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs">{chart}</pre>
      </div>
    )
  }

  if (!svg) {
    return <p className="text-sm text-gray-500 dark:text-gray-400">Rendering diagram…</p>
  }

  return <div className="overflow-auto" dangerouslySetInnerHTML={{ __html: svg }} />
}

export default MermaidDiagram
