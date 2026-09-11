import { strToU8, unzipSync, zipSync } from 'fflate'
import wavefile from 'wavefile'
import { MAX_AUDIO_BYTES, MAX_FRAMES, mix, newSong, SAMPLE_RATE, validateTrial } from '../model'
import type { Song } from '../model'

const MAX_METADATA = 2_000_000
const { WaveFile } = wavefile
const MAX_ARCHIVE = 160 * 1024 * 1024
const decoder = new TextDecoder('utf-8', { fatal: true })

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid project metadata.')
  return value as Record<string, unknown>
}

function level(value: unknown, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) throw new Error('Invalid mixer value in project.')
  return value
}

function text(value: unknown, max: number): string {
  if (typeof value !== 'string' || value.length > max) throw new Error('Invalid track name in project.')
  return value
}

function bool(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Invalid mixer switch in project.')
  return value
}

export function encodeWav(channels: Float32Array[], rate = SAMPLE_RATE, pcm = false): Uint8Array<ArrayBuffer> {
  const wav = new WaveFile()
  wav.fromScratch(channels.length, rate, '32f', channels.length === 1 ? channels[0] : channels)
  if (pcm) wav.toBitDepth('24')
  return new Uint8Array(wav.toBuffer())
}

export function decodeWav(bytes: Uint8Array, project = false): Float32Array<ArrayBuffer> {
  const wav = new WaveFile(bytes)
  const format = { ...wav.fmt } as { sampleRate: number; numChannels: number; bitsPerSample: number; blockAlign: number }
  const data = wav.data as { samples: Uint8Array }
  if (!Number.isInteger(format.sampleRate) || format.sampleRate < 8000 || format.sampleRate > 192000
    || ![1, 2].includes(format.numChannels) || format.blockAlign <= 0
    || data.samples.length / format.blockAlign / format.sampleRate > MAX_FRAMES / SAMPLE_RATE) {
    throw new Error('Use a mono or stereo WAV no longer than five minutes.')
  }
  if (project && (format.sampleRate !== SAMPLE_RATE || format.numChannels !== 1)) throw new Error('Project tracks must be mono 44.1 kHz WAV audio.')
  if (wav.bitDepth !== '32f') wav.toBitDepth('32f')
  if (format.sampleRate !== SAMPLE_RATE) wav.toSampleRate(SAMPLE_RATE, { method: 'sinc' })
  const interleaved = wav.getSamples(true, Float32Array)
  const output = new Float32Array(interleaved.length / format.numChannels)
  if (output.length > MAX_FRAMES) throw new Error('Audio exceeds the five-minute trial limit.')
  for (let frame = 0; frame < output.length; frame++) {
    let sum = 0
    for (let channel = 0; channel < format.numChannels; channel++) {
      const sample = interleaved[frame * format.numChannels + channel]
      if (!Number.isFinite(sample)) throw new Error('Audio contains invalid samples.')
      sum += sample / format.numChannels
    }
    output[frame] = sum
  }
  return output
}

export function encodeProject(song: Song, recoveryBackup = false): Uint8Array<ArrayBuffer> {
  if (!recoveryBackup) validateTrial(song)
  else if (song.tracks.length !== 8 || song.tracks.some(track => track.audio.length > SAMPLE_RATE * 600)
    || song.tracks.reduce((bytes, track) => bytes + track.audio.byteLength, 0)
      + Object.values(song.preserved).reduce((bytes, entry) => bytes + entry.byteLength, 0) > MAX_AUDIO_BYTES) {
    throw new Error('Recovery exceeds archive limits.')
  }
  const lyrics = strToU8(song.lyrics)
  if (lyrics.length > MAX_METADATA) throw new Error('Songwriting text exceeds 2 MB.')
  const metadata = { ...song.metadata, version: 4, sample_rate: SAMPLE_RATE, master: song.master, bpm: song.bpm,
    tracks: song.tracks.map(track => ({ ...track.metadata, name: track.name, volume: track.volume,
      pan: track.pan, muted: track.muted, solo: track.solo })),
  }
  const encoded = strToU8(JSON.stringify(metadata))
  if (encoded.length > MAX_METADATA) throw new Error('Project metadata exceeds 2 MB.')
  const entries: Record<string, Uint8Array> = { ...song.preserved, 'song.json': encoded, 'lyrics.txt': lyrics }
  song.tracks.forEach((track, index) => {
    if (track.audio.length) entries[`track-${index}.wav`] = encodeWav([track.audio])
  })
  return new Uint8Array(zipSync(entries, { level: 1 }))
}

export function decodeProject(bytes: Uint8Array): Song {
  if (bytes.length > MAX_ARCHIVE) throw new Error('Project archive is too large for the browser edition.')
  let expanded = 0
  let count = 0
  const entries = unzipSync(bytes, { filter: entry => {
    expanded += entry.originalSize
    count++
    if (expanded > MAX_AUDIO_BYTES + 4 * MAX_METADATA || count > 256
      || (['song.json', 'lyrics.txt'].includes(entry.name) && entry.originalSize > MAX_METADATA)) {
      throw new Error('Project exceeds browser archive limits (128 MB audio, 2 MB text).')
    }
    return true
  } })
  if (!entries['song.json']) throw new Error('The archive has no song.json file.')
  const metadata = record(JSON.parse(decoder.decode(entries['song.json'])))
  if (![1, 2, 3, 4, 5].includes(Number(metadata.version)) || metadata.sample_rate !== SAMPLE_RATE) throw new Error('Unsupported project version or sample rate.')
  if (!Array.isArray(metadata.tracks) || metadata.tracks.length !== 8) throw new Error('A project must contain eight tracks.')
  const song = newSong()
  song.master = level(metadata.master, 0, 1)
  song.bpm = level(metadata.bpm ?? 100, 40, 240)
  song.metadata = metadata
  song.lyrics = entries['lyrics.txt'] ? decoder.decode(entries['lyrics.txt']) : ''
  song.tracks = metadata.tracks.map((item, index) => {
    const track = record(item)
    if (track.effects !== undefined && (!Array.isArray(track.effects) || track.effects.length > 8)) throw new Error('Invalid effects metadata.')
    if (track.takes !== undefined && (!Array.isArray(track.takes) || track.takes.length > 4)) throw new Error('Invalid takes metadata.')
    const channel = track.channel === undefined ? {} : record(track.channel)
    song.bypassed ||= (Array.isArray(track.effects) && track.effects.some(effect => record(effect).enabled !== false))
      || Object.values(channel).some(value => value !== 0)
    return {
      name: text(track.name, 64), volume: level(track.volume, 0, 1), pan: level(track.pan, -1, 1),
      muted: bool(track.muted), solo: bool(track.solo), metadata: track,
      audio: entries[`track-${index}.wav`] ? decodeWav(entries[`track-${index}.wav`], true) : new Float32Array(0),
    }
  })
  const tape = metadata.tape === undefined ? {} : record(metadata.tape)
  song.bypassed ||= Boolean(tape.loop || tape.punch || (tape.tape_speed !== undefined && tape.tape_speed !== 1))
  for (const [name, data] of Object.entries(entries)) {
    if (name !== 'song.json' && name !== 'lyrics.txt' && !/^track-[0-7]\.wav$/.test(name)) song.preserved[name] = new Uint8Array(data)
  }
  validateTrial(song)
  return song
}

export function exportMix(song: Song): Uint8Array<ArrayBuffer> {
  validateTrial(song)
  const channels = mix(song)
  if (!channels[0].length) throw new Error('There is no audio to export.')
  if (channels.some(channel => channel.some(sample => Math.abs(sample) > 1))) throw new Error('The mix clips. Lower the master or track levels before exporting.')
  return encodeWav(channels, SAMPLE_RATE, true)
}
