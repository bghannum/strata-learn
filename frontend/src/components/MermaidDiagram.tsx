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
    // Reuses organic-danger rather than introducing a third bespoke warning
    // color — Organic has no warning role either, and "could not render"
    // reads as an error state to the reader regardless.
    return (
      <div className="rounded-2xl bg-organic-danger-bg p-3.5">
        <p className="text-sm text-organic-danger">Could not render this diagram: {error}</p>
        <pre className="mt-2 overflow-auto text-xs whitespace-pre-wrap text-organic-danger">{chart}</pre>
      </div>
    )
  }

  if (!svg) {
    return <p className="text-sm opacity-70">Rendering diagram…</p>
  }

  return <div className="overflow-auto" dangerouslySetInnerHTML={{ __html: svg }} />
}

export default MermaidDiagram
