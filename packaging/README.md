# Desktop Preview Builds

Desktop retains eight tracks, the current ten-minute timeline, native audio I/O,
built-in effects and native VST3 hosting, alternate takes, tape tools, songwriting,
recovery, and mix/stem/range exports. Packaging adds no license check and does not
change the desktop project format or limits.

## Native Builds

Build on the target OS and CPU architecture. Cross-compiling Windows or macOS
apps from Linux is not supported. The CI baseline uses clean Python 3.12; the
local Linux bundle has also been built with Python 3.14. Python and application
dependencies are bundled, so end users do not install Python.

```sh
python -m pip install . -r packaging/requirements.txt
python -m unittest discover -s tests -v
python packaging/build_desktop.py
```

The script runs PyInstaller, smoke-tests the executable and writes versioned
artifacts under `dist/releases/`:

| Target | Download |
| --- | --- |
| Windows x64 | Per-user Inno Setup `.exe` installer |
| macOS Apple Silicon | ARM64 `.dmg` containing `8T.app` |
| macOS Intel | x86_64 `.dmg` containing `8T.app` |
| Linux x64 | `.tar.gz` containing the `8t/` application folder |

Each artifact has a SHA-256 file, build manifest and smoke-test report. Keep the
entire application folder intact; `_internal` contains required libraries.
Linux: extract and run `./8t/8t`. macOS: move the app from the DMG to Applications.
Windows: run the installer, which uses the current user's Programs folder.

Windows builders need Inno Setup 6 (`ISCC` on PATH or its standard install path).
macOS uses `hdiutil`; the bundle includes microphone usage text. Separate CPU
builds avoid falsely labeling thin binaries as universal. An ad-hoc macOS
signature used by PyInstaller is not Developer ID signing or notarization.

Linux builders need PortAudio and Qt platform libraries. On Ubuntu 22.04, the
workflow installs `libportaudio2`, `libxcb-cursor0`, `libegl1`, `libopengl0`, and
`libxkbcommon-x11-0`. Install missing prerequisites using your system package
manager. The spec includes resolved PortAudio; sounddevice hooks collect wheel
libraries on Windows/macOS.

Linux still depends on the host's glibc, graphics/audio stack and drivers. Builds
from a newer distribution are not certified for older ones. Use the Ubuntu CI
build and clean-machine tests to establish compatibility. Manifests record the
actual build OS/libc. Minimum Windows/macOS versions must also be verified
against resolved Qt/audio dependencies; runner success is not a support matrix.

## Automation

`.github/workflows/build-desktop.yml` is a manual GitHub Actions workflow with
Linux x64, Windows x64, macOS Intel and ARM64 jobs. It runs tests, builds native
artifacts and uploads them as workflow artifacts. Artifact access follows the
repository's visibility. It does not create releases or publish to your website.
The workspace must be hosted in a suitable repository to run it; local builds
are independent of GitHub.

App dependencies follow `pyproject.toml`; manifests record exact resolved
versions. Build tools are pinned in `requirements.txt`. Archive approved build
environments and dependency wheels if exact repeatability is required.

## Verification

The frozen executable accepts a non-interactive check:

```sh
dist/desktop/8t/8t --smoke-test build/smoke.json --project /path/to/browser.8t
```

Windows uses `dist/desktop/8t/8t.exe`; macOS uses
`dist/desktop/8T.app/Contents/MacOS/8t`. A report is written only after window
creation, project save/reopen, float-audio comparison, stereo WAV export and
built-in effect rendering pass. Settings are isolated from desktop preferences.
The check enumerates devices but does not record from hardware.

Before distribution, test the installed/extracted artifact on each clean target
OS: startup, microphone permissions, actual recording/playback, browser project
import, native save/reopen, recovery, effects and export. Test third-party native
VST3 plugins separately; their binaries are never bundled and compatibility is
not guaranteed across operating systems or architectures.

## Publication Gates

These are unsigned previews. Windows may show SmartScreen warnings and macOS may
block unidentified developers. Do not disable OS protections globally. Obtain
signing credentials, sign artifacts, notarize/staple macOS releases and verify
them on clean machines before describing them as signed downloads.

Dependency metadata and its packaged license files are included, but that alone
does not establish redistribution compliance. Pedalboard is GPLv3 and Qt has its
own obligations. Before hosting artifacts, select a compatible application
license, review bundled libraries, include required notices/licenses and
corresponding source/build materials, and meet applicable source/relinking
obligations. This build does not invent an application license. Manifests mark
redistribution review as required.

After review, upload artifacts/checksums to your host and configure the browser's
`VITE_DESKTOP_*_URL` values. Host credentials are deployment inputs, never embedded
in the app. Signing and publication are deliberately not automated yet.