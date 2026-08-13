import { useState } from 'react'
import type { Citation } from '../api/client'

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
      <button
        type="button"
        aria-label="Close citation panel"
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
      />
      <div className="relative flex h-full w-full max-w-lg flex-col overflow-auto bg-white p-6 shadow-xl dark:bg-gray-900">
        <button
          type="button"
          onClick={onClose}
          className="self-end text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        >
          Close
        </button>

        <div className="mt-2 flex items-center justify-between gap-2">
          <p className="font-mono text-sm text-gray-900 dark:text-gray-100">
            {citation.file_path}:{citation.line_start}-{citation.line_end}
          </p>
          <button
            type="button"
            onClick={handleCopyPath}
            className="shrink-0 rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            {copied ? 'Copied!' : 'Copy path'}
          </button>
        </div>

        <p className="mt-3 text-sm text-gray-600 dark:text-gray-400">{citation.claim_excerpt}</p>

        {/* No syntax-highlighting library added this phase — plain monospace
        text, a deliberate v1 simplification (see the phase plan). */}
        <pre className="mt-4 overflow-auto rounded-md bg-gray-900 p-3 text-xs text-gray-100">
          {citation.snippet_text}
        </pre>
      </div>
    </div>
  )
}

export default CitationPanel
