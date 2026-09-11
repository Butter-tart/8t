import { newSong, SAMPLE_RATE } from './model'
import type { Song } from './model'

export async function createDemo(): Promise<Song> {
  const song = newSong()
  song.bpm = 100
  song.lyrics = 'SUNDAY SKETCHES\n\nAm          F\n\nC           G\n\nVerse\n\n\nChorus\n'
  const parts = [
    { name: 'Soft keys', frequencies: [220, 174.61, 261.63, 196], type: 'sine' as OscillatorType, duration: 1.8, volume: 0.32, pan: -0.3 },
    { name: 'Bass line', frequencies: [110, 87.31, 130.81, 98], type: 'triangle' as OscillatorType, duration: 0.45, volume: 0.26, pan: 0 },
    { name: 'High notes', frequencies: [659.25, 523.25, 783.99, 587.33], type: 'sine' as OscillatorType, duration: 0.25, volume: 0.17, pan: 0.35 },
  ]
  for (const [index, part] of parts.entries()) {
    const context = new OfflineAudioContext(1, SAMPLE_RATE * 10, SAMPLE_RATE)
    for (let beat = 0; beat < 16; beat++) {
      if (index === 0 && beat % 4) continue
      const time = beat * 0.6
      const oscillator = context.createOscillator()
      oscillator.type = part.type
      oscillator.frequency.value = part.frequencies[Math.floor(beat / 4)]
      const gain = context.createGain()
      gain.gain.setValueAtTime(0, time)
      gain.gain.linearRampToValueAtTime(part.volume, time + 0.015)
      gain.gain.exponentialRampToValueAtTime(0.0001, time + part.duration)
      oscillator.connect(gain).connect(context.destination)
      oscillator.start(time)
      oscillator.stop(time + part.duration + 0.02)
    }
    song.tracks[index].audio = new Float32Array((await context.startRendering()).getChannelData(0))
    song.tracks[index].name = part.name
    song.tracks[index].pan = part.pan
  }
  return song
}