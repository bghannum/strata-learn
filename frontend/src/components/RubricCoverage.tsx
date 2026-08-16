import { cn } from './ui/cn'

interface RubricCoverageProps {
  rubric: string[]
  /** One per rubric point, or null when coverage is still withheld — the
   *  points render without marks. */
  hits: boolean[] | null
}

/** Which key points a short answer landed. Score is hits/total, so this
 *  *is* the score, made legible: a learner who got two of three sees which
 *  idea they missed, not just "67%". Sage for hit, neutral for missed —
 *  deliberately not organic-danger, which is scoped to failures; a missed
 *  idea is a study prompt, not an error. */
function RubricCoverage({ rubric, hits }: RubricCoverageProps) {
  const total = rubric.length
  const hitCount = hits ? hits.filter(Boolean).length : null
  return (
    <div>
      <p className="text-[10px] font-medium tracking-[0.1em] uppercase opacity-70">
        Key points{hitCount !== null && ` — ${hitCount} of ${total}`}
      </p>
      <ul className="mt-1.5 flex flex-col gap-1">
        {rubric.map((point, index) => {
          const hit = hits ? hits[index] : null
          return (
            <li key={index} className="flex items-start gap-2 text-sm">
              <span
                aria-hidden
                className={cn(
                  'mt-[3px] inline-flex size-4 shrink-0 items-center justify-center rounded-full text-[11px]',
                  hit === true && 'bg-organic-accent-2-100 text-organic-accent-2-800',
                  hit === false && 'bg-organic-neutral-200 text-organic-neutral-700',
                  hit === null && 'border border-organic-divider',
                )}
              >
                {hit === true ? '✓' : hit === false ? '–' : ''}
              </span>
              <span className={cn(hit === false && 'opacity-70')}>
                <span className="sr-only">{hit === true ? 'Covered: ' : hit === false ? 'Missed: ' : ''}</span>
                {point}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export default RubricCoverage
