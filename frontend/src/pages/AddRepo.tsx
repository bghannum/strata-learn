import { useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createRepo, MAX_ZIP_UPLOAD_BYTES } from '../api/client'

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
    <main className="mx-auto max-w-lg p-6">
      <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Add a repository</h1>

      <div className="mt-4 flex gap-1 rounded-lg bg-gray-100 p-1 dark:bg-gray-800">
        {(['git_url', 'zip_upload'] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === t
                ? 'bg-white text-gray-900 shadow dark:bg-gray-700 dark:text-gray-100'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
            }`}
          >
            {t === 'git_url' ? 'Git URL' : 'Upload Zip'}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="mt-6 space-y-4">
        {tab === 'git_url' ? (
          <div>
            <label htmlFor="git-url" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Repository URL
            </label>
            <input
              id="git-url"
              type="text"
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
              onBlur={() => setGitUrlTouched(true)}
              placeholder="https://github.com/owner/repo.git"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-800"
            />
            {gitUrlTouched && gitUrl.length > 0 && !gitUrlValid && (
              <p className="mt-1 text-sm text-red-600 dark:text-red-400">Enter a valid http(s) URL.</p>
            )}
          </div>
        ) : (
          <div>
            <div
              onDragOver={(e) => {
                e.preventDefault()
                setIsDragging(true)
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={`rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
                isDragging ? 'border-blue-500 bg-blue-50 dark:bg-blue-950' : 'border-gray-300 dark:border-gray-600'
              }`}
            >
              {file ? (
                <p className="text-sm text-gray-700 dark:text-gray-300">
                  {file.name} <span className="text-gray-400">({formatBytes(file.size)})</span>
                </p>
              ) : (
                <p className="text-sm text-gray-500 dark:text-gray-400">Drag a .zip file here, or</p>
              )}
              <label className="mt-2 inline-block cursor-pointer text-sm font-medium text-blue-600 hover:underline dark:text-blue-400">
                choose a file
                <input
                  type="file"
                  accept=".zip"
                  className="hidden"
                  onChange={(e) => selectFile(e.target.files?.[0])}
                />
              </label>
            </div>
            {fileSizeError && <p className="mt-1 text-sm text-red-600 dark:text-red-400">{fileSizeError}</p>}
            {uploadProgress !== null && (
              <div className="mt-2">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
                  <div className="h-full bg-blue-600 transition-all" style={{ width: `${uploadProgress}%` }} />
                </div>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Uploading… {uploadProgress}%</p>
              </div>
            )}
          </div>
        )}

        <div>
          <label htmlFor="display-name" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            Display name <span className="text-gray-400">(optional)</span>
          </label>
          <input
            id="display-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-800"
          />
        </div>

        {error && (
          <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300">{error}</p>
        )}

        <button
          type="submit"
          disabled={!canSubmit || submitting}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Adding…' : 'Add repository'}
        </button>
      </form>
    </main>
  )
}

export default AddRepo
