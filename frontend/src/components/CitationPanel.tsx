import { useState } from 'react'
import type { Citation } from '../api/client'
import { buttonClasses } from './ui/buttonVariants'
import Tag from './ui/Tag'

interface CitationPanelProps {
  citation: Citation | null
  onClose: () => void
}

function CitationPanel({ citation, onClose }: CitationPanelProps) {
  const [copied, setCopied] = useState(false)

  if (!citation) return null

  async function handleCopyPath() {
    await navigator.clipboard.writeText(citation!.file_path)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* tabIndex={-1} — found via #48 (the identical bug fixed in
      AppLayout.tsx's account-menu backdrop, Codex's PR #47 review): without
      it, a keyboard user opening this panel and pressing Tab lands on this
      full-screen, visually empty target instead of "Close". */}
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close citation panel"
        className="absolute inset-0 bg-organic-neutral-900/45"
        onClick={onClose}
      />
      <aside className="relative flex h-full w-full max-w-lg flex-col overflow-auto bg-organic-bg p-7 shadow-organic-lg">
        <div className="mb-4.5 flex items-center gap-2.5">
          <Tag variant="accent">Citation</Tag>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto cursor-pointer border-0 bg-transparent text-lg opacity-60 hover:opacity-100"
          >
            ×
          </button>
        </div>

        <h3 className="mb-1 font-mono text-base font-normal">{citation.file_path}</h3>
        <p className="mb-4 text-xs opacity-60">
          Lines {citation.line_start}–{citation.line_end}
        </p>

        <pre className="mb-4.5 overflow-auto rounded-2xl bg-organic-neutral-900 p-4 text-xs leading-relaxed text-organic-neutral-200">
          {citation.snippet_text}
        </pre>

        <p className="mb-4.5 text-sm leading-relaxed">{citation.claim_excerpt}</p>

        <button type="button" onClick={handleCopyPath} className={buttonClasses('secondary')}>
          {copied ? 'Copied!' : 'Copy path'}
        </button>
      </aside>
    </div>
  )
}

export default CitationPanel
