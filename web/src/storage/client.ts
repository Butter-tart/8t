import type { Song } from '../model'

export function fileJob<T>(type: 'open' | 'save' | 'export' | 'import' | 'backup', value: Song | Uint8Array): Promise<T> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' })
    const timeout = setTimeout(() => { worker.terminate(); reject(new Error('File processing timed out. The current project is unchanged.')) }, 120000)
    const cleanup = () => { clearTimeout(timeout); worker.terminate() }
    worker.onmessage = ({ data }) => {
      cleanup()
      if (data.error) reject(new Error(data.error))
      else resolve(data.result as T)
    }
    worker.onerror = event => { cleanup(); reject(new Error(event.message || 'The file worker failed.')) }
    worker.postMessage({ type, value })
  })
}

export function download(bytes: Uint8Array<ArrayBuffer> | string, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([bytes], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}