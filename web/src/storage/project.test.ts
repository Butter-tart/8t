import { describe, expect, it } from 'vitest'
import { strToU8, unzipSync, zipSync } from 'fflate'
import wavefile from 'wavefile'
import { newSong } from '../model'
import { decodeProject, decodeWav, encodeProject, encodeWav, exportMix } from './project'

describe('project exchange', () => {
  it('round trips float audio and lyrics with four empty desktop slots', () => {
    const song = newSong()
    song.lyrics = 'Verse 1\nAm  C  G'
    song.tracks[0].audio = new Float32Array([0, 0.25, -0.5, 1.25])
    const loaded = decodeProject(encodeProject(song))
    expect(loaded.lyrics).toBe(song.lyrics)
    expect(loaded.tracks[0].audio).toEqual(song.tracks[0].audio)
    expect(loaded.preserved).toEqual(song.preserved)
    expect(loaded.bypassed).toBe(false)
    expect(loaded.tracks).toHaveLength(8)
    expect(loaded.tracks.slice(4).every(track => !track.audio.length)).toBe(true)
  })
  it('refuses desktop-only content instead of stripping it', () => {
    const entries = unzipSync(encodeProject(newSong()))
    const original = JSON.parse(new TextDecoder().decode(entries['song.json']))
    for (const extra of [{ effects: [{ kind: 'vst3', enabled: true }] }, { takes: ['Original'] }, { channels: 2 }]) {
      const metadata = structuredClone(original)
      metadata.version = 5
      Object.assign(metadata.tracks[0], extra)
      expect(() => decodeProject(zipSync({ ...entries, 'song.json': strToU8(JSON.stringify(metadata)) }))).toThrow(/desktop/)
    }
    expect(() => decodeProject(zipSync({ ...entries, 'track-4.wav': encodeWav([new Float32Array([0.1])]) }))).toThrow(/tracks 5-8/)
    expect(() => decodeProject(zipSync({ ...entries, 'effects/0-0.bin': new Uint8Array([1]) }))).toThrow(/desktop/)
    const song = newSong()
    song.tracks[4].audio = new Float32Array([0.1])
    expect(() => encodeProject(song)).toThrow(/tracks 5-8/)
    expect(() => exportMix(song)).toThrow(/tracks 5-8/)
    const backup = unzipSync(encodeProject(song, true))
    expect(decodeWav(backup['track-4.wav'], true)[0]).toBeCloseTo(0.1)
  })
  it('reads older versions with default lyrics', () => {
    const song = newSong()
    for (const version of [1, 2, 3, 4, 5]) {
      const bytes = zipSync({ 'song.json': strToU8(JSON.stringify({ version, sample_rate: 44100, master: 0.8,
        tracks: song.tracks.map(({ audio: _audio, metadata: _metadata, ...track }) => track) })) })
      expect(decodeProject(bytes).lyrics).toBe('')
    }
  })
  it('rejects malformed and oversized metadata without touching an existing song', () => {
    expect(() => decodeProject(zipSync({ 'song.json': strToU8('{}') }))).toThrow()
    expect(() => decodeProject(zipSync({ 'song.json': new Uint8Array(2_000_001) }))).toThrow(/limits/)
  })
  it('downmixes stereo WAV and resamples 48 kHz files', () => {
    const bytes = encodeWav([new Float32Array(480).fill(0.5), new Float32Array(480).fill(-0.25)], 48000)
    const samples = decodeWav(bytes)
    expect(samples.length).toBe(441)
    expect(samples[220]).toBeCloseTo(0.125, 3)
    expect(() => decodeWav(bytes, true)).toThrow(/44.1/)
  })
  it('rejects overlong WAV before resampling', () => {
    const wav = new wavefile.WaveFile()
    wav.fromScratch(1, 8000, '8', [128])
    ;(wav.data as { samples: Uint8Array }).samples = new Uint8Array(8000 * 300 + 1)
    expect(() => decodeWav(wav.toBuffer())).toThrow(/five minutes/)
  })
  it('blocks empty or clipped exports', () => {
    const song = newSong()
    expect(() => exportMix(song)).toThrow(/no audio/)
    song.tracks[0].audio = new Float32Array([3])
    expect(() => exportMix(song)).toThrow(/clips/)
  })
})