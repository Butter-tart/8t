import { decodeProject, decodeWav, encodeProject, exportMix } from './project'
import type { Song } from '../model'

self.onmessage = ({ data }: MessageEvent<{ type: string; value: Song | Uint8Array }>) => {
  try {
    let result: Song | Uint8Array<ArrayBuffer> | Float32Array<ArrayBuffer>
    switch (data.type) {
      case 'open': result = decodeProject(data.value as Uint8Array); break
      case 'save': result = encodeProject(data.value as Song); break
      case 'backup': result = encodeProject(data.value as Song, true); break
      case 'export': result = exportMix(data.value as Song); break
      case 'import': result = decodeWav(data.value as Uint8Array); break
      default: throw new Error('Unknown file operation.')
    }
    self.postMessage({ result })
  } catch (error) {
    self.postMessage({ error: error instanceof Error ? error.message : String(error) })
  }
}