# 8T itch.io release checklist

This release targets one paid itch.io page with an embedded browser trial and native desktop downloads.

## Browser build

From `web/`, configure the final HTTPS download URLs and run:

```sh
npm ci
npm test
npm run typecheck
npm run lint
npm run test:e2e -- --output=/tmp/8t-browser-release
VITE_DESKTOP_WINDOWS_URL=https://... \
VITE_DESKTOP_MAC_ARM_URL=https://... \
VITE_DESKTOP_MAC_INTEL_URL=https://... \
VITE_DESKTOP_LINUX_URL=https://... \
npm run release:build
```

Upload the resulting `web/dist/` directory to itch.io as an HTML5 build and enable page embedding. Do not upload a build using the Playwright fixture URL or one that displays `Not yet available` for a platform being sold.

The workflow in `.github/workflows/build-web.yml` can produce the same artifact on a manual run or `v*` tag. Configure these non-secret repository variables before running it:

```text
DESKTOP_WINDOWS_URL
DESKTOP_MAC_ARM_URL
DESKTOP_MAC_INTEL_URL
DESKTOP_LINUX_URL
```

These values are embedded in the public browser bundle. They must be final HTTPS download URLs, never credentials.

## Desktop builds

Build on each target OS and architecture. Cross-compilation is not supported.

```sh
python -m unittest discover -s tests -v
python packaging/build_desktop.py
```

Before uploading native artifacts:

- [ ] Application and dependency redistribution licenses have been reviewed.
- [ ] Required license, notice, and source-offer files are included.
- [ ] Windows installer is Authenticode-signed and verified.
- [ ] macOS app is Developer ID-signed, notarized, and stapled.
- [ ] Linux support baseline and glibc requirements are documented.
- [ ] Each artifact has a matching SHA-256 file, manifest, and passed smoke report.
- [ ] The manifest no longer reports unverified signing or redistribution review.
- [ ] Native downloads were tested on clean target machines.

Upload one file per platform, with the platform and architecture in the filename. Keep the checksum and manifest available with the corresponding release files.

## Functional acceptance

- [ ] Browser trial opens from the itch.io page over HTTPS.
- [ ] Browser recording works with a real microphone in a supported browser.
- [ ] Browser project download and WAV export work.
- [ ] Desktop starts after installation or extraction without development tools.
- [ ] Desktop records and plays back through a real audio interface.
- [ ] Desktop saves and reopens a `.8t` project.
- [ ] Desktop recovery, effects, and WAV export work.
- [ ] One third-party VST3 plugin has been tested, with its user-installed status documented.
- [ ] Buyer-view purchase, download, install, and launch have been tested.

## Page copy

State these limits directly:

- Browser: four mono tracks, five-minute limit, browser-local storage, no native VST3.
- Desktop: eight tracks, ten-minute timeline, native audio devices, effects, songwriting, recovery, and exports.
- Desktop plugins are installed by the user and are not bundled.
- Hardware compatibility depends on the operating system, drivers, and audio interface.

Publish as preview or early access until clean-machine and real-hardware checks pass. Promote to stable only after those checks and the first buyer walkthrough are complete.
