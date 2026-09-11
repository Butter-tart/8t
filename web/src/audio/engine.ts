import recorderUrl from './recorder.worklet.js?url'
import { audioBytes, MAX_AUDIO_BYTES, MAX_FRAMES, SAMPLE_RATE, songLength, trackGain, validateTrackIndex, validateTrial } from '../model'
import type { Song } from '../model'

export type Mode = 'stopped' | 'playing' | 'recording'
export interface Capture { track: number; start: number; audio: Float32Array<ArrayBuffer> }

export class AudioEngine {
  context: AudioContext | null = null
  mode: Mode = 'stopped'
  position = 0
  peak = 0
  inputPeak = 0
  clipped = false
  onStop: (capture: Capture | null, warning?: string) => void = () => {}
  private sources: AudioBufferSourceNode[] = []
  private channels: { gain: GainNode; pan: StereoPannerNode }[] = []
  private analyser: AnalyserNode | null = null
  private meter = new Float32Array(1024)
  private origin = 0
  private begin = 0
  private end = 0
  private armed = -1
  private stream: MediaStream | null = null
  private input: MediaStreamAudioSourceNode | null = null
  private recorder: AudioWorkletNode | null = null
  private silent: GainNode | null = null
  private chunks: Float32Array<ArrayBuffer>[] = []
  private captured = 0
  private offset = 0
  private timer: ReturnType<typeof setInterval> | null = null
  private stopping = false
  private stopResolve: (() => void) | null = null
  private stopPromise: Promise<void> | null = null

  setStopHandler(handler: (capture: Capture | null, warning?: string) => void): void {
    this.onStop = handler
  }

  resetClip(): void { this.clipped = false }

  async ready(): Promise<AudioContext> {
    if (!window.isSecureContext || !window.AudioContext) throw new Error('Audio requires HTTPS or localhost and a browser with Web Audio support.')
    if (!this.context) {
      this.context = new AudioContext({ latencyHint: 'interactive', sampleRate: SAMPLE_RATE })
      try {
        await this.context.audioWorklet.addModule(recorderUrl)
      } catch (error) {
        await this.context.close()
        this.context = null
        throw error
      }
      this.context.onstatechange = () => {
        if (this.context?.state !== 'running' && this.mode !== 'stopped') {
          void this.stop('Audio was interrupted. Captured samples have been retained.', true)
        }
      }
    }
    await this.context.resume()
    return this.context
  }

  async start(song: Song, position: number, armed = -1, device = '', offsetMs = 0): Promise<void> {
    if (this.mode !== 'stopped') return
    validateTrial(song)
    if (armed !== -1) validateTrackIndex(armed)
    if (!Number.isInteger(position) || position < 0) throw new Error('Invalid playhead position.')
    if (!Number.isFinite(offsetMs) || offsetMs < 0 || offsetMs > 2000) throw new Error('Invalid recording offset.')
    if (position >= MAX_FRAMES) throw new Error('Rewind before recording or playing.')
    if (armed < 0 && position >= songLength(song)) throw new Error('The playhead is past the end of the audio.')
    const context = await this.ready()
    this.armed = armed
    this.offset = Math.round(offsetMs / 1000 * context.sampleRate)
    this.chunks = []
    this.captured = 0
    this.clipped = false
    this.stopping = false
    this.stopPromise = null
    try {
      let available = MAX_FRAMES - position
      if (armed >= 0) {
        const otherBytes = audioBytes(song) - song.tracks[armed].audio.byteLength
        available = Math.min(available, Math.floor((MAX_AUDIO_BYTES - otherBytes) / 4) - position)
        if (available <= 0) throw new Error('There is no recording space left in the 128 MB audio budget.')
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone recording is unavailable in this browser.')
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
          deviceId: device ? { exact: device } : undefined,
          channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false,
        } })
        await context.resume()
        this.input = context.createMediaStreamSource(this.stream)
        this.recorder = new AudioWorkletNode(context, '8t-recorder', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] })
        this.silent = context.createGain()
        this.silent.gain.value = 0
        this.input.connect(this.recorder).connect(this.silent).connect(context.destination)
        this.recorder.port.onmessage = ({ data }) => {
          if (data.type === 'samples') {
            this.chunks.push(data.samples)
            this.captured += data.samples.length
            this.inputPeak = data.samples.reduce((peak: number, sample: number) => Math.max(peak, Math.abs(sample)), 0)
            this.clipped ||= this.inputPeak >= 0.99
          }
          if (data.type === 'done') void this.finish()
        }
        this.stream.getAudioTracks().forEach(track => {
          track.onended = () => { void this.stop('The input disconnected. Captured samples have been retained.') }
        })
      }
      this.origin = position
      this.position = position
      this.begin = Math.ceil((context.currentTime + 0.1) * context.sampleRate) / context.sampleRate
      this.end = armed >= 0 ? position + available : songLength(song)
      this.analyser = context.createAnalyser()
      this.analyser.fftSize = 1024
      this.analyser.connect(context.destination)
      this.channels = song.tracks.map((track, index) => {
        const gain = context.createGain()
        const pan = context.createStereoPanner()
        gain.gain.value = trackGain(song, index, armed)
        pan.pan.value = track.pan
        gain.connect(pan).connect(this.analyser!)
        if (index !== armed && track.audio.length > position) {
          const buffer = context.createBuffer(1, track.audio.length, SAMPLE_RATE)
          buffer.copyToChannel(track.audio, 0)
          const source = context.createBufferSource()
          source.buffer = buffer
          source.connect(gain)
          source.start(this.begin, position / SAMPLE_RATE)
          this.sources.push(source)
        }
        return { gain, pan }
      })
      this.mode = armed < 0 ? 'playing' : 'recording'
      this.recorder?.port.postMessage({ type: 'start',
        startFrame: Math.round(this.begin * context.sampleRate),
        endFrame: Math.round(this.begin * context.sampleRate + available / SAMPLE_RATE * context.sampleRate) + this.offset,
      })
      this.timer = setInterval(() => {
        this.tick()
        if (this.mode === 'playing' && this.position >= this.end) void this.stop()
      }, 30)
    } catch (error) {
      this.cleanup()
      this.mode = 'stopped'
      throw error
    }
  }

  update(song: Song): void {
    this.channels.forEach((channel, index) => {
      channel.gain.gain.setTargetAtTime(trackGain(song, index, this.armed), this.context!.currentTime, 0.01)
      channel.pan.pan.setTargetAtTime(song.tracks[index].pan, this.context!.currentTime, 0.01)
    })
  }

  tick(): number {
    if (this.mode !== 'stopped' && this.context) {
      this.position = Math.min(this.end, this.origin + Math.max(0, this.context.currentTime - this.begin) * SAMPLE_RATE)
      this.analyser?.getFloatTimeDomainData(this.meter)
      this.peak = this.meter.reduce((peak, sample) => Math.max(peak, Math.abs(sample)), 0)
      this.clipped ||= this.peak >= 0.99
    }
    return this.position
  }

  private warning: string | undefined

  stop(warning?: string, interrupted = false): Promise<void> {
    if (this.stopPromise) {
      if (interrupted) void this.finish()
      return this.stopPromise
    }
    if (this.mode === 'stopped') return Promise.resolve()
    this.warning = warning
    this.tick()
    this.sources.forEach(source => { try { source.stop() } catch {} })
    this.sources = []
    this.stopPromise = new Promise(resolve => { this.stopResolve = resolve })
    if (this.recorder && !interrupted) this.recorder.port.postMessage({ type: 'stop' })
    else void this.finish()
    return this.stopPromise
  }

  private async finish(): Promise<void> {
    if (this.stopping) return
    this.stopping = true
    this.tick()
    const rate = this.context!.sampleRate
    const armed = this.armed
    this.cleanup()
    let capture: Capture | null = null
    try {
      if (armed >= 0 && this.captured > this.offset) {
        const samples = new Float32Array(this.captured)
        let cursor = 0
        for (const chunk of this.chunks) { samples.set(chunk, cursor); cursor += chunk.length }
        const audio = (await resample(samples.slice(this.offset), rate)).slice(0, this.end - this.origin)
        capture = { track: armed, start: this.origin, audio }
        this.position = Math.min(MAX_FRAMES, this.origin + audio.length)
      }
    } catch (error) {
      this.warning = `Recording could not be finalized: ${String(error)}`
    } finally {
      this.chunks = []
      this.mode = 'stopped'
      this.peak = 0
      this.inputPeak = 0
      this.onStop(capture, this.warning)
      this.warning = undefined
      this.stopResolve?.()
      this.stopResolve = null
      this.stopPromise = null
    }
  }

  private cleanup(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
    this.sources.forEach(source => { try { source.stop() } catch {} source.disconnect() })
    this.sources = []
    this.channels.forEach(channel => { channel.gain.disconnect(); channel.pan.disconnect() })
    this.channels = []
    this.analyser?.disconnect()
    this.analyser = null
    this.input?.disconnect()
    this.recorder?.disconnect()
    if (this.recorder) this.recorder.port.onmessage = null
    this.silent?.disconnect()
    this.stream?.getTracks().forEach(track => { track.onended = null; track.stop() })
    this.stream = null
    this.input = null
    this.recorder = null
    this.silent = null
  }
}

export async function resample(samples: Float32Array<ArrayBuffer>, sourceRate: number): Promise<Float32Array<ArrayBuffer>> {
  if (sourceRate === SAMPLE_RATE || !samples.length) return samples
  const offline = new OfflineAudioContext(1, Math.max(1, Math.round(samples.length * SAMPLE_RATE / sourceRate)), SAMPLE_RATE)
  const buffer = offline.createBuffer(1, samples.length, sourceRate)
  buffer.copyToChannel(samples, 0)
  const source = offline.createBufferSource()
  source.buffer = buffer
  source.connect(offline.destination)
  source.start()
  return new Float32Array((await offline.startRendering()).getChannelData(0))
}