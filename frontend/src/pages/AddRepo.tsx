import { useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createRepo, MAX_ZIP_UPLOAD_BYTES } from '../api/client'
import Button from '../components/ui/Button'
import { buttonClasses } from '../components/ui/buttonVariants'
import { cn } from '../components/ui/cn'
import { Field, Input } from '../components/ui/Field'

type Tab = 'git_url' | 'zip_upload'

function isValidHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function AddRepo() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('git_url')
  const [gitUrl, setGitUrl] = useState('')
  const [gitUrlTouched, setGitUrlTouched] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [fileSizeError, setFileSizeError] = useState<string | null>(null)
  const [displayName, setDisplayName] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const gitUrlValid = isValidHttpUrl(gitUrl)
  const canSubmit = tab === 'git_url' ? gitUrlValid : file !== null && !fileSizeError

  function selectFile(selected: File | undefined) {
    if (!selected) {
      setFile(null)
      setFileSizeError(null)
      return
    }
    if (selected.size > MAX_ZIP_UPLOAD_BYTES) {
      // Rejected here, before ever starting the upload — the backend
      // enforces this too, but failing after transferring the whole file
      // just to get the same 422 wastes the person's bandwidth.
      setFile(selected)
      setFileSizeError(`This file is ${formatBytes(selected.size)}, over the ${formatBytes(MAX_ZIP_UPLOAD_BYTES)} limit.`)
      return
    }
    setFile(selected)
    setFileSizeError(null)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit || submitting) return
    setSubmitting(true)
    setError(null)
    setUploadProgress(tab === 'zip_upload' ? 0 : null)
    try {
      const repo =
        tab === 'git_url'
          ? await createRepo({ sourceType: 'git_url', gitUrl, displayName: displayName || undefined })
          : await createRepo(
              { sourceType: 'zip_upload', file: file!, displayName: displayName || undefined },
              setUploadProgress,
            )
      navigate(`/repos/${repo.id}`)
    } catch (err) {
      // Surfaced verbatim, not paraphrased — the backend's message (unreachable
      // URL, malformed zip, etc.) is more specific than anything generic here.
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
      setSubmitting(false)
      setUploadProgress(null)
    }
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragging(false)
    selectFile(e.dataTransfer.files[0])
  }

  return (
    <main className="mx-auto max-w-xl p-7">
      <h1 className="mb-1.5 text-[34px] leading-tight">Add a repo</h1>
      <p className="mb-5.5 text-[14.5px] leading-relaxed opacity-70">
        Indexing usually takes a few minutes. You don't have to sit and watch it.
      </p>

      {/* Organic's segmented control (.seg/.seg-opt), translated with
      has-[:checked]: rather than a raw --color-organic-accent glow to match
      the source's `:has(input:checked)` treatment. */}
      <div className="mb-5 inline-flex overflow-hidden rounded-full border border-organic-divider">
        {(['git_url', 'zip_upload'] as const).map((t, index) => (
          <label
            key={t}
            className={cn(
              'cursor-pointer px-3.5 py-2 text-[13px] has-[:checked]:bg-organic-accent-700 has-[:checked]:text-organic-bg has-[:focus-visible]:outline-2 has-[:focus-visible]:-outline-offset-2 has-[:focus-visible]:outline-organic-accent',
              index > 0 && 'border-l border-organic-divider',
            )}
          >
            <input type="radio" name="source" className="sr-only" checked={tab === t} onChange={() => setTab(t)} />
            {t === 'git_url' ? 'Git URL' : 'Upload zip'}
          </label>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4.5 rounded-[32px] bg-organic-surface p-6">
        {tab === 'git_url' ? (
          <Field label="Repository URL" htmlFor="git-url">
            <Input
              id="git-url"
              type="text"
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
              onBlur={() => setGitUrlTouched(true)}
              placeholder="https://github.com/owner/repo.git"
            />
            {gitUrlTouched && gitUrl.length > 0 && !gitUrlValid && (
              <p className="mt-2 text-[12.5px] text-organic-accent-700">Enter a valid http(s) URL.</p>
            )}
          </Field>
        ) : (
          <div>
            <div
              onDragOver={(e) => {
                e.preventDefault()
                setIsDragging(true)
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={cn(
                'flex flex-col items-center gap-2.5 rounded-[28px] border-2 border-dashed p-9 text-center transition-colors',
                isDragging ? 'border-organic-accent bg-organic-accent-100' : 'border-organic-neutral-400',
              )}
            >
              <div className="grid size-14 place-items-center rounded-full bg-organic-accent-2-200 text-xl text-organic-accent-2-700">
                ↓
              </div>
              {file ? (
                <p className="font-mono text-[12.5px]">
                  {file.name} <span className="opacity-60">({formatBytes(file.size)})</span>
                </p>
              ) : (
                <p className="text-sm font-semibold">Drop a .zip here</p>
              )}
              {/* buttonClasses() is a <button>-oriented style, applied here to a
              <span> so the actual interactive element stays a native
              <label>+file <input>, keeping keyboard/native file-picker
              behavior. */}
              <label>
                <span className={buttonClasses('secondary')}>Choose a file</span>
                <input type="file" accept=".zip" className="sr-only" onChange={(e) => selectFile(e.target.files?.[0])} />
              </label>
            </div>
            {fileSizeError && <p className="mt-2 text-[12.5px] text-organic-danger">{fileSizeError}</p>}
            {uploadProgress !== null && (
              <div className="mt-2.5 flex items-center gap-3 rounded-2xl bg-organic-neutral-100 px-4 py-3">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-organic-neutral-300">
                  <div className="h-full rounded-full bg-organic-accent-700 transition-all" style={{ width: `${uploadProgress}%` }} />
                </div>
                <p className="text-xs opacity-70">{uploadProgress}%</p>
              </div>
            )}
          </div>
        )}

        <Field label="Display name" htmlFor="display-name">
          <Input id="display-name" type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </Field>

        {error && (
          <div className="rounded-2xl bg-organic-danger-bg p-3.5">
            <p className="text-sm text-organic-danger">{error}</p>
          </div>
        )}

        <Button type="submit" block disabled={!canSubmit || submitting}>
          {submitting ? 'Adding…' : 'Add repository'}
        </Button>
      </form>
    </main>
  )
}

export default AddRepo
