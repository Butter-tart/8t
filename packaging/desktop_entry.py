import argparse
import json
import os
from pathlib import Path
import tempfile


def smoke_test(report_path: Path, project: Path | None) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    from PySide6.QtCore import QSettings, QStandardPaths
    from PySide6.QtWidgets import QApplication
    from eighttrack.effects import Effect, render_effects
    from eighttrack.model import SAMPLE_RATE, Song, export_mix, load_song, save_song
    from eighttrack.ui import StudioWindow

    with tempfile.TemporaryDirectory(prefix="8t-smoke-") as temporary:
        folder = Path(temporary)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(folder))
        QStandardPaths.setTestModeEnabled(True)
        app = QApplication([])
        app.setApplicationName("8T Build Smoke")
        app.setStyle("Fusion")
        song = load_song(project) if project else Song()
        if project is None:
            song.tracks[0].audio = np.sin(np.arange(4410, dtype=np.float32) * 0.1) * 0.1
        if len(song.tracks) != 8:
            raise RuntimeError("Desktop must provide eight tracks.")
        save_song(song, folder / "smoke.8t")
        restored = load_song(folder / "smoke.8t")
        for original, loaded in zip(song.tracks, restored.tracks, strict=True):
            np.testing.assert_array_equal(original.audio, loaded.audio)
        if restored.lyrics != song.lyrics:
            raise RuntimeError("Songwriting text did not survive project exchange.")
        export_mix(restored, folder / "smoke.wav")
        info = sf.info(folder / "smoke.wav")
        if info.samplerate != SAMPLE_RATE or info.channels != 2:
            raise RuntimeError("Invalid stereo export.")
        rendered = render_effects(song.tracks[0].audio, [Effect.builtin("Gain")], SAMPLE_RATE)
        if not np.isfinite(rendered).all():
            raise RuntimeError("Invalid effect output.")
        window = StudioWindow()
        window.show()
        app.processEvents()
        if not window.isVisible():
            raise RuntimeError("The desktop window failed to open.")
        window.close()
        report = {"status": "passed", "tracks": len(song.tracks), "sample_rate": info.samplerate,
                  "audio_frames": [len(track.audio) for track in song.tracks],
                  "lyrics": song.lyrics, "devices_detected": len(sd.query_devices()),
                  "checks": ["window", "project-roundtrip", "stereo-export", "builtin-effect"],
                  "hardware_recording_tested": False}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="8t")
    parser.add_argument("--smoke-test", type=Path, metavar="REPORT_JSON")
    parser.add_argument("--project", type=Path)
    options = parser.parse_args()
    if options.smoke_test:
        smoke_test(options.smoke_test, options.project)
    else:
        from eighttrack.ui import main
        main()