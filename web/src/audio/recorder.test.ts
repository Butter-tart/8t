import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { expect, it } from 'vitest'
import { AudioEngine } from './engine'
import { MAX_FRAMES, newSong } from '../model'

it('rejects trial violations before opening an audio context or microphone', async () => {
  const engine = new AudioEngine()
  const song = newSong()
  await expect(engine.start(song, 0, 4)).rejects.toThrow(/tracks 1-4/)
  await expect(engine.start(song, MAX_FRAMES, 0)).rejects.toThrow(/Rewind/)
  await expect(engine.start(song, -1, 0)).rejects.toThrow(/position/)
  await expect(engine.start(song, 0.5, 0)).rejects.toThrow(/position/)
  await expect(engine.start(song, 0, 0, '', -1)).rejects.toThrow(/offset/)
  song.tracks[4].audio = new Float32Array([0.1])
  await expect(engine.start(song, 0)).rejects.toThrow(/tracks 5-8/)
  expect(engine.context).toBeNull()
  expect(engine.mode).toBe('stopped')
})

it('captures exact scheduled frames, flushes the final partial block and never monitors input', () => {
  const messages: { type: string; samples?: Float32Array; frames?: number }[] = []
  const scope = vm.createContext({
    currentFrame: 0, Float32Array,
    AudioWorkletProcessor: class {
      port = { postMessage: (message: typeof messages[number]) => messages.push(message), onmessage: null }
    },
    registerProcessor: (_name: string, processor: unknown) => { scope.Processor = processor },
  })
  vm.runInContext(readFileSync(new URL('./recorder.worklet.js', import.meta.url), 'utf8'), scope)
  vm.runInContext('recorder = new Processor(); recorder.port.onmessage({data: {type: "start", startFrame: 65, endFrame: 5000}})', scope)
  for (let block = 0; block < 40; block++) {
    scope.currentFrame = block * 128
    vm.runInContext('output = new Float32Array(128); recorder.process([[new Float32Array(128).fill(0.5)]], [[output]])', scope)
    expect(Array.from(scope.output as Float32Array).every(value => value === 0)).toBe(true)
  }
  expect(messages.filter(message => message.type === 'samples').reduce((sum, message) => sum + message.samples!.length, 0)).toBe(4935)
  expect(messages.at(-1)).toEqual({ type: 'done', frames: 4935 })
  vm.runInContext('recorder.port.onmessage({data: {type: "stop"}})', scope)
  expect(messages.filter(message => message.type === 'done')).toHaveLength(1)
})