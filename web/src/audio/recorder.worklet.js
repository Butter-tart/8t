class Recorder extends AudioWorkletProcessor {
  constructor() {
    super()
    this.active = false
    this.buffer = new Float32Array(4096)
    this.used = 0
    this.total = 0
    this.port.onmessage = ({ data }) => {
      if (data.type === 'start') {
        this.startFrame = data.startFrame
        this.endFrame = data.endFrame
        this.active = true
      }
      if (data.type === 'stop') this.finish()
    }
  }

  flush() {
    if (!this.used) return
    const samples = this.buffer.slice(0, this.used)
    this.port.postMessage({ type: 'samples', samples }, [samples.buffer])
    this.used = 0
  }

  finish() {
    if (!this.active) return
    this.active = false
    this.flush()
    this.port.postMessage({ type: 'done', frames: this.total })
  }

  process(inputs, outputs) {
    if (!this.active) return true
    const input = inputs[0]?.[0]
    const size = outputs[0]?.[0]?.length ?? 128
    for (let offset = 0; offset < size; offset++) {
      const frame = currentFrame + offset
      if (frame < this.startFrame) continue
      if (frame >= this.endFrame) {
        this.finish()
        break
      }
      this.buffer[this.used++] = input?.[offset] ?? 0
      this.total++
      if (this.used === this.buffer.length) this.flush()
    }
    return true
  }
}

registerProcessor('8t-recorder', Recorder)