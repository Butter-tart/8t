import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { Circle, Download, FilePlus2, FolderOpen, Headphones, Mic, MonitorDown, Music2, Play, Redo2, Save, Settings2, SkipBack, SlidersHorizontal, Square, Trash2, Undo2, Upload, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { del, get, set } from 'idb-keyval'
import { AudioEngine } from './audio/engine'
import type { Capture, Mode } from './audio/engine'
import { audioBytes, formatTime, MAX_AUDIO_BYTES, MAX_FRAMES, newSong, overwrite, replaceAudio, SAMPLE_RATE, songLength, TRIAL_TRACKS, validateTrackIndex, validateTrial } from './model'
import type { Song } from './model'
import { download, fileJob } from './storage/client'
import { createDemo } from './demo'
import './App.css'

const RECOVERY_KEY = '8t-web-session-v1'
const desktopDownloads = [
  ['Windows x64', import.meta.env.VITE_DESKTOP_WINDOWS_URL],
  ['macOS Apple Silicon', import.meta.env.VITE_DESKTOP_MAC_ARM_URL],
  ['macOS Intel', import.meta.env.VITE_DESKTOP_MAC_INTEL_URL],
  ['Linux x64', import.meta.env.VITE_DESKTOP_LINUX_URL],
].map(([label, url]) => ({ label, url: typeof url === 'string' && /^https:\/\/[^\s]+$/.test(url) ? url : '' }))

function Tool({ icon: Icon, label, active = false, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; active?: boolean }) {
  return <button type="button" title={label} aria-label={label} className={`tool ${active ? 'active' : ''}`} {...props}><Icon size={18} aria-hidden="true" /></button>
}

function Waveform({ audio, duration, color }: { audio: Float32Array; duration: number; color: string }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const element = canvas.current!
    const draw = () => {
      const width = element.clientWidth
      const height = element.clientHeight
      const ratio = window.devicePixelRatio || 1
      element.width = width * ratio
      element.height = height * ratio
      const context = element.getContext('2d')!
      context.scale(ratio, ratio)
      context.clearRect(0, 0, width, height)
      context.strokeStyle = '#d7dcda'
      context.beginPath()
      context.moveTo(0, height / 2)
      context.lineTo(width, height / 2)
      context.stroke()
      context.fillStyle = color
      const step = Math.max(1, Math.ceil(duration / width))
      for (let pixel = 0; pixel < width; pixel++) {
        const start = Math.floor(pixel / width * duration)
        if (start >= audio.length) break
        let low = 0
        let high = 0
        for (let frame = start; frame < Math.min(start + step, audio.length); frame++) {
          low = Math.min(low, audio[frame])
          high = Math.max(high, audio[frame])
        }
        context.fillRect(pixel, height / 2 - Math.min(1, high) * height * 0.42, 1,
          Math.max(1, (Math.min(1, high) - Math.max(-1, low)) * height * 0.42))
      }
    }
    const observer = new ResizeObserver(draw)
    observer.observe(element)
    return () => observer.disconnect()
  }, [audio, duration, color])
  return <canvas ref={canvas} aria-label={audio.length ? 'Recorded audio waveform' : 'Empty track'} />
}

const colors = ['#16856c', '#2b77ad', '#bb554d', '#92732c', '#527c66', '#9e5679', '#487e87', '#6d6b61']

function App() {
  const [song, setSong] = useState(newSong)
  const songRef = useRef(song)
  const [engine] = useState(() => new AudioEngine())
  const [mode, setMode] = useState<Mode>('stopped')
  const [position, setPosition] = useState(0)
  const [armed, setArmed] = useState<number | null>(null)
  const [name, setName] = useState('Untitled session')
  const [busy, setBusy] = useState('')
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [peak, setPeak] = useState(0)
  const [inputPeak, setInputPeak] = useState(0)
  const [clipped, setClipped] = useState(false)
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [device, setDevice] = useState('')
  const [offset, setOffset] = useState(0)
  const [recovery, setRecovery] = useState<{ song: Song; name: string; date: string } | null>(null)
  const [recoveryStatus, setRecoveryStatus] = useState('Local session')
  const [historyCounts, setHistoryCounts] = useState({ undo: 0, redo: 0 })
  const history = useRef<{ undo: Song[]; redo: Song[] }>({ undo: [], redo: [] })
  const projectInput = useRef<HTMLInputElement>(null)
  const audioInput = useRef<HTMLInputElement>(null)
  const importTarget = useRef(0)
  const settings = useRef<HTMLDialogElement>(null)
  const downloads = useRef<HTMLDialogElement>(null)
  const pending = useRef(false)
  const locked = mode !== 'stopped' || Boolean(busy)
  const duration = Math.min(MAX_FRAMES, Math.max(SAMPLE_RATE * 30, songLength(song) + SAMPLE_RATE * 5, position + SAMPLE_RATE * 5))
  const memory = audioBytes(song)

  function commit(next: Song, undoable = false) {
    validateTrial(next)
    if (undoable) {
      history.current.undo.push(songRef.current)
      let bytes = history.current.undo.reduce((sum, item) => sum + audioBytes(item), 0)
      while (history.current.undo.length > 1 && (history.current.undo.length > 32 || bytes > 32 * 1024 * 1024)) bytes -= audioBytes(history.current.undo.shift()!)
      history.current.redo = []
      setHistoryCounts({ undo: history.current.undo.length, redo: 0 })
    }
    songRef.current = next
    setSong(next)
    setDirty(true)
    engine.update(next)
  }

  function replaceSession(next: Song, filename: string, changed = false) {
    validateTrial(next)
    songRef.current = next
    setSong(next)
    setName(filename)
    setDirty(changed)
    setPosition(0)
    setArmed(null)
    setError('')
    setNotice('')
    history.current = { undo: [], redo: [] }
    setHistoryCounts({ undo: 0, redo: 0 })
  }

  async function task(label: string, action: () => Promise<void>) {
    if (pending.current) return
    pending.current = true
    setBusy(label)
    setError('')
    try { await action() } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)) }
    finally { pending.current = false; setBusy('') }
  }

  const stopped = useEffectEvent((capture: Capture | null, warning?: string) => {
    if (capture) {
      try {
        const current = songRef.current
        commit(replaceAudio(current, capture.track, overwrite(current.tracks[capture.track].audio, capture.audio, capture.start)), true)
        setNotice('Take recorded')
      } catch (failure) { setError(String(failure)) }
    }
    if (warning) setError(warning)
    setMode('stopped')
    setPosition(Math.round(engine.position))
    setPeak(0)
    setInputPeak(0)
  })

  useEffect(() => {
    engine.setStopHandler((capture, warning) => stopped(capture, warning))
    const timer = setInterval(() => {
      if (engine.mode !== 'stopped') {
        setPosition(Math.round(engine.tick()))
        setPeak(engine.peak)
        setInputPeak(engine.inputPeak)
        setClipped(engine.clipped)
      }
    }, 60)
    return () => { clearInterval(timer) }
  }, [engine])

  useEffect(() => {
    let active = true
    get<{ song: Song; name: string; date: string }>(RECOVERY_KEY).then(value => {
      if (active && value?.song?.tracks?.length === 8) setRecovery(value)
    }).catch(() => { if (active) setRecoveryStatus('Recovery unavailable') })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!dirty || locked || recovery) return
    const timer = setTimeout(() => {
      set(RECOVERY_KEY, { song, name, date: new Date().toISOString() })
        .then(() => setRecoveryStatus('Recovery saved locally'))
        .catch(() => setRecoveryStatus('Recovery failed: download a project backup'))
    }, 1500)
    return () => clearTimeout(timer)
  }, [song, name, dirty, locked, recovery])

  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty || mode === 'recording' || busy) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', unload)
    return () => window.removeEventListener('beforeunload', unload)
  }, [dirty, mode, busy])

  async function start(recording: boolean) {
    await task(recording ? 'Opening input...' : 'Starting audio...', async () => {
      await engine.start(songRef.current, Math.round(position), recording ? armed ?? -1 : -1, device, offset)
      setClipped(false)
      setMode(engine.mode)
    })
  }

  function undo(redo = false) {
    const source = redo ? history.current.redo : history.current.undo
    const target = redo ? history.current.undo : history.current.redo
    const previous = source.pop()
    if (!previous) return
    target.push(songRef.current)
    commit({ ...songRef.current, tracks: songRef.current.tracks.map((track, index) => ({ ...track, audio: previous.tracks[index].audio })) })
    setHistoryCounts({ undo: history.current.undo.length, redo: history.current.redo.length })
  }

  function updateTrack(index: number, changes: Partial<Song['tracks'][number]>) {
    validateTrackIndex(index)
    commit({ ...songRef.current, tracks: songRef.current.tracks.map((track, slot) => slot === index ? { ...track, ...changes } : track) })
  }

  function confirmReplace(): boolean { return !dirty || window.confirm('Discard unsaved changes to this session?') }

  async function openFile(file: File) {
    if (locked || pending.current) return
    if (!confirmReplace()) return
    await task('Opening project...', async () => {
      if (file.size > 160 * 1024 * 1024) throw new Error('This project is too large for the browser edition.')
      const next = await fileJob<Song>('open', new Uint8Array(await file.arrayBuffer()))
      replaceSession(next, file.name.replace(/\.(8t|porta)$/i, ''))
      setRecovery(null)
    })
  }

  async function saveProject() {
    await task('Preparing project...', async () => {
      const bytes = await fileJob<Uint8Array<ArrayBuffer>>('save', songRef.current)
      download(bytes, `${name.trim() || 'Untitled session'}.8t`, 'application/zip')
      setDirty(false)
      setNotice('Project download prepared')
    })
  }

  const keyAction = useEffectEvent((event: KeyboardEvent) => {
    if (event.target instanceof HTMLElement && (event.target.matches('input, textarea, select, button') || event.target.isContentEditable)) return
    if (settings.current?.open || downloads.current?.open || busy) return
    if (event.code === 'Space') {
      event.preventDefault()
      if (mode !== 'stopped') void task('Finishing take...', () => engine.stop())
      else void start(false)
    }
    if (event.code === 'Home' && !locked) { event.preventDefault(); setPosition(0) }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && !locked) { event.preventDefault(); void saveProject() }
  })

  useEffect(() => {
    const listener = (event: KeyboardEvent) => keyAction(event)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  async function refreshDevices() {
    await task('Checking inputs...', async () => {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Audio input requires HTTPS or localhost.')
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach(track => track.stop())
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter(item => item.kind === 'audioinput'))
    })
  }

  return <div className="studio" aria-busy={Boolean(busy)}>
    <header className="masthead">
      <div className="identity"><h1>8T<span className="brand-dot">.</span></h1><div><strong>8 TRACK DAW</strong><span>BROWSER TRIAL / 4 TRACKS / 5 MIN</span></div></div>
      <div className="session-title"><input aria-label="Session name" value={name} maxLength={64} disabled={locked} onChange={event => { setName(event.target.value); setDirty(true) }} /><span>{dirty ? 'Unsaved changes' : 'Local session'}<span className={`status-dot ${dirty ? 'unsaved' : ''}`} /></span></div>
      <nav className="file-tools" aria-label="Project">
        <Tool icon={FilePlus2} label="New session" disabled={locked} onClick={() => { if (confirmReplace()) { replaceSession(newSong(), 'Untitled session'); setRecovery(null); void del(RECOVERY_KEY).catch(() => setRecoveryStatus('Could not clear recovery')) } }} />
        <Tool icon={FolderOpen} label="Open project" disabled={locked} onClick={() => projectInput.current?.click()} />
        <Tool icon={Save} label="Download project" disabled={locked} onClick={() => void saveProject()} />
        <Tool icon={Download} label="Export stereo WAV" disabled={locked || !songLength(song)} onClick={() => void task('Rendering mix...', async () => { download(await fileJob<Uint8Array<ArrayBuffer>>('export', songRef.current), `${name || 'Mix'}.wav`, 'audio/wav'); setNotice('Stereo WAV download prepared') })} />
        <span className="divider" /><Tool icon={Settings2} label="Audio settings" disabled={locked} onClick={() => settings.current?.showModal()} />
        <Tool icon={MonitorDown} label="Download desktop" onClick={() => downloads.current?.showModal()} />
      </nav>
    </header>
    {recovery && <section className="recovery-banner"><span>Last session: <strong>{recovery.name}</strong> / {new Date(recovery.date).toLocaleString()}</span><div><button disabled={locked} onClick={() => { if (confirmReplace()) void task('Restoring session...', async () => { replaceSession(recovery.song, recovery.name, true); setRecovery(null) }) }}>Restore session</button><Tool icon={Download} label="Download recovery project" disabled={locked} onClick={() => void task('Preparing recovery...', async () => { download(await fileJob<Uint8Array<ArrayBuffer>>('backup', recovery.song), `${recovery.name || 'Recovery'}.8t`, 'application/zip') })} /><Tool icon={X} label="Dismiss recovery" disabled={locked} onClick={() => setRecovery(null)} /></div></section>}
    {error && <div className="message error" role="alert"><span>{error}</span><Tool icon={X} label="Dismiss error" onClick={() => setError('')} /></div>}
    <section className="transport" aria-label="Transport">
      <div className="transport-buttons">
        <Tool icon={SkipBack} label="Rewind" disabled={locked} onClick={() => setPosition(0)} />
        <Tool icon={Square} label="Stop" disabled={mode === 'stopped' || Boolean(busy)} onClick={() => void task('Finishing take...', () => engine.stop())} />
        <Tool icon={Play} label="Play" active={mode === 'playing'} disabled={locked || !songLength(song)} onClick={() => void start(false)} />
        <Tool icon={Circle} label="Record" active={mode === 'recording'} disabled={locked || armed === null} onClick={() => void start(true)} />
      </div>
      <div className={`time-display ${mode === 'recording' ? 'recording' : ''}`}><span className="transport-state">{mode === 'recording' ? 'REC' : mode === 'playing' ? 'PLAY' : 'STOP'}</span><output aria-label="Playhead time">{formatTime(position / SAMPLE_RATE)}</output></div>
      <div className="transport-meta"><label>BPM <input type="number" min="40" max="240" aria-label="Tempo" value={song.bpm} disabled={locked} onChange={event => commit({ ...song, bpm: Math.min(240, Math.max(40, Number(event.target.value))) })} /></label><span>44.1 kHz / PCM</span></div>
      <div className="master-section"><label htmlFor="master">MASTER <output>{Math.round(song.master * 100)}%</output></label><input id="master" type="range" min="0" max="1" step="0.01" disabled={Boolean(busy)} value={song.master} onChange={event => commit({ ...song, master: Number(event.target.value) })} /><div className="meter-line"><meter aria-label="Output level" min="0" max="1" value={peak} /><button className={`clip ${clipped ? 'lit' : ''}`} onClick={() => { engine.resetClip(); setClipped(false) }} title="Reset clip indicator">CLIP</button></div></div>
    </section>
    <section className="view-bar">
      <h2 className="mixer-title"><SlidersHorizontal size={16} />Mixer</h2>
      <div className="edit-tools"><Tool icon={Undo2} label="Undo audio edit" disabled={locked || !historyCounts.undo} onClick={() => undo()} /><Tool icon={Redo2} label="Redo audio edit" disabled={locked || !historyCounts.redo} onClick={() => undo(true)} /><button className="demo-button" disabled={locked} onClick={() => { if (confirmReplace()) void task('Creating demo...', async () => { replaceSession(await createDemo(), 'Sunday sketches', true); setRecovery(null) }) }}><Music2 size={15} />Load demo</button></div>
    </section>
    <main><div className="mixer" aria-label="Mixer">
      <div className="mixer-heading"><span>CHANNEL / INPUT</span><div className="ruler">{Array.from({ length: 5 }, (_, index) => <span key={index}>{formatTime(duration / SAMPLE_RATE * index / 4).slice(0, 5)}</span>)}</div><span>LEVEL / PAN</span></div>
      <div className="tracks">{song.tracks.slice(0, TRIAL_TRACKS).map((track, index) => <section className={`track-row ${armed === index ? 'armed' : ''}`} key={index} style={{ '--track-color': colors[index] } as React.CSSProperties} aria-label={`Track ${index + 1}`}>
        <div className="track-controls"><div className="track-label"><span className="track-number">{String(index + 1).padStart(2, '0')}</span><input aria-label={`Track ${index + 1} name`} value={track.name} maxLength={64} disabled={locked} onChange={event => updateTrack(index, { name: event.target.value })} /></div><div className="track-switches"><Tool icon={Circle} label={`Arm track ${index + 1}`} active={armed === index} aria-pressed={armed === index} disabled={locked} onClick={() => setArmed(armed === index ? null : index)} /><button aria-label={`Mute track ${index + 1}`} title="Mute" aria-pressed={track.muted} className={track.muted ? 'muted' : ''} disabled={Boolean(busy)} onClick={() => updateTrack(index, { muted: !track.muted })}>M</button><button aria-label={`Solo track ${index + 1}`} title="Solo" aria-pressed={track.solo} className={track.solo ? 'solo' : ''} disabled={Boolean(busy)} onClick={() => updateTrack(index, { solo: !track.solo })}>S</button><Tool icon={Upload} label={`Import WAV to track ${index + 1}`} disabled={locked} onClick={() => { importTarget.current = index; audioInput.current?.click() }} /><Tool icon={Trash2} label={`Erase track ${index + 1}`} disabled={locked || !track.audio.length} onClick={() => { if (window.confirm(`Erase audio from ${track.name}?`)) commit(replaceAudio(song, index, new Float32Array(0)), true) }} /></div></div>
        <button className={`wave-lane ${track.muted ? 'wave-muted' : ''}`} aria-label={`Seek in track ${index + 1}`} disabled={locked} onClick={event => { const bounds = event.currentTarget.getBoundingClientRect(); setPosition(Math.round(Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) * duration)) }}><Waveform audio={track.audio} duration={duration} color={colors[index]} />{!track.audio.length && <span className="empty-lane">{armed === index ? 'INPUT ARMED' : 'EMPTY'}</span>}<span className="playhead" style={{ left: `${position / duration * 100}%` }} />{mode === 'recording' && armed === index && <span className="recording-label">RECORDING</span>}</button>
        <div className="channel-mix"><label><span>LEVEL <output>{Math.round(track.volume * 100)}</output></span><input aria-label={`Track ${index + 1} level`} type="range" min="0" max="1" step="0.01" value={track.volume} disabled={Boolean(busy)} onChange={event => updateTrack(index, { volume: Number(event.target.value) })} /></label><label className="pan"><span>L</span><input aria-label={`Track ${index + 1} pan`} type="range" min="-1" max="1" step="0.01" value={track.pan} disabled={Boolean(busy)} onChange={event => updateTrack(index, { pan: Number(event.target.value) })} /><span>R</span></label></div>
      </section>)}</div>
      <div className="position-strip"><label htmlFor="position">POSITION</label><input id="position" type="range" min="0" max={duration} step="1" value={position} disabled={locked} onChange={event => setPosition(Number(event.target.value))} /><output>{formatTime(songLength(song) / SAMPLE_RATE)} TOTAL</output></div>
    </div></main>
    <footer className="status-bar"><span className="status-message" role="status">{busy || notice || recoveryStatus}</span><span className="input-status"><Mic size={13} />{armed === null ? 'No track armed' : `Track ${armed + 1} armed`}<meter aria-label="Input level" min="0" max="1" value={inputPeak} /></span><span>{(memory / 1024 / 1024).toFixed(1)} / {MAX_AUDIO_BYTES / 1024 / 1024} MB</span></footer>
    <input ref={projectInput} data-testid="project-input" type="file" accept=".8t,.porta" hidden onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void openFile(file) }} />
    <input ref={audioInput} data-testid="audio-input" type="file" accept=".wav,audio/wav" hidden onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file && !locked) void task('Importing WAV...', async () => { if (file.size > MAX_AUDIO_BYTES) throw new Error('WAV exceeds the browser file budget.'); const audio = await fileJob<Float32Array<ArrayBuffer>>('import', new Uint8Array(await file.arrayBuffer())); const current = songRef.current; commit(replaceAudio(current, importTarget.current, overwrite(current.tracks[importTarget.current].audio, audio, Math.round(position))), true); setNotice('WAV imported') }) }} />
    <dialog ref={downloads} className="audio-settings desktop-downloads" aria-labelledby="desktop-heading"><div className="dialog-heading"><h2 id="desktop-heading"><MonitorDown size={20} />8T Desktop</h2><Tool icon={X} label="Close desktop downloads" onClick={() => downloads.current?.close()} /></div><span className="preview-status">Unsigned preview</span><ul>{desktopDownloads.map(item => <li key={item.label}><span>{item.label}</span>{item.url ? <a href={item.url} target="_blank" rel="noopener noreferrer" aria-label={`Download ${item.label}`}><Download size={16} />Download</a> : <span className="unavailable">Not yet available</span>}</li>)}</ul></dialog>
    <dialog ref={settings} className="audio-settings"><div className="dialog-heading"><h2><Headphones size={20} />Audio settings</h2><Tool icon={X} label="Close audio settings" disabled={Boolean(busy)} onClick={() => settings.current?.close()} /></div><label>Recording input<select aria-label="Recording input" value={device} onChange={event => setDevice(event.target.value)}><option value="">System default</option>{devices.map(item => <option key={item.deviceId} value={item.deviceId}>{item.label || 'Audio input'}</option>)}</select></label><button disabled={Boolean(busy)} onClick={() => void refreshDevices()}><Mic size={16} />Find audio inputs</button><label>Recording offset (ms)<input aria-label="Recording offset (ms)" type="number" min="0" max="2000" value={offset} onChange={event => setOffset(Math.max(0, Math.min(2000, Number(event.target.value))))} /></label><dl><div><dt>Output</dt><dd>System default</dd></div><div><dt>Monitoring</dt><dd>Off</dd></div><div><dt>Audio context</dt><dd>{engine.context ? `${engine.context.sampleRate} Hz` : 'Not started'}</dd></div></dl>{error && <p role="alert">{error}</p>}</dialog>
  </div>
}

export default App