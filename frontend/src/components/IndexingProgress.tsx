import type { SnapshotStatus } from '../api/client'

// The backend's `parsing` status covers both cloning/extracting the source
// AND running Layer A structural analysis as one atomic phase (no separate
// status transition between them) — so unlike the UI spec's 5-label list
// ("Ingesting" / "Structural Analysis" / ...), this only distinguishes what
// the backend actually reports. Overclaiming a split the pipeline can't
// signal would just be a more precise-looking version of the same guess.
const STAGES: { label: string; statuses: SnapshotStatus[] }[] = [
  { label: 'Ingesting & Structural Analysis', statuses: ['pending', 'parsing'] },
  { label: 'Semantic Analysis', statuses: ['analyzing'] },
  { label: 'Generating Study Guide', statuses: ['generating'] },
  { label: 'Ready', statuses: ['ready'] },
]

type StageState = 'not-started' | 'in-progress' | 'complete' | 'failed'

function stageState(stageIndex: number, currentIndex: number, isReady: boolean): StageState {
  if (stageIndex < currentIndex) return 'complete'
  if (stageIndex === currentIndex) return isReady ? 'complete' : 'in-progress'
  return 'not-started'
}

const DOT_CLASSES: Record<StageState, string> = {
  'not-started': 'bg-gray-300 dark:bg-gray-600',
  'in-progress': 'bg-blue-500 animate-pulse',
  complete: 'bg-green-500',
  failed: 'bg-red-500',
}

interface IndexingProgressProps {
  status: SnapshotStatus | undefined
  // The last non-terminal status seen before `failed` — lets the stepper
  // show *which* stage failed instead of a generic message (`status` alone
  // has already been overwritten to `failed` by the time this renders).
  lastNonTerminalStatus?: SnapshotStatus
  error?: string
  variant: 'chip' | 'stepper'
}

function IndexingProgress({ status, lastNonTerminalStatus, error, variant }: IndexingProgressProps) {
  if (status === 'failed') {
    const failedIndex = lastNonTerminalStatus
      ? STAGES.findIndex((s) => s.statuses.includes(lastNonTerminalStatus))
      : -1
    const failedStageLabel = failedIndex >= 0 ? STAGES[failedIndex].label : undefined

    if (variant === 'chip') {
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-medium text-red-800 dark:bg-red-950 dark:text-red-300">
          <span className="h-2 w-2 rounded-full bg-red-500" />
          Failed
        </span>
      )
    }

    return (
      <div>
        <ol className="flex flex-wrap items-center gap-x-2 gap-y-3">
          {STAGES.map((stage, index) => {
            const state: StageState = index < failedIndex ? 'complete' : index === failedIndex ? 'failed' : 'not-started'
            return (
              <li key={stage.label} className="flex items-center gap-2">
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT_CLASSES[state]}`} />
                <span
                  className={
                    state === 'not-started'
                      ? 'text-sm text-gray-400 dark:text-gray-500'
                      : 'text-sm font-medium text-gray-900 dark:text-gray-100'
                  }
                >
                  {stage.label}
                </span>
                {index < STAGES.length - 1 && <span className="mx-1 h-px w-6 bg-gray-300 dark:bg-gray-600" />}
              </li>
            )
          })}
        </ol>
        <div className="mt-3 rounded-md border border-red-300 bg-red-50 p-4 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          <p className="font-medium">
            Indexing failed{failedStageLabel ? ` during ${failedStageLabel}` : ''}
          </p>
          <p className="mt-1 text-sm">
            {/* The backend only carries an error message on the live progress
            WebSocket, not on AnalysisSnapshot itself — a page load that missed
            that message (e.g. after a refresh) has no specific text to show. */}
            {error ?? 'No error detail is available — this page loaded after the failure message was sent.'}
          </p>
        </div>
      </div>
    )
  }

  const currentIndex = status ? STAGES.findIndex((s) => s.statuses.includes(status)) : -1
  const isReady = status === 'ready'

  if (variant === 'chip') {
    const stage = currentIndex >= 0 ? STAGES[currentIndex] : undefined
    const state = currentIndex >= 0 ? stageState(currentIndex, currentIndex, isReady) : 'not-started'
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-300">
        <span className={`h-2 w-2 rounded-full ${DOT_CLASSES[state]}`} />
        {stage?.label ?? 'Queued'}
      </span>
    )
  }

  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-3">
      {STAGES.map((stage, index) => {
        const state = stageState(index, currentIndex, isReady)
        return (
          <li key={stage.label} className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT_CLASSES[state]}`} />
            <span
              className={
                state === 'not-started'
                  ? 'text-sm text-gray-400 dark:text-gray-500'
                  : 'text-sm font-medium text-gray-900 dark:text-gray-100'
              }
            >
              {stage.label}
            </span>
            {index < STAGES.length - 1 && <span className="mx-1 h-px w-6 bg-gray-300 dark:bg-gray-600" />}
          </li>
        )
      })}
    </ol>
  )
}

export default IndexingProgress
