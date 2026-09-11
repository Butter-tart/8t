import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import tempfile
import json
import zipfile

import numpy as np
import soundfile as sf

from eighttrack.effects import BUILTINS, Effect, TAIL_SECONDS, render_effects
from eighttrack.engine import AudioEngine
from eighttrack.model import SAMPLE_RATE, Song, export_mix, load_song, save_song


class EffectTests(unittest.TestCase):
    def test_all_builtins_render_finite_audio(self):
        audio = np.sin(np.arange(512, dtype=np.float32) * 0.1) * 0.1
        for name in BUILTINS:
            with self.subTest(effect=name):
                result = render_effects(audio, [Effect.builtin(name)], 44100)
                self.assertEqual(result.shape, (512 + 44100 * TAIL_SECONDS, 2))
                self.assertTrue(np.isfinite(result).all())

    def test_gain_renders_stereo_without_changing_original(self):
        audio = np.full(128, 0.1, dtype=np.float32)
        effect = Effect.builtin("Gain")
        effect.parameters["gain_db"] = 6.0
        effect.load()
        rendered = render_effects(audio, [effect], 44100)
        self.assertEqual(rendered.shape, (128 + 44100 * TAIL_SECONDS, 2))
        np.testing.assert_allclose(rendered[:128], 0.1 * 10 ** (6 / 20), rtol=1e-5)
        np.testing.assert_array_equal(audio, np.full(128, 0.1, dtype=np.float32))

    def test_bypassed_missing_plugin_does_not_run(self):
        effect = Effect("vst3", "Missing", enabled=False)
        np.testing.assert_array_equal(render_effects(np.ones(8), [effect], 44100), np.ones((8, 2)))
        effect.enabled = True
        with self.assertRaisesRegex(ValueError, "not loaded"):
            render_effects(np.ones(8), [effect], 44100)

    def test_stereo_inserts_preserve_channels_and_source(self):
        audio = np.tile([0.1, -0.3], (128, 1)).astype(np.float32)
        original = audio.copy()
        bypassed = render_effects(audio, [], SAMPLE_RATE)
        np.testing.assert_array_equal(bypassed, original)
        self.assertFalse(np.shares_memory(bypassed, audio))
        effect = Effect.builtin("Gain")
        effect.parameters["gain_db"] = 6
        effect.load()
        rendered = render_effects(audio, [effect], SAMPLE_RATE)
        self.assertEqual(rendered.shape, (128 + SAMPLE_RATE * TAIL_SECONDS, 2))
        np.testing.assert_allclose(rendered[:128], original * 10 ** (6 / 20), rtol=1e-5)
        np.testing.assert_array_equal(audio, original)
        np.testing.assert_array_equal(render_effects(np.zeros((0, 2)), [effect], SAMPLE_RATE), np.zeros((0, 2)))

    def test_restoring_metadata_does_not_execute_external_plugin(self):
        original = Effect("vst3", "Saved effect", path="/tmp/example.vst3", state=b"preset")
        with patch("eighttrack.effects.pb.load_plugin") as loader:
            restored = Effect.from_dict(original.to_dict(), original.state)
        loader.assert_not_called()
        self.assertEqual(restored.state, b"preset")
        self.assertIsNone(restored.processor)

    @patch("eighttrack.effects.pb.load_plugin")
    def test_native_plugin_state_restore_and_instrument_rejection(self, loader):
        plugin = Mock(is_effect=True, is_instrument=False)
        plugin.name = "Example"
        plugin.raw_state = b"initial"
        loader.return_value = plugin
        effect = Effect("vst3", "Example", path="/tmp/example.vst3", state=b"saved")
        effect.load()
        loader.assert_called_once_with("/tmp/example.vst3", plugin_name=None)
        self.assertEqual(plugin.raw_state, b"saved")
        plugin.raw_state = b"edited"
        effect.capture_state()
        self.assertEqual(effect.state, b"edited")
        plugin.is_instrument = True
        with self.assertRaisesRegex(ValueError, "MIDI instruments"):
            effect.load()
        self.assertIsNone(effect.processor)

    def test_delay_tail_and_repeatable_render(self):
        audio = np.zeros(100, dtype=np.float32)
        audio[0] = 0.5
        effect = Effect.builtin("Delay")
        effect.parameters.update(delay_seconds=0.1, feedback=0, mix=1)
        effect.load()
        first = render_effects(audio, [effect], 44100)
        second = render_effects(audio, [effect], 44100)
        self.assertGreater(np.max(np.abs(first[100:])), 0.1)
        np.testing.assert_allclose(first, second)

    def test_playback_and_export_use_same_cached_effects(self):
        song = Song(master=1)
        track = song.tracks[0]
        track.write(0, np.ones(128, dtype=np.float32) * 0.1)
        track.effects.append(Effect.builtin("Gain"))
        track.effects[0].parameters["gain_db"] = 6
        track.effects[0].load()
        song.prepare_effects()
        cached = track._fx_audio
        playback = AudioEngine(song)._render(128)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mix.wav"
            export_mix(song, path)
            samples, rate = sf.read(path)
        self.assertIs(track._fx_audio, cached)
        np.testing.assert_allclose(samples[:128], playback, atol=2e-7)
        self.assertEqual(len(samples), song.playback_length)
        self.assertEqual(rate, SAMPLE_RATE)

    def test_cache_invalidated_by_take_parameter_and_bypass_changes(self):
        song = Song()
        track = song.tracks[0]
        track.write(0, np.ones(128, dtype=np.float32) * 0.1)
        effect = Effect.builtin("Gain")
        track.effects.append(effect)
        song.prepare_effects()
        first = track._fx_audio
        track.write(0, np.ones(128, dtype=np.float32) * 0.2)
        song.prepare_effects()
        self.assertIsNot(first, track._fx_audio)
        np.testing.assert_allclose(track._fx_audio[:128], 0.2)
        effect.parameters["gain_db"] = 6
        effect.load()
        song.prepare_effects()
        np.testing.assert_allclose(track._fx_audio[:128], 0.2 * 10 ** (6 / 20), rtol=1e-5)
        effect.enabled = False
        song.prepare_effects()
        self.assertIsNone(track._fx_audio)
        self.assertEqual(song.playback_length, song.length)

    def test_effect_rack_round_trip_preserves_missing_plugin(self):
        song = Song()
        gain = Effect.builtin("Gain")
        gain.parameters["gain_db"] = -3
        gain.load()
        song.tracks[0].effects = [gain, Effect("vst3", "Missing", False, "/tmp/missing.vst3", state=b"preset")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.porta"
            save_song(song, path)
            with patch("eighttrack.effects.pb.load_plugin") as loader:
                restored = load_song(path)
            loader.assert_not_called()
            effects = restored.tracks[0].effects
            self.assertEqual(effects[0].parameters["gain_db"], -3)
            self.assertEqual(effects[1].state, b"preset")
            self.assertFalse(effects[1].enabled)
            save_song(restored, path)
            self.assertEqual(load_song(path).tracks[0].effects[1].state, b"preset")

    def test_version_one_songs_still_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.porta"
            metadata = {"version": 1, "sample_rate": SAMPLE_RATE, "master": 0.8,
                        "tracks": [{"name": f"Track {index + 1}", "volume": 0.8, "pan": 0,
                                    "muted": False, "solo": False} for index in range(8)]}
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("song.json", json.dumps(metadata))
            restored = load_song(path)
            self.assertTrue(all(not track.effects for track in restored.tracks))

    def test_unloaded_effect_prevents_start_before_device_opens(self):
        song = Song()
        song.tracks[0].write(0, np.ones(8))
        song.tracks[0].effects.append(Effect("vst3", "Missing"))
        engine = AudioEngine(song)
        with patch("eighttrack.engine.sd.OutputStream") as stream:
            with self.assertRaisesRegex(ValueError, "not loaded"):
                engine.start()
        stream.assert_not_called()
        self.assertFalse(engine.running)

    def test_muted_tracks_are_prepared_for_live_unmute(self):
        song = Song()
        track = song.tracks[0]
        track.write(0, np.ones(8) * 0.1)
        track.effects.append(Effect.builtin("Gain"))
        track.muted = True
        song.prepare_effects()
        track.muted = False
        self.assertTrue(song.mix(0, 8).any())


if __name__ == "__main__":
    unittest.main()