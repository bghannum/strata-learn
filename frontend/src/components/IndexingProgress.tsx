import type { SnapshotStatus } from '../api/client'
import Button from './ui/Button'
import Tag from './ui/Tag'

// The backend's `parsing` status covers both cloning/extracting the source
// AND running Layer A structural analysis as one atomic phase (no separate
// status transition between them) — so unlike the UI spec's 5-label list
// ("Ingesting" / "Structural Analysis" / ...), this only distinguishes what
// the backend actually reports. Overclaiming a split the pipeline can't
// signal would just be a more precise-looking version of the same guess.
const STAGES: { label: string; note: string; statuses: SnapshotStatus[] }[] = [
  { label: 'Ingesting & Structural Analysis', note: 'clone, parse, tree-sitter', statuses: ['pending', 'parsing'] },
  { label: 'Semantic Analysis', note: 'LLM module summaries', statuses: ['analyzing'] },
  { label: 'Generating Study Guide', note: 'sections + diagrams', statuses: ['generating'] },
  { label: 'Ready', note: 'guide available', statuses: ['ready'] },
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
  'in-progress': 'bg-organic-accent animate-pulse motion-reduce:animate-none',
  complete: 'bg-organic-accent-2',
  failed: 'bg-organic-danger',
}

// Fill for the larger, glyph-bearing stepper dots — a step darker than
// DOT_CLASSES' plain indicator dots (700-tier, not 500) because these carry
// text on top of the fill: the mockup's literal 500-tier accent is only
// tuned to ~3:1, below the 4.5:1 small-text requirement a numeral/checkmark
// glyph needs. Same fix Button.tsx's primary variant already applies.
const GLYPH_FILL_CLASSES: Record<StageState, string> = {
  'not-started': 'border-2 border-organic-neutral-400 text-organic-text opacity-70',
  'in-progress': 'bg-organic-accent-700 text-organic-bg',
  complete: 'bg-organic-accent-2-700 text-organic-bg',
  failed: 'bg-organic-danger text-organic-bg',
}

const CHIP_VARIANT: Record<'running' | 'ready' | 'failed', 'neutral' | 'accent-2' | 'danger'> = {
  running: 'neutral',
  ready: 'accent-2',
  failed: 'danger',
}

const CHIP_DOT_CLASSES: Record<'running' | 'ready' | 'failed', string> = {
  running: DOT_CLASSES['in-progress'],
  ready: DOT_CLASSES.complete,
  failed: DOT_CLASSES.failed,
}

interface IndexingProgressProps {
  status: SnapshotStatus | undefined
  // The last non-terminal status seen before `failed` — lets the stepper
  // show *which* stage failed instead of a generic message (`status` alone
  // has already been overwritten to `failed` by the time this renders).
  lastNonTerminalStatus?: SnapshotStatus
  error?: string
  variant: 'chip' | 'stepper'
  // Only used by the stepper variant's failed state — the retry action
  // needs API access (reindexRepo) that this presentational component
  // doesn't have, so the caller owns the request and just hands in its
  // pending/error state to render inline with the rest of the failure card.
  onRetry?: () => void
  retrying?: boolean
  retryError?: string | null
}

function IndexingProgress({
  status,
  lastNonTerminalStatus,
  error,
  variant,
  onRetry,
  retrying,
  retryError,
}: IndexingProgressProps) {
  const isReady = status === 'ready'
  const isFailed = status === 'failed'
  const failedIndex =
    isFailed && lastNonTerminalStatus ? STAGES.findIndex((s) => s.statuses.includes(lastNonTerminalStatus)) : -1
  const failedStageLabel = failedIndex >= 0 ? STAGES[failedIndex].label : undefined
  const currentIndex = !isFailed && status ? STAGES.findIndex((s) => s.statuses.includes(status)) : -1

  if (variant === 'chip') {
    if (isFailed) {
      return (
        <Tag variant="danger" className="gap-1.5">
          <span className="h-2 w-2 rounded-full bg-organic-danger" />
          Failed
        </Tag>
      )
    }
    const stage = currentIndex >= 0 ? STAGES[currentIndex] : undefined
    const state = currentIndex >= 0 ? stageState(currentIndex, currentIndex, isReady) : 'not-started'
    return (
      <Tag variant={isReady ? 'accent-2' : 'neutral'} className="gap-1.5">
        <span className={`h-2 w-2 rounded-full ${DOT_CLASSES[state]}`} />
        {stage?.label ?? 'Queued'}
      </Tag>
    )
  }

  const chipKind = isFailed ? 'failed' : isReady ? 'ready' : 'running'
  const summary = isFailed
    ? failedIndex >= 0
      ? `Stopped at stage ${failedIndex + 1} of ${STAGES.length}`
      : 'Stopped'
    : isReady
      ? `All ${STAGES.length} stages complete`
      : currentIndex >= 0
        ? `Stage ${currentIndex + 1} of ${STAGES.length}`
        : undefined

  return (
    <div>
      <div className="mb-6.5 flex items-baseline gap-2.5">
        <h2 className="text-lg font-semibold">Indexing</h2>
        {summary && <span className="text-[12.5px] text-organic-text opacity-55">{summary}</span>}
        <Tag variant={CHIP_VARIANT[chipKind]} className="ml-auto gap-1.5">
          <span className={`h-2 w-2 rounded-full ${CHIP_DOT_CLASSES[chipKind]}`} />
          {isFailed ? 'Failed' : isReady ? 'Ready' : 'Working'}
        </Tag>
      </div>

      <div className="grid grid-cols-4 gap-0">
        {STAGES.map((stage, index) => {
          const state: StageState = isFailed
            ? index < failedIndex
              ? 'complete'
              : index === failedIndex
                ? 'failed'
                : 'not-started'
            : stageState(index, currentIndex, isReady)
          const prevComplete = isFailed ? index - 1 < failedIndex : index - 1 < currentIndex || isReady
          return (
            <div key={stage.label} className="relative flex flex-col items-center gap-2.5 px-2 text-center">
              {index > 0 && (
                <span
                  className={`absolute top-[17px] right-1/2 left-[-50%] h-0.5 ${
                    prevComplete ? 'bg-organic-accent-2-400' : 'bg-organic-neutral-300'
                  }`}
                />
              )}
              <div className="relative">
                {state === 'in-progress' && (
                  <span className="absolute inset-0 rounded-full border-2 border-organic-accent animate-ping motion-reduce:animate-none" />
                )}
                <div
                  className={`relative z-[2] grid size-[34px] place-items-center rounded-full text-[13px] font-bold ${GLYPH_FILL_CLASSES[state]}`}
                >
                  {state === 'complete' ? '✓' : state === 'failed' ? '!' : index + 1}
                </div>
              </div>
              <div>
                <div className="text-[13px] leading-tight font-semibold">{stage.label}</div>
                <div className="mt-0.5 text-[11.5px] text-organic-text opacity-55">{stage.note}</div>
              </div>
            </div>
          )
        })}
      </div>

      {isFailed && (
        <div className="mt-6.5 rounded-organic-md bg-organic-danger-bg p-4.5">
          <div className="mb-2 flex items-center gap-2.5">
            <span className="grid size-[18px] shrink-0 place-items-center rounded-full bg-organic-danger text-[11px] font-bold text-organic-bg">
              !
            </span>
            <strong className="text-sm text-organic-danger">
              {failedStageLabel ? `${failedStageLabel} stopped` : 'Indexing stopped'}
            </strong>
          </div>
          <pre className="mb-3.5 font-mono text-xs whitespace-pre-wrap text-organic-danger">
            {/* The backend only carries an error message on the live progress
            WebSocket, not on AnalysisSnapshot itself — a page load that missed
            that message (e.g. after a refresh) has no specific text to show. */}
            {error ?? 'No error detail is available — this page loaded after the failure message was sent.'}
          </pre>
          {onRetry && (
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={onRetry} disabled={retrying}>
                {retrying ? 'Retrying…' : 'Retry'}
              </Button>
              {retryError && <span className="text-[12.5px] text-organic-danger">{retryError}</span>}
            </div>
          )}
        </div>
      )}

      {!isFailed && !isReady && (
        <p className="mt-6.5 text-[13px] text-organic-text opacity-62">
          {currentIndex >= 0 ? STAGES[currentIndex].label : 'Queued'} — you can close this tab, we'll keep going and
          the shelf will show it as ready.
        </p>
      )}
    </div>
  )
}

export default IndexingProgress
