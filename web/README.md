# 8T Browser Trial

A visitor-local recorder built with React, TypeScript, Web Audio and AudioWorklet.
Audio is not uploaded. The Python desktop application remains separate.

## Development

Requires Node.js 22.12+ or a newer supported Node release. From this directory:

```bash
npm ci
npm run dev -- --host 127.0.0.1
```

This workspace has a user-local Node runtime. If `node` is not on your PATH:

```bash
export PATH="$HOME/.local/share/8t-tools/node-v22.22.0-linux-x64/bin:$PATH"
```

Open the URL printed by Vite. Microphone access requires HTTPS or localhost;
an ordinary HTTP LAN address is not sufficient. No Python backend is required.

## Current Scope

- Four mono recording tracks, up to five minutes per track, with a 128 MB audio budget.
- One armed input at a time; overdubs overwrite the selected range and retain audio outside it.
- Playback, seeking, level, equal-power pan, mute/solo, master output and clipping meters.
- WAV import (stereo downmixed to mono), 44.1 kHz project audio, 24-bit stereo WAV export.
- Audio undo/redo, generated demo session, project downloads and stopped-session local recovery.
- `.8t` and `.porta` project reading within trial limits; downloads retain eight project slots for desktop compatibility.

Arm a track and select Record to request microphone access. Audio settings offer
input selection and a manual recording offset. Output uses the system default.
There is no software input monitoring: use headphones and your interface's direct
monitoring when available. Browser and OS device routing can differ from desktop.
The displayed BPM is project metadata; a browser metronome is not implemented yet.

Projects requiring unsupported track layouts or trial features are rejected without
replacing the current session. The trial does not render native VST3 plugins or
provide desktop tape/effects tools. Songwriting is not exposed in the trial UI.
See the current project validation rules before expanding interoperability.

Recovery is stored in IndexedDB after stopped edits. Browser storage can be evicted
or cleared, and an in-progress recording is not crash-protected. Download `.8t`
backups regularly. Concurrent tabs share the same recovery slot.

Older recovery sessions exceeding trial limits can still be downloaded with
**Download recovery project** for use in desktop. The trial preserves imported
lyrics in saved projects even though it does not expose a lyric editor. Four full
five-minute float tracks exceed the combined 128 MB budget. There is no expiry
timer, payment system, or account requirement; these feature limits are not DRM.

## Checks

```bash
npm test
npm run typecheck
npm run lint
npx playwright install chromium firefox
npm run test:e2e
```

Playwright builds and tests the production bundle. Set `PLAYWRIGHT_PORT` to use a
different test port; `--output=/tmp/8t-checks` selects separate test artifacts.
Tests cover audio-clock capture boundaries, mixing rules, archive validation,
sample conversion, project round trips, overdub capture, recovery, downloads,
responsive screenshots and waveform pixels.

Automated capture uses simulated microphone audio. Physical interfaces, measured
overdub latency/drift, long-session memory stress, input unplugging, and mobile/Safari
recording still require manual verification. Desktop and mobile layouts are tested
in Chromium and Firefox; this is not a claim of mobile hardware compatibility.

## Static Hosting

```bash
npm run build
npm run preview -- --host 127.0.0.1
```

Publish `dist/` to HTTPS static hosting. Relative asset paths support subdirectories.
Serve worker JavaScript with a JavaScript MIME type. Recording currently loads its
small worklet as a bundled data URL, so any custom Content Security Policy must
permit that or the build must be configured to emit a separate worklet asset.
No account, cloud storage, public deployment, or native plugin hosting is included.

Desktop download links are build-time configuration via `VITE_DESKTOP_WINDOWS_URL`,
`VITE_DESKTOP_MAC_ARM_URL`, `VITE_DESKTOP_MAC_INTEL_URL`, and `VITE_DESKTOP_LINUX_URL`.
Leave unavailable platforms unset; use actual hosted release URLs when publishing.
The example URL used by browser tests is not a downloadable desktop release.

Rebuild with real configuration after E2E tests before publishing: test builds
intentionally inject a fictional Windows download URL. Missing or non-HTTPS URLs
display **Not yet available**. `VITE_*` values are public; never put credentials in
them. `.env.example` lists the supported values. Downloads currently display
**Unsigned preview**, which must not change until signing is verified.

See [desktop packaging](../packaging/README.md) for native builds, checksums and
the licensing/signing gates before uploading artifacts to your own host.
