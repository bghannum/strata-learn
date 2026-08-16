import { useEffect, useState, type ReactNode } from 'react'
import {
  ApiError,
  getStudyGuideDiff,
  listStudyGuideVersions,
  type StudyGuideDiff,
  type StudyGuideVersion,
} from '../api/client'
import { Select } from './ui/Field'
import Tag from './ui/Tag'

/** A subsystem that gained or lost a lot of files would otherwise render its
 *  entire membership into the page — the same unbounded-by-construction shape
 *  as #75's quiz history, one layer down. Eight is enough to see *what kind* of
 *  files moved, which is the question this list answers; the exact roster is
 *  the study guide's job. */
const MAX_FILES_SHOWN = 8

/** "v2 · a1b2c3d" — the version is what the diff endpoint orders by, the commit
 *  is what makes it recognizable against the repo's own history. Zip uploads
 *  have no commit, so it degrades to the version alone. */
function versionLabel(version: StudyGuideVersion): string {
  return version.commit_hash ? `v${version.version} · ${version.commit_hash.slice(0, 7)}` : `v${version.version}`
}

function isEmpty(diff: StudyGuideDiff): boolean {
  const { subsystems, tradeoffs, pattern, dependencies } = diff
  return (
    subsystems.added.length === 0 &&
    subsystems.removed.length === 0 &&
    subsystems.changed.length === 0 &&
    tradeoffs.added.length === 0 &&
    tradeoffs.removed.length === 0 &&
    tradeoffs.changed.length === 0 &&
    !pattern.changed &&
    dependencies.edges_added.length === 0 &&
    dependencies.edges_removed.length === 0
  )
}

function Layer({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-organic-divider pt-3.5 first:border-t-0 first:pt-0">
      <h3 className="text-[13px] font-semibold">{title}</h3>
      <ul className="mt-2 flex flex-col gap-2">{children}</ul>
    </div>
  )
}

/** Added is sage (accent-2, this system's positive voice) and removed is
 *  neutral — deliberately not organic-danger, which is scoped to actual
 *  failures: a subsystem disappearing between two indexings is information,
 *  not an error. */
function FileList({ paths, added }: { paths: string[]; added: boolean }) {
  if (paths.length === 0) return null
  return (
    <ul className="mt-1 flex flex-col">
      {paths.slice(0, MAX_FILES_SHOWN).map((path) => (
        <li
          key={path}
          className={added ? 'font-mono text-xs text-organic-accent-2-800' : 'font-mono text-xs opacity-55 line-through'}
        >
          {added ? '+' : '−'} {path}
        </li>
      ))}
      {paths.length > MAX_FILES_SHOWN && (
        <li className="text-xs opacity-55">…and {paths.length - MAX_FILES_SHOWN} more</li>
      )}
    </ul>
  )
}

function Before({ children }: { children: ReactNode }) {
  return <span className="opacity-55">{children}</span>
}

function DiffBody({ diff }: { diff: StudyGuideDiff }) {
  const { subsystems, tradeoffs, pattern, dependencies } = diff

  if (isEmpty(diff)) {
    return (
      <p className="mt-3.5 text-[13px] leading-relaxed opacity-70">
        No architectural changes between v{diff.from_version} and v{diff.to_version}. The code may well have moved —
        this compares the subsystems, trade-offs, dependencies, and primary pattern the guide is built on, and those
        came out the same.
      </p>
    )
  }

  return (
    <div className="mt-3.5 flex flex-col gap-3.5">
      {/* Stated rather than implied: the endpoint decides direction by version,
      not by which select the user touched, so picking the newer one first
      still reads oldest → newest here. */}
      <p className="text-[13px] opacity-70">
        v{diff.from_version}
        {diff.from_commit && <span className="font-mono text-xs"> ({diff.from_commit.slice(0, 7)})</span>} → v
        {diff.to_version}
        {diff.to_commit && <span className="font-mono text-xs"> ({diff.to_commit.slice(0, 7)})</span>}
      </p>

      {pattern.changed && (
        <Layer title="Primary pattern">
          <li className="text-sm">
            <Before>{pattern.pattern_before ?? 'none detected'}</Before> → {pattern.pattern_after ?? 'none detected'}
            {(pattern.confidence_before || pattern.confidence_after) && (
              <span className="ml-2 text-xs opacity-55">
                confidence {pattern.confidence_before ?? '—'} → {pattern.confidence_after ?? '—'}
              </span>
            )}
          </li>
        </Layer>
      )}

      {(subsystems.added.length > 0 || subsystems.removed.length > 0 || subsystems.changed.length > 0) && (
        <Layer title="Subsystems">
          {subsystems.added.map((subsystem) => (
            <li key={`added-${subsystem.key}`} className="flex flex-wrap items-center gap-2 text-sm">
              <Tag variant="accent-2">Added</Tag>
              <span>{subsystem.name}</span>
              <span className="font-mono text-xs opacity-55">{subsystem.key}</span>
            </li>
          ))}
          {subsystems.removed.map((subsystem) => (
            <li key={`removed-${subsystem.key}`} className="flex flex-wrap items-center gap-2 text-sm">
              <Tag variant="neutral">Removed</Tag>
              <span className="line-through opacity-70">{subsystem.name}</span>
              <span className="font-mono text-xs opacity-55">{subsystem.key}</span>
            </li>
          ))}
          {subsystems.changed.map((subsystem) => (
            <li key={`changed-${subsystem.key}`} className="text-sm">
              <span className="flex flex-wrap items-center gap-2">
                <Tag variant="outline">Membership</Tag>
                <span>{subsystem.name}</span>
                <span className="font-mono text-xs opacity-55">{subsystem.key}</span>
              </span>
              <FileList paths={subsystem.files_added} added />
              <FileList paths={subsystem.files_removed} added={false} />
            </li>
          ))}
        </Layer>
      )}

      {(tradeoffs.added.length > 0 || tradeoffs.removed.length > 0 || tradeoffs.changed.length > 0) && (
        <Layer title="Trade-offs">
          {tradeoffs.added.map((decision) => (
            <li key={`added-${decision}`} className="flex flex-wrap items-start gap-2 text-sm">
              <Tag variant="accent-2">Added</Tag>
              <span className="min-w-0 flex-1">{decision}</span>
            </li>
          ))}
          {tradeoffs.removed.map((decision) => (
            <li key={`removed-${decision}`} className="flex flex-wrap items-start gap-2 text-sm">
              <Tag variant="neutral">Removed</Tag>
              <span className="min-w-0 flex-1 opacity-70">{decision}</span>
            </li>
          ))}
          {/* A card matches on the *files its evidence cites*, never on its
          prose (see backend/app/generation/diffing.py) — so "changed" here
          means the same decision is now explained differently, which is worth
          reading in full rather than as a one-line label. */}
          {tradeoffs.changed.map((change) => (
            <li key={change.evidence_paths.join('|')} className="text-sm">
              <span className="flex flex-wrap items-start gap-2">
                <Tag variant="outline">Reasoning</Tag>
                <span className="min-w-0 flex-1">
                  {change.decision_before === change.decision_after ? (
                    change.decision_after
                  ) : (
                    <>
                      <Before>{change.decision_before}</Before> → {change.decision_after}
                    </>
                  )}
                </span>
              </span>
              <dl className="mt-1.5 flex flex-col gap-1 text-[13px] leading-relaxed">
                <div>
                  <dt className="inline text-xs opacity-55">Was: </dt>
                  <dd className="inline opacity-55">{change.reasoning_before}</dd>
                </div>
                <div>
                  <dt className="inline text-xs opacity-55">Now: </dt>
                  <dd className="inline">{change.reasoning_after}</dd>
                </div>
                {change.cost_before !== change.cost_after && (
                  <div>
                    <dt className="inline text-xs opacity-55">Cost: </dt>
                    <dd className="inline">
                      <Before>{change.cost_before}</Before> → {change.cost_after}
                    </dd>
                  </div>
                )}
              </dl>
            </li>
          ))}
        </Layer>
      )}

      {(dependencies.edges_added.length > 0 || dependencies.edges_removed.length > 0) && (
        // Subsystem-level, not file-level: a refactor that moves twenty files
        // is one line here rather than forty that all say the same thing.
        <Layer title="Dependencies">
          {dependencies.edges_added.map((edge) => (
            <li key={`added-${edge.source}-${edge.target}`} className="flex flex-wrap items-center gap-2 text-sm">
              <Tag variant="accent-2">Added</Tag>
              <span className="font-mono text-xs">
                {edge.source} → {edge.target}
              </span>
            </li>
          ))}
          {dependencies.edges_removed.map((edge) => (
            <li key={`removed-${edge.source}-${edge.target}`} className="flex flex-wrap items-center gap-2 text-sm">
              <Tag variant="neutral">Removed</Tag>
              <span className="font-mono text-xs opacity-55 line-through">
                {edge.source} → {edge.target}
              </span>
            </li>
          ))}
        </Layer>
      )}
    </div>
  )
}

/** #72: the read side of re-indexing. The staleness banner and the re-index
 *  action live directly above this on RepoDetail, which completes the loop the
 *  Phase 7 checkpoint describes — "this is stale" → "re-index" → "here's what
 *  changed" — instead of leaving the diff endpoint reachable only by curl.
 *
 *  Renders nothing at all until a repo has two versions to compare, which is
 *  the common case: a repo indexed once has no history to diff against, and an
 *  empty picker would be a permanent piece of furniture for a feature that
 *  can't do anything yet.
 */
function ArchitecturalDiff({ repoId, guideId }: { repoId: string; guideId: string | undefined }) {
  const [versions, setVersions] = useState<StudyGuideVersion[] | null>(null)
  const [versionsError, setVersionsError] = useState<string | null>(null)
  const [fromId, setFromId] = useState<string | null>(null)
  const [toId, setToId] = useState<string | null>(null)
  const [diff, setDiff] = useState<StudyGuideDiff | null>(null)
  const [diffError, setDiffError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  // guideId is in the dependency list so a completed re-index — which is what
  // produces the second version this panel needs — brings the new version into
  // the picker without a page reload.
  useEffect(() => {
    if (!repoId) return
    setVersionsError(null)
    listStudyGuideVersions(repoId)
      .then((fetched) => {
        setVersions(fetched)
        if (fetched.length < 2) return
        // Newest against the one before it: "what did the last re-index
        // change" is the question someone arrives here with.
        setToId(fetched[0].id)
        setFromId(fetched[1].id)
      })
      .catch((err) =>
        setVersionsError(err instanceof ApiError ? err.message : 'Could not load this guide’s version history.'),
      )
  }, [repoId, guideId, reload])

  useEffect(() => {
    if (!fromId || !toId || fromId === toId) {
      setDiff(null)
      return
    }
    let current = true
    setDiffError(null)
    getStudyGuideDiff(fromId, toId)
      .then((fetched) => {
        // A slower earlier request must not overwrite a newer one's result —
        // both selects are live, so two changes in quick succession leave two
        // requests in flight.
        if (current) setDiff(fetched)
      })
      .catch((err) => {
        if (current) setDiffError(err instanceof ApiError ? err.message : 'Could not compare these versions.')
      })
    return () => {
      current = false
    }
  }, [fromId, toId])

  if (versionsError === null && (versions === null || versions.length < 2)) return null

  return (
    <section className="mt-5.5 rounded-[32px] bg-organic-surface p-7">
      <h2 className="text-lg font-semibold">What changed</h2>

      {versionsError ? (
        <div className="mt-3.5 rounded-2xl bg-organic-danger-bg p-3.5">
          <p className="text-sm text-organic-danger">{versionsError}</p>
          <button
            type="button"
            onClick={() => setReload((count) => count + 1)}
            className="mt-1.5 text-sm font-semibold underline"
          >
            Try again
          </button>
        </div>
      ) : (
        <>
          <p className="mt-1.5 text-[13px] opacity-70">
            Structure only — subsystems, trade-offs, dependencies, and the primary pattern. Generated wording is never
            compared, so re-running an unchanged repo reports nothing rather than churn.
          </p>

          <div className="mt-3.5 flex flex-wrap items-center gap-2.5">
            <label className="flex items-center gap-2 text-[13px]">
              <span className="opacity-70">Compare</span>
              {/* Not labelled "earlier"/"later": the endpoint sorts by version
              itself, so either order produces the same oldest → newest diff and
              promising otherwise would be a lie the UI can't keep. */}
              <Select
                aria-label="First version to compare"
                value={fromId ?? ''}
                onChange={(event) => setFromId(event.target.value)}
              >
                {versions?.map((version) => (
                  <option key={version.id} value={version.id}>
                    {versionLabel(version)}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <span className="opacity-70">with</span>
              <Select
                aria-label="Second version to compare"
                value={toId ?? ''}
                onChange={(event) => setToId(event.target.value)}
              >
                {versions?.map((version) => (
                  <option key={version.id} value={version.id}>
                    {versionLabel(version)}
                  </option>
                ))}
              </Select>
            </label>
          </div>

          {fromId === toId && <p className="mt-3.5 text-[13px] opacity-70">Pick two different versions to compare.</p>}

          {diffError && (
            <div className="mt-3.5 rounded-2xl bg-organic-danger-bg p-3.5">
              <p className="text-sm text-organic-danger">{diffError}</p>
            </div>
          )}

          {!diffError && fromId !== toId && diff === null && <p className="mt-3.5 text-[13px] opacity-55">Loading…</p>}
          {!diffError && diff !== null && <DiffBody diff={diff} />}
        </>
      )}
    </section>
  )
}

export default ArchitecturalDiff
