import { describe, expect, it } from 'vitest'
import { MAX_FRAMES, SAMPLE_RATE, mix, newSong, overwrite, replaceAudio, trackGain, validateTrial } from './model'

describe('desktop-compatible audio rules', () => {
  it('overwrites only the recorded range and preserves the ending', () => {
    expect([...overwrite(new Float32Array([1, 2, 3, 4]), new Float32Array([9]), 1)]).toEqual([1, 9, 3, 4])
    expect([...overwrite(new Float32Array([1]), new Float32Array([9]), 3)]).toEqual([1, 0, 0, 9])
  })
  it('rejects invalid positions and tracks beyond five minutes', () => {
    expect(MAX_FRAMES).toBe(SAMPLE_RATE * 300)
    expect(() => overwrite(new Float32Array(), new Float32Array(1), MAX_FRAMES)).toThrow()
    expect(() => overwrite(new Float32Array(), new Float32Array(1), -1)).toThrow()
  })
  it('accepts the exact boundary and rejects editing a fifth track', () => {
    const song = newSong()
    expect(song.tracks).toHaveLength(8)
    const audio = overwrite(new Float32Array(), new Float32Array([0.25]), MAX_FRAMES - 1)
    expect(audio.length).toBe(MAX_FRAMES)
    expect(() => validateTrial(replaceAudio(song, 3, audio))).not.toThrow()
    for (const index of [-1, 4, 8, 0.5]) expect(() => replaceAudio(song, index, audio)).toThrow(/tracks 1-4/)
    expect(song.tracks[3].audio.length).toBe(0)
  })
  it('rejects incompatible desktop projects without changing them', () => {
    const song = newSong()
    song.tracks[4].audio = new Float32Array([0.25])
    expect(() => validateTrial(song)).toThrow(/tracks 5-8/)
    expect(song.tracks[4].audio[0]).toBe(0.25)
    song.tracks[4].audio = new Float32Array()
    song.tracks[0].metadata = { effects: [{ kind: 'reverb' }] }
    expect(() => validateTrial(song)).toThrow(/desktop/)
  })
  it('uses equal-power pan and excludes muted, non-solo and armed tracks', () => {
    const song = newSong()
    song.master = 1
    song.tracks[0].volume = 1
    song.tracks[0].audio = new Float32Array([1])
    expect(mix(song)[0][0]).toBeCloseTo(Math.SQRT1_2)
    song.tracks[0].pan = 1
    expect(mix(song)[1][0]).toBe(1)
    expect(trackGain(song, 0, 0)).toBe(0)
    song.tracks[1].solo = true
    expect(mix(song)[1][0]).toBe(0)
    song.tracks[0].solo = true
    song.tracks[0].muted = true
    expect(mix(song)[1][0]).toBe(0)
  })
})