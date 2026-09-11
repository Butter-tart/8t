export const SAMPLE_RATE = 44100
export const TRIAL_TRACKS = 4
export const MAX_FRAMES = SAMPLE_RATE * 300
export const MAX_AUDIO_BYTES = 128 * 1024 * 1024

export interface Track {
  name: string
  audio: Float32Array<ArrayBuffer>
  volume: number
  pan: number
  muted: boolean
  solo: boolean
  metadata: Record<string, unknown>
}

export interface Song {
  tracks: Track[]
  master: number
  bpm: number
  lyrics: string
  metadata: Record<string, unknown>
  preserved: Record<string, Uint8Array<ArrayBuffer>>
  bypassed: boolean
}

export function newSong(): Song {
  return {
    tracks: Array.from({ length: 8 }, (_, index) => ({
      name: `Track ${index + 1}`, audio: new Float32Array(0),
      volume: 0.8, pan: 0, muted: false, solo: false, metadata: {},
    })),
    master: 0.8, bpm: 100, lyrics: '', metadata: {}, preserved: {}, bypassed: false,
  }
}

export function songLength(song: Song): number {
  return Math.max(0, ...song.tracks.map(track => track.audio.length))
}

export function audioBytes(song: Song): number {
  return song.tracks.reduce((total, track) => total + track.audio.byteLength, 0)
    + Object.values(song.preserved).reduce((total, entry) => total + entry.byteLength, 0)
}

export function overwrite(original: Float32Array, incoming: Float32Array, start: number): Float32Array<ArrayBuffer> {
  if (!Number.isInteger(start) || start < 0 || original.length > MAX_FRAMES || start + incoming.length > MAX_FRAMES) {
    throw new Error('The browser trial supports up to five minutes per track.')
  }
  if (!incoming.length) return new Float32Array(original)
  const result = new Float32Array(Math.max(original.length, start + incoming.length))
  result.set(original)
  result.set(incoming, start)
  return result
}

export function replaceAudio(song: Song, index: number, audio: Float32Array<ArrayBuffer>): Song {
  validateTrackIndex(index)
  if (audio.length > MAX_FRAMES) throw new Error('The browser trial supports up to five minutes per track.')
  if (audioBytes(song) - song.tracks[index].audio.byteLength + audio.byteLength > MAX_AUDIO_BYTES) {
    throw new Error('This project exceeds the browser edition\'s 128 MB audio budget.')
  }
  return { ...song, tracks: song.tracks.map((track, slot) => slot === index ? { ...track, audio } : track) }
}

export function validateTrackIndex(index: number): void {
  if (!Number.isInteger(index) || index < 0 || index >= TRIAL_TRACKS) {
    throw new Error('The browser trial supports tracks 1-4. Open the project in desktop for all eight tracks.')
  }
}

export function validateTrial(song: Song): void {
  if (song.tracks.length !== 8) throw new Error('A project must contain eight archive tracks.')
  if (songLength(song) > MAX_FRAMES) throw new Error('This project exceeds the five-minute browser trial limit. Open it in desktop.')
  if (audioBytes(song) > MAX_AUDIO_BYTES) throw new Error('Project exceeds the 128 MB browser audio budget.')
  const defaults = newSong()
  song.tracks.forEach((track, index) => {
    if (index >= TRIAL_TRACKS && (track.audio.length || track.name !== defaults.tracks[index].name
      || track.volume !== 0.8 || track.pan !== 0 || track.muted || track.solo)) {
      throw new Error('This project uses tracks 5-8. Open it in desktop; no tracks have been removed.')
    }
    const metadata = track.metadata
    if ((metadata.channels !== undefined && metadata.channels !== 1)
      || (Array.isArray(metadata.effects) && metadata.effects.length)
      || (Array.isArray(metadata.takes) && metadata.takes.length)
      || (metadata.channel && Object.values(metadata.channel).some(value => value !== 0))) {
      throw new Error('This project uses desktop effects, takes or stereo tracks. Open it in desktop.')
    }
  })
  const tape = song.metadata.tape as Record<string, unknown> | undefined
  if (song.bypassed || Object.keys(song.preserved).length
    || (Array.isArray(song.metadata.bounce_history) && song.metadata.bounce_history.length)
    || (tape && (tape.loop || tape.punch || tape.count_in || tape.marker_a || tape.marker_b
      || (tape.tape_speed !== undefined && tape.tape_speed !== 1)
      || (tape.markers && Object.keys(tape.markers).length)))) {
    throw new Error('This project uses desktop production tools. Open it in desktop; the original is unchanged.')
  }
}

export function trackGain(song: Song, index: number, exclude = -1): number {
  const track = song.tracks[index]
  return index === exclude || track.muted || (song.tracks.some(item => item.solo) && !track.solo)
    ? 0 : track.volume * song.master
}

export function mix(song: Song): [Float32Array<ArrayBuffer>, Float32Array<ArrayBuffer>] {
  const length = songLength(song)
  const left = new Float32Array(length)
  const right = new Float32Array(length)
  song.tracks.forEach((track, index) => {
    const gain = trackGain(song, index)
    const angle = (track.pan + 1) * Math.PI / 4
    const leftGain = gain * Math.cos(angle)
    const rightGain = gain * Math.sin(angle)
    for (let frame = 0; frame < track.audio.length; frame++) {
      left[frame] += track.audio[frame] * leftGain
      right[frame] += track.audio[frame] * rightGain
    }
  })
  return [left, right]
}

export function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`
}