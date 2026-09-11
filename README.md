# 8T: 8 Track DAW

A Python desktop prototype for making songs one track at a time, like an
eight-track tape recorder. Audio, song storage, and the interface are separate
so you can learn or change one part without rewriting the others.

## Browser Trial and Desktop

The [browser trial](web/README.md) offers four mono tracks, a five-minute
timeline, recording/import, basic mixing, project downloads, and stereo WAV
export. It has no expiry, account requirement, or payment system. Projects open
in desktop with four additional empty tracks available.

Desktop keeps its eight tracks, existing ten-minute timeline, effects/VST3,
alternate takes, tape editing, songwriting, recovery and advanced exports.
The trial refuses incompatible desktop projects rather than removing their
tracks or processing. Older browser recovery can still be downloaded for desktop.

[Native preview packaging](packaging/README.md) produces Windows installers,
separate Intel/Apple Silicon macOS disk images, and Linux archives for hosting
on your website. Initial builds are unsigned previews; platform testing, signing
and dependency-license review remain release requirements.

## Run it

The local environment and dependencies have already been installed in this workspace:

```bash
.venv/bin/python -m eighttrack
```

The installed command is `.venv/bin/8t` (or `8t` with the virtual environment
activated). The distribution is named `8t-daw`; its Python package is `eighttrack`.

Or press **F5** in VS Code and choose **8T: 8 Track DAW**.
For editor completion and the test explorer, run **Python: Select Interpreter**
from the command palette and choose `.venv/bin/python` if VS Code still shows
system Python. No web server, account, or network connection is needed at runtime.

### Setting up another Linux machine

Requires Python 3.11+ and a graphical desktop:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m eighttrack
```

The Python packages are declared in `pyproject.toml`. `sounddevice` also needs
the OS PortAudio library. On Debian/Ubuntu its package is `libportaudio2`;
on Fedora it is `portaudio`. If Qt reports a missing XCB cursor library,
install `libxcb-cursor0` on Debian/Ubuntu or `xcb-util-cursor` on Fedora.
Those OS packages require your usual system package manager and administrator access.

## Record your first song

1. Connect headphones and a microphone or audio interface. Start with low speaker/headphone levels.
2. Open **Audio > Audio devices...**. Choose your input and stereo output, select the input channel (numbered from 1), and set input gain. The system default often works without changes.
3. Edit the **Name** field above track 1's waveform and check its **REC ARM** box. Names are saved with the song; the recording status shows the track number and name. Only one track can be armed at a time.
4. Optionally turn on **Click** and choose a tempo. The click plays during recording/playback but is never added to the exported mix.
5. Press **Record**, play your part, then press the square **Stop** button. The waveform appears when the take is committed at Stop.
6. Return to the start with the rewind button. Arm track 2 and record another part while track 1 plays. Repeat for up to eight tracks.
7. Adjust track faders, pan, **M** (mute), **S** (solo), and the master volume. Multiple solo tracks can play together; mute takes precedence over solo.
8. Choose **File > Save song** to save a `.8t` project. Choose **File > Export stereo WAV...** for a 24-bit stereo WAV you can share.

Older `.porta` projects still open and save in place. **Save song as...** defaults
to `.8t`; saving upgrades the project to format v5. Recording-offset settings and recovery
snapshots from the previous app name are also recognized.

The input meter runs during recording; the output meter runs during playback
and recording. **CLIP** stays red after an overload until the next transport start.
Reduce input gain for input clipping, or track/master levels for output clipping.
Digital input gain cannot repair a microphone or interface that is already clipping.

### Audio interfaces

Open **Audio > Audio devices...** while stopped. Select your interface as both the
recording and playback device, choose an input (or stereo pair for an armed stereo track), and choose the stereo output
pair connected to your headphones or monitors (1/2, 3/4, etc.). The app records
one track at a time, capturing one mono input or two consecutive stereo inputs.
Playback, overdubs, the count-in, and loopback calibration
all use the selected output pair; other outputs remain silent.

Device names include the audio backend (such as ALSA or JACK) and input/output
channel counts. System default shows the device it currently resolves to.
Only available channels or complete input pairs are offered; hovering over a device entry shows its
original name, numeric ID, and default sample rate. Hardware port names are
not provided by PortAudio, so channel numbers follow your interface's routing.

The selected input/output combination is checked at the project's fixed
44.1 kHz rate before applying. Prefer the same interface and backend for both
directions. Device and channel choices last for the current app session.
Use the interface's direct monitoring to hear your live input; software input
monitoring is not available. Recalibrate the recording offset after changing
devices or channels.

On Linux, the interface must be visible to the OS audio system/PortAudio.
If it is missing, connect it before launching the app, check its profile in
system sound settings (a multichannel/Pro Audio profile may expose more ports),
and restart the app. Generic `default` or `pulse` entries represent system
routing, not a specific hardware interface. Physical interface compatibility
still needs to be verified on your hardware.

### Mono and stereo tracks

Each of the eight slots can hold mono or stereo audio. Tracks start in mono.
Stop, open the rightmost tool button below a track, choose **Channel > Format**,
select **Stereo**, and click **Convert format**. Conversion includes all alternate
takes and is undoable. Mono-to-stereo duplicates the signal; stereo-to-mono
averages left and right, with confirmation before converting existing material.

Imports use the destination track's format: stereo tracks preserve left/right,
and mono tracks downmix stereo files. Both resample to 44.1 kHz. Erasing a track
keeps its format. Stereo waveforms show left above right and the duration has an
**ST** suffix. **BAL** replaces **PAN** on stereo tracks: center leaves both
channels at unity, and moving to either side attenuates the opposite channel.
Mono tracks retain the existing equal-power pan law; converting duplicated mono
audio to stereo can therefore increase its centered playback level.

To record stereo, convert the destination, arm it, then open **Audio > Audio
devices...** and choose a complete input pair. The selected input is left and the
next input is right. Both use the same gain and recording offset. Count-in,
punch, effects, takes, mixdown, and stems preserve stereo. Calibration measures
the pair's first input; device/channel changes still require recalibration.
Starting a stereo recording on an interface without the required channels fails
without replacing track audio. Physical stereo capture still needs verification
on your interface; automated tests use simulated device streams.

This is the first implementation increment. Simultaneously armed tracks, the
expanded timeline, tuner/pre-record metering, loop takes/comping, automation,
linked song sections, and in-progress recording recovery are not implemented yet.

### Songwriting

Choose **Songwriting** in the header to write lyrics, chord charts, song sections,
or session notes. **Mixer** returns to the tracks; transport controls stay visible
in both views. The plain-text editor uses a monospaced font for chord alignment
and supports selection, copy/paste, and its own undo/redo. While typing, Space,
R, Home, and brackets edit text instead of triggering transport shortcuts.
Ctrl+Z and Ctrl+Shift+Z undo/redo text while the editor has focus.

**File > Save song** (Ctrl+S) saves the writing inside the `.8t` project and
also writes a UTF-8 plain-text copy beside it: `My song.8t` gets
`My song.lyrics.txt`. The text file opens in ordinary text editors on any platform.
Save As writes a copy beside the new project, leaving the old files unchanged.
Each save replaces the text copy, including when you clear the writing. External
edits to that copy are not imported automatically; paste them into Songwriting
before saving to keep them. The embedded text is used when reopening a project,
so the `.8t` file remains self-contained.

Writing marks the song as unsaved and is included in the existing stopped-song
recovery snapshots. You can write during playback or recording; stop to save.
Background operations temporarily lock the editor with the other song controls.
Older songs open with an empty writing page. Text is limited to 2 MB of UTF-8
per song; formatting such as bold text or embedded images is not stored.

### Tape-style edits

- Stop and click a waveform or move the tape slider to choose a position.
- Recording overwrites the armed track from that position until Stop. Audio before and after the take is preserved. The old audio on the armed track is not played during the take.
- The folder icon below a track imports audio at the playhead using the same overwrite behavior. Mono/stereo WAV, FLAC, AIFF, and OGG are supported through libsndfile. Files are converted to the track's channel format and resampled to 44.1 kHz.
- The trash icon erases a whole track after confirmation.
- **Edit > Undo last audio edit** and **Redo audio edit** cover recordings, imports, erases, bounces, range edits, channel-format conversions, and take-bank changes. History retains up to 32 edits with a 128 MB snapshot budget; the newest edit is retained even if larger. Mixer and effect changes are not in audio history.
- The tape display leaves empty space after the song for new recording. Playback stops at the last stored sample; recording can extend the song.

### Bounce tracks and history

1. Stop playback and choose **Tracks > Bounce tracks...**.
2. Check one or more source tracks and choose a separate destination by track number and name. Empty tracks cannot be sources.
3. Click **Bounce**. If the destination contains audio, confirm before replacing it. The complete source timeline starts at zero, regardless of the playhead, and replaces the entire destination. Sources are kept unchanged.
4. Open **Tracks > Bounce history...** to see the newest bounces first, including local time, source/destination names and numbers, duration, format, and status. Long entries wrap and the table scrolls.

The bounce sums source audio after track volume and effects in the destination's
channel format. Mono destinations average stereo sources/effects; stereo
destinations preserve left/right and duplicate mono sources to both channels.
Pan, mute/solo, and master level are ignored: the checked sources
determine what is included. The destination keeps its own name, volume, pan,
mute/solo, and effects, which apply on playback. Mute the original sources when
auditioning only the bounced track. Samples above full scale are retained without
normalization; a status warning advises lowering destination volume before playback.

Effects tails are included, but bounces exceeding the ten-minute track limit are
rejected without changing destination audio or adding history. Effected bounces
and exports render on a worker thread with song controls locked. Unavailable
source plugins must be loaded or bypassed before bouncing. Channel EQ and tape
coloration are included in a bounce; shared sends and tape speed are not.

History is saved in the `.8t` project, with the names and channel format as they
were at bounce time even after later renames/conversions. Undo restores the destination's previous audio and marks
that bounce **Undone**; history is an activity log, not a store of recoverable takes.
Undo/redo history is not saved across sessions. Up to 1,000 bounce entries
are supported per song. Older projects open with an empty history; new saves use
project format v5, which older app versions cannot open. This version opens formats v1-v4.

### Count-in, loop and punch

- Set **A** and **B** in seconds, or use each down-arrow button to store the current playhead. **Tape > Locate A/B** recalls those positions.
- **Loop** repeats A-B during playback, including when B lies beyond existing audio. Recording does not loop or automatically replace repeated takes.
- **Punch** records only A-B. Start before A for a lead-in with the armed track audible; that track is excluded while recording the punch region. Starting after A locates A automatically. Capture stops after B, including the configured input-delay allowance. Audio outside A-B is preserved.
- **Tape > Tape settings and locations...** sets a 0-16 beat count-in for recording. It plays even with Click off, holds the playhead, and never enters the take or mixdown. Stop during count-in leaves the track unchanged.
- The same panel stores up to eight named locations, plus shared reverb/delay settings and tape speed. These settings and A/B positions are saved in the song.

### Recording alignment

**Audio > Audio devices... > Recording offset** compensates for late input by
discarding the initial round-trip delay before writing audio at the intended tape
position. Positive offsets range from 0 to 2,000 ms and persist on this machine.
Recalibrate after changing interfaces, channels, drivers, or buffering.

**Audio > Calibrate recording offset...** measures the delay using a quiet test
burst. Connect an interface line output to the selected line input with an
appropriate cable, disable direct monitoring, turn off phantom power, and lower
speaker levels before approving the prompt. Silence, unreliable matches, and
clipped input are rejected. This requires a real cable loopback, not a microphone
listening to speakers. Hardware calibration has not been verified on your device.

Automatic punch collects the delayed ending of the phrase before stopping.
For ordinary recording, leave a little time after the phrase before pressing Stop;
audio still in the hardware input buffer at Stop cannot be recovered.

### Tape tools and alternate takes

The rightmost icon below each channel opens **Tape edit / Takes / Channel**.

- **Tape edit** selects start/end positions for silence, trim, copy, move, fade-in, or fade-out. Copy/move overwrite at the destination. Trim retains the selected passage at its original timeline position, silences its lead-in, and removes the ending. None of these operations shifts other tracks. Edge fades default to 5 ms and can be disabled.
- **Takes** keeps up to four named alternatives in addition to the active performance. Keep a take before replacing it, then swap an alternative with the active take to compare performances without losing either. Take names follow their audio. Removing an alternate is undoable. The bank is saved losslessly in the project.
- **Channel** provides low/mid/high EQ at 120 Hz, 1.2 kHz, and 8 kHz, each +/-12 dB, plus reverb/delay sends. The shared effect settings live in Tape settings. Sends follow track volume, pan, mute/solo, and master level; they are not included in track bounces.
- Optional tape saturation and wow/flutter follow the insert rack and leave the original recording unchanged. Both default to zero. The saturation is an approximation, not a physical tape-machine model.

Tape speed ranges from 50% to 200% for playback and mixdown, changing pitch and
duration together. Faster playback uses antialias filtering. Recording requires
100% speed; this is not pitch-preserving time stretching. Channel processing and
speed are edited while stopped. The compact needle meters display peak dBFS,
not calibrated analog VU levels.

### Mixdown and recovery

**File > Export stereo WAV...** offers the whole song or A-B, optional effect tails,
and individual processed tracks. Range exports exclude audio after B before
rendering tails. Stems are numbered stereo WAVs aligned to the selected start,
with volume, pan, master, insert/channel effects, sends, and tape speed applied.
Stem export ignores mute/solo and refuses to overwrite colliding stem filenames.
Tail lengths may differ between stems. Stereo mixdown honors mute/solo.

Dirty, stopped songs receive an atomic recovery snapshot every 30 seconds when
no modal panel or background operation is active. On restart, the latest snapshot
from an interrupted session is offered for recovery; **File > Recover autosave...**
opens others. Recovery opens an unsaved song and preserves the snapshot until you
save or deliberately discard the recovered song. External plugins still require
explicit trust before loading.

Recovery files live in Qt's application-data directory under `recovery` (normally
`~/.local/share/8T/recovery` on Linux). Clean save/discard/close removes
that session's snapshots. Recovery does not protect an in-progress recording or
edits since the last snapshot, and it is not a replacement for backups.

### Keyboard shortcuts

| Key | Action |
| --- | --- |
| Space | Play / stop |
| Home | Stop and return to start |
| R | Record armed track |
| Ctrl+Z | Undo last audio edit |
| Ctrl+Shift+Z | Redo audio edit |
| [ / ] | Locate A / B |
| Ctrl+S | Save |
| Ctrl+Shift+S | Save as |
| Ctrl+O | Open |
| Ctrl+N | New song |
| Ctrl+E | Export stereo mix |

## Plugins and effects

Each track's **Effects** button opens its rack in the bottom Effects dock.
Use the dock's track selector to switch tracks. You can chain up to eight effects
per track, processed from left to right. Stop the transport to edit effects;
the dock also locks during background rendering and recovery saves.

### Arrange the effects dock

- **Docks > Effects** shows or hides the panel. Its close button hides it without
     removing any effects; clicking a track's **Effects** button brings it back.
- Drag the divider above the dock to resize it. Drag its title bar to dock it
     at the top or bottom, or use **Docks > Float effects** for a separate window.
- **Docks > Lock dock positions** prevents moving or floating the dock. It can
     still be resized, hidden, and shown.
- **Docks > Reset dock layout** unlocks the dock and returns it to the bottom.

Dock position, size, visibility, and the position lock are remembered when you
close the app. These are workspace preferences, not changes to the song. In
compact windows, the mixer and effect parameters scroll independently.

### Try an included effect

1. Record or import a take, then click that track's **Effects** button.
2. Choose Gain, Compressor, Reverb, Delay, Distortion, Lowpass, or Highpass from the menu and click the add icon beside it.
3. Select the effect and adjust its numeric parameters. Uncheck **Enabled** to bypass it. Arrow buttons change the order; the trash button removes it.
4. Play to audition; the dock can stay open. Changes are applied immediately to the song settings; save the song to keep them. Effect edits are not part of audio undo.

### Load a VST3 effect on Linux

1. Install a **native Linux VST3 audio effect** that matches your CPU architecture. Common installation locations are `~/.vst3`, `/usr/lib/vst3`, and `/usr/local/lib/vst3`.
2. In the track's rack, open **Load VST3**. Choose **Plugin file...** for a file or **Plugin bundle folder...** for a directory ending in `.vst3`. Select the bundle itself, not the `.so` nested inside it.
3. Confirm the trust prompt only for plugins you trust. For bundles containing multiple plugins, choose an effect from the list.
4. Adjust the generic controls (VST3 parameters use normalized values from 0 to 1), or click **Open plugin editor** for the plugin's own interface when supported. Its editor blocks the main app until closed and may require X11/XWayland on Linux.
5. Save the song. Plugin paths, order, bypass settings, built-in parameters, and VST3 state are stored in the project. Plugin binaries and external resources are not bundled.

When reopening a project, external plugins are deliberately **not loaded automatically**.
Open the rack and click **Load saved plugin** to approve each trusted plugin and its
saved state. Use **Locate** if the same plugin has moved. Missing plugins retain their
settings; bypass or remove an unavailable plugin to continue. Muting is not a substitute
for bypassing an unavailable plugin, since tracks are prepared for live unmuting.
Never relocate saved state onto an unrelated plugin; add that plugin as a new effect instead.

Native plugins execute code inside the application. A broken or malicious plugin can
crash the app or access your files; this is not a sandbox. Save before loading plugins,
and only restore state from trusted songs. No third-party plugin is downloaded automatically.

### How processing works

Effects are **non-destructive, pre-rendered inserts**, not live input effects.
The original mono take stays untouched. Before playback, overdubbing, or export,
each changed track is rendered through its chain into a cached stereo buffer.
Volume, pan, mute, solo, and master controls then operate on that buffer and remain
live. The armed recording track is excluded during a take; automatic punch
prepares it as well so its old audio can play during the lead-in.
The new take gets its effects on the next playback. Waveforms show the original take.

Playback and export read the same cache, so seeking does not reset reverb/delay
history or reload a plugin on the audio thread. A three-second tail is appended to
every effected track, even for effects such as gain that have no audible tail.
Longer reverb/delay tails are truncated; adjust `TAIL_SECONDS` in `effects.py` if needed.
The recording limit remains ten minutes; effect tails can extend playback/export beyond it.

Rendering before playback, effected bounces, exports, and recovery saves runs on
a worker thread. Song controls are locked while the worker owns audio/plugin
state; the window keeps processing events. Closing is blocked until the operation
finishes. A hung native plugin cannot be safely interrupted in-process.
Effects cannot be adjusted during
playback, and there is no plugin automation, tempo-sync transport, sidechain routing,
MIDI instrument support, or processed microphone monitoring. VST2, LV2, LADSPA,
CLAP, Audio Units, and Windows `.dll`/Windows VST3 files are not supported by this rack.
Use native Linux VST3 builds here. Individual plugin compatibility varies.

The host is [Spotify Pedalboard](https://spotify.github.io/pedalboard/).
Its dependency is GPLv3-licensed; review its license and your plugins' licenses
before distributing a packaged application.

## Where to edit

| File | Responsibility | Good first changes |
| --- | --- | --- |
| `eighttrack/model.py` | `Track`, `Song`, stereo mixing, audio import, project save/load, export | Default levels, panning, a simple stateless effect |
| `eighttrack/engine.py` | Playback/recording transport and PortAudio callbacks | Metronome sound, buffer size, transport behavior |
| `eighttrack/ui.py` | `Waveform`, `ChannelStrip`, `StudioWindow`, colors and controls | Colors, labels, fader controls, menu commands |
| `eighttrack/effects.py` | Pedalboard host, built-in registry, plugin state, offline rendering | Add a built-in effect to `BUILTINS`, adjust tail length |
| `eighttrack/effects_ui.py` | Effects rack, trusted VST3 loading, parameter editors | Rack controls and parameter presentation |
| `eighttrack/tape_ui.py` | Tape settings, locations, range edits, takes, channel processing and mixdown panels | Secondary controls |
| `tests/` | Automated model, transport, and interface tests | A regression test for each new behavior |

### How the pieces connect

```text
Desktop controls --> Song / Track settings
                         |
Microphone --> AudioEngine recording buffer --> Track.audio (at Stop)
                         |
Track.audio --> prepare_effects() --> cached stereo audio
                                          |
Song.mix() <-------------------------------+
     |
     +------> AudioEngine --> Speakers/headphones
     |
     +------> export_mix() --> stereo WAV

save_song() / load_song() <--> one .8t file
```

`Track.audio` is a NumPy array of mono floating-point samples, normally between
-1 and +1. A frame is one moment in time: at 44,100 Hz, 44,100 mono frames equal
one second. `Song.mix(start, frames)` combines the eight tracks into a stereo
array with a left and right column. The same mixing function drives playback
and export, so mixer changes affect both.
Call `song.prepare_effects()` before directly calling `song.mix()` on an effected
song. Transport start and export already do this. Change takes through `Track.write()`
or replace the audio array; do not mutate samples in place, which would bypass cache
invalidation. Rack edits invalidate the cache automatically.

Start with `STYLE` and `COLORS` in `ui.py` for a visual change, or the default
`volume` in the `Track` dataclass for an audio setting. Restart the app to see changes.

For a new per-track feature, add its data to `Track`, implement its sound in
`Song.mix()`, connect a control in `ChannelStrip`, and update both `save_song()`
and `load_song()` so it survives reopening. Add a model test before wiring the UI.
For effects, prefer adding a Pedalboard constructor and numeric parameter ranges
to `BUILTINS` in `effects.py`. The rack, persistence, and rendering then pick it up
automatically. Offline renders reset the chain once and process the complete track,
including silence for tails; no plugin processing happens in audio callbacks.

Audio callbacks run on a PortAudio thread. Never open dialogs, access Qt widgets,
write files, or perform slow work from a callback. The UI polls transport state
with a Qt timer. Recording uses a preallocated buffer and commits it on the main
thread when stopped.

The `.8t` file is a ZIP containing `song.json` and lossless floating-point WAV
tracks, plus separate binary VST3 state entries. It stores track names, original
audio, volume, pan, mute/solo, master level, BPM, and effect chains. Rendered caches
are temporary and are not saved. Alternate takes, active-take names, channel EQ,
send levels, tape coloration, speed, locations and recording modes are saved too.
New saves use project format version 4; this app also opens versions 1-3, but
older app versions cannot open new saves.
Device choices, input gain, click enablement, record arming, and playhead position
are session settings, not saved song data. Saving uses a temporary file and atomic
replacement to protect the previous save if writing fails. Keep backups of valuable songs.

## Test your changes

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests use simulated audio streams and offscreen Qt, so they do not open a
microphone or make sound. They cover punch-in overwrite, overdubbing, mixer
controls, format conversion, saving/loading, exporting, transport failures,
undo, effects-rack controls, cache invalidation, effect tails, playback/export
matching, plugin trust handling, and basic desktop layouts. Built-in effects run
through the real Pedalboard host; external-plugin tests use a simulated VST3.
An actual third-party VST3 and its native editor still need testing on your setup.

For a real-device check: record a short take on track 1, overdub track 2 using
headphones, rewind and listen, undo the second take, save/reopen, then export
and listen to the WAV in another player. Check timing alignment as well as sound.

## Prototype limits

- Eight mono tracks, one recording input at a time, fixed 44.1 kHz, maximum ten minutes per song. Output must support two channels.
- Audio is held in RAM, not streamed from disk. Eight full ten-minute tracks use about 847 MB of raw audio, plus recording, undo, and file-operation buffers. Effects add stereo caches (roughly 213 MB per ten-minute effected track), plugin memory, and temporary render buffers. Start with short songs.
- Latency compensation uses a fixed measured/manual offset, not continuous device-clock tracking. Test timing on your hardware before important overdubs.
- No software microphone monitoring. Use headphones and your interface's direct-monitor feature to hear your live input without feedback or software delay.
- No MIDI, clip dragging, comping lanes, pitch-preserving time stretching, or automation. Eight tracks remain the working surface.
- Import, manual project open/save, and dry bounce sums can still pause the UI for large songs. Effects, mixdown, effected bounces and recovery saves use the worker. File operations and effect edits require a stopped transport.
- Audio, alternate takes, send returns, speed filters and undo snapshots consume RAM. Each full-length alternate adds roughly 106 MB; enabled sends and fast-speed filtering add further caches. Keep projects short on low-memory machines.
- Recovery snapshots cover committed, stopped songs only. Save frequently and keep independent backups.
- Output is hard-clipped at full scale for playback/export; there is no mastering limiter or automatic normalization. Export warns if the mix clipped.
- Python audio callbacks are suitable for prototyping, but not guaranteed hard real-time. If you hear dropouts, try increasing `BLOCK_SIZE` in `engine.py` from 512 to 1024, at the cost of more latency.

Before relying on the recorder for a session, check punch boundaries, loopback
alignment, playback responsiveness and mixdown on your actual audio interface.