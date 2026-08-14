import type { SnapshotStatus } from '../api/client'
import Tag from './ui/Tag'

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

// Same color/icon language everywhere this renders (Dashboard.tsx's chip and
// RepoDetail.tsx's stepper) per ui-spec.md §8 — accent-2 (sage) reads as the
// system's positive/complete voice since Organic has no dedicated success
// color, and organic-danger is the one deliberately added non-mockup color
// (see organic.css), used only for this failed state.
const DOT_CLASSES: Record<StageState, string> = {
  'not-started': 'bg-organic-neutral-400',
  'in-progress': 'bg-organic-accent animate-pulse',
  complete: 'bg-organic-accent-2',
  failed: 'bg-organic-danger',
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
        <Tag variant="danger" className="gap-1.5">
          <span className="h-2 w-2 rounded-full bg-organic-danger" />
          Failed
        </Tag>
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
                {/* opacity-70, not -45 — found via Codex's PR #47 review class:
                -45 is 2.72:1 on organic-bg, below the 4.5:1 small-text
                requirement; -70 clears it (5.67:1). Not a disabled control
                (WCAG's contrast exemption doesn't apply), just a
                not-yet-reached stage label a sighted low-vision user still
                needs to read. */}
                <span
                  className={
                    state === 'not-started'
                      ? 'text-sm text-organic-text opacity-70'
                      : 'text-sm font-medium text-organic-text'
                  }
                >
                  {stage.label}
                </span>
                {index < STAGES.length - 1 && <span className="mx-1 h-px w-6 bg-organic-divider" />}
              </li>
            )
          })}
        </ol>
        <div className="mt-3 rounded-organic-md border border-organic-danger/30 bg-organic-danger-bg p-4 text-organic-danger">
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
      <Tag variant="neutral" className="gap-1.5">
        <span className={`h-2 w-2 rounded-full ${DOT_CLASSES[state]}`} />
        {stage?.label ?? 'Queued'}
      </Tag>
    )
  }

  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-3">
      {STAGES.map((stage, index) => {
        const state = stageState(index, currentIndex, isReady)
        return (
          <li key={stage.label} className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT_CLASSES[state]}`} />
            {/* opacity-70, not -45 — see the failed-branch stepper above for why. */}
            <span
              className={
                state === 'not-started' ? 'text-sm text-organic-text opacity-70' : 'text-sm font-medium text-organic-text'
              }
            >
              {stage.label}
            </span>
            {index < STAGES.length - 1 && <span className="mx-1 h-px w-6 bg-organic-divider" />}
          </li>
        )
      })}
    </ol>
  )
}

export default IndexingProgress
