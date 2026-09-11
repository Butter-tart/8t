from dataclasses import asdict
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import numpy as np
import soundfile as sf

from eighttrack.effects import Effect, TAIL_SECONDS
from eighttrack.model import BounceRecord, MAX_FRAMES, MAX_LYRICS_BYTES, SAMPLE_RATE, Song, Track, export_lyrics, export_mix, export_stems, import_audio, load_song, save_song


class ModelTests(unittest.TestCase):
    def test_songwriting_round_trip_as_utf8_plain_text(self):
        text = "[Verse 1]\nAm       F\nCaf\u00e9 lights\n\n[Chorus]\nSing again\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.porta"
            save_song(Song(lyrics=text), path)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.read("lyrics.txt"), text.encode("utf-8"))
            self.assertEqual(load_song(path).lyrics, text)
            save_song(Song(), path)
            self.assertEqual(load_song(path).lyrics, "")

    def test_song_without_songwriting_entry_loads_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.porta"
            legacy = Path(directory) / "legacy.porta"
            save_song(Song(), original)
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(legacy, "w") as target:
                for name in source.namelist():
                    if name != "lyrics.txt":
                        target.writestr(name, source.read(name))
            self.assertEqual(load_song(legacy).lyrics, "")

    def test_songwriting_size_limit_preserves_saved_project(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.porta"
            save_song(Song(lyrics="Original"), path)
            with self.assertRaisesRegex(ValueError, "Songwriting text"):
                save_song(Song(lyrics="x" * (MAX_LYRICS_BYTES + 1)), path)
            self.assertEqual(load_song(path).lyrics, "Original")
            with zipfile.ZipFile(path) as archive:
                metadata = archive.read("song.json")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("song.json", metadata)
                archive.writestr("lyrics.txt", b"x" * (MAX_LYRICS_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "Songwriting text"):
                load_song(path)

    def test_plain_text_export_preserves_previous_file_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lyrics.txt"
            export_lyrics(Song(lyrics="Original"), path)
            with patch.object(Path, "replace", side_effect=OSError("Disk full")):
                with self.assertRaises(OSError):
                    export_lyrics(Song(lyrics="Replacement"), path)
            self.assertEqual(path.read_text(encoding="utf-8"), "Original")
            self.assertEqual(list(Path(directory).iterdir()), [path])
            text = "Am  F\nCaf\u00e9 lights\n"
            export_lyrics(Song(lyrics=text), path)
            self.assertEqual(path.read_bytes(), text.encode("utf-8"))

    def test_invalid_enabled_loop_cannot_replace_saved_project(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid.porta"
            song = Song()
            save_song(song, path)
            song.loop = True
            with self.assertRaises(ValueError):
                save_song(song, path)
            self.assertFalse(load_song(path).loop)

    def test_fast_varispeed_filters_above_new_nyquist(self):
        song = Song(tape_speed=2)
        track = song.tracks[0]
        track.write(0, np.sin(np.arange(4410) * 2 * np.pi * 18000 / SAMPLE_RATE) * 0.5)
        song.prepare_effects()
        self.assertLess(float(np.max(np.abs(song.mix_tape(500, 1000)))), 0.005)
        track.write(0, np.zeros(4410))
        song.prepare_effects()
        self.assertFalse(song.mix_tape(0, 1000).any())

    def test_overwrite_invalidates_shared_send_cache(self):
        song = Song()
        track = song.tracks[0]
        track.write(0, np.ones(100))
        track.delay_send = 1
        song.prepare_effects()
        self.assertTrue(track._send_audio["delay"].any())
        track.write(0, np.zeros(100))
        song.prepare_effects()
        self.assertFalse(track._send_audio["delay"].any())

    def test_export_range_speed_and_stems(self):
        song = Song(master=1, tape_speed=0.5)
        song.tracks[0] = Track("Voice / lead", np.full(20, 0.1, dtype=np.float32), muted=True)
        song.tracks[1].write(0, np.full(10, 0.2))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mix.wav"
            export_mix(song, path, start=2, end=8, include_tails=False)
            self.assertEqual(sf.info(path).frames, 12)
            paths, clipped = export_stems(song, directory, end=20, include_tails=False)
            self.assertEqual(len(paths), 2)
            self.assertFalse(clipped)
            self.assertTrue(sf.read(paths[0])[0].any())
            self.assertEqual(sf.info(paths[1]).frames, 40)
            with self.assertRaises(ValueError):
                export_stems(song, directory)
        self.assertTrue(song.tracks[0].muted)

    def test_range_export_does_not_leak_audio_after_boundary_into_tail(self):
        song = Song()
        song.tracks[0].write(100, np.ones(100))
        song.tracks[0].effects.append(Effect.builtin("Reverb"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "range.wav"
            export_mix(song, path, end=100)
            samples, _ = sf.read(path)
        self.assertFalse(samples.any())
        self.assertGreater(len(samples), 100)

    def test_channel_eq_tape_and_sends_preserve_dry_take(self):
        song = Song(master=1)
        track = song.tracks[0]
        source = np.sin(np.arange(4410) * 2 * np.pi * 1200 / SAMPLE_RATE).astype(np.float32) * 0.1
        track.write(0, source)
        track.eq_mid, track.tape_drive, track.wow_flutter = 6, 3, 0.2
        track.reverb_send, track.delay_send = 0.2, 0.3
        song.prepare_effects()
        rendered = song.mix(0, song.playback_length)
        self.assertTrue(np.isfinite(rendered).all())
        self.assertTrue(rendered[len(source):].any())
        np.testing.assert_array_equal(track.audio, source)
        previous = rendered.copy()
        track.volume *= 0.5
        np.testing.assert_allclose(song.mix(0, song.playback_length), previous * 0.5, atol=1e-7)
        track.muted = True
        self.assertFalse(song.mix(0, song.playback_length).any())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.porta"
            save_song(song, path)
            restored = load_song(path)
        self.assertEqual(restored.tracks[0].eq_mid, 6)
        self.assertEqual(restored.tracks[0].reverb_send, 0.2)
        self.assertEqual(restored.tracks[0].wow_flutter, 0.2)

    def test_varispeed_block_boundaries_are_continuous(self):
        song = Song(tape_speed=0.83)
        song.tracks[0].write(0, np.sin(np.arange(1000) * 0.1))
        whole = song.mix_tape(0, 200)
        split = np.concatenate([song.mix_tape(0, 73), song.mix_tape(73 * 0.83, 127)])
        np.testing.assert_allclose(whole, split, atol=1e-6)

    def test_tape_settings_and_alternate_takes_round_trip(self):
        song = Song(marker_a=2, marker_b=20, loop=True, punch=True, count_in=4, tape_speed=0.8)
        song.set_marker("Chorus", 12)
        song.tracks[0].write(0, np.ones(20))
        song.tracks[0].keep_take("Original")
        song.tracks[0].write(0, np.zeros(20))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tape.porta"
            save_song(song, path)
            restored = load_song(path)
        self.assertEqual(restored.tape_range(), (2, 20))
        self.assertEqual(restored.markers, {"Chorus": 12})
        self.assertEqual(restored.count_in, 4)
        self.assertEqual(restored.tape_speed, 0.8)
        self.assertTrue(restored.loop and restored.punch)
        self.assertEqual(restored.tracks[0].takes[0].name, "Original")
        np.testing.assert_array_equal(restored.tracks[0].takes[0].audio, 1)
        np.testing.assert_array_equal(restored.tracks[0].audio, 0)

    def test_location_memory_and_range_bounds(self):
        song = Song()
        with self.assertRaises(ValueError):
            song.tape_range()
        for index in range(8):
            song.set_marker(str(index), index)
        song.set_marker("0", 20)
        with self.assertRaises(ValueError):
            song.set_marker("Extra", 0)
        with self.assertRaises(ValueError):
            song.set_marker("0", MAX_FRAMES)

    def test_take_bank_swaps_without_losing_active_audio(self):
        track = Track("Voice", np.ones(8, dtype=np.float32))
        track.keep_take("First")
        track.write(0, np.zeros(8))
        track.select_take(0)
        np.testing.assert_array_equal(track.audio, np.ones(8))
        self.assertEqual(track.active_take_name, "First")
        self.assertEqual(track.takes[0].name, "Current")
        track.select_take(0)
        np.testing.assert_array_equal(track.audio, np.zeros(8))
        self.assertEqual(track.active_take_name, "Current")
        for index in range(3):
            track.keep_take(f"Alternate {index}")
        with self.assertRaises(ValueError):
            track.keep_take("Overflow")

    def test_range_edits_preserve_timeline_and_validate_before_mutating(self):
        track = Track("Tape", np.arange(10, dtype=np.float32))
        track.edit_range(2, 5, "copy", 12, fade_frames=0)
        np.testing.assert_array_equal(track.audio, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 0, 2, 3, 4])
        track.edit_range(2, 5, "silence", fade_frames=0)
        np.testing.assert_array_equal(track.audio[:10], [0, 1, 0, 0, 0, 5, 6, 7, 8, 9])
        previous = track.audio.copy()
        with self.assertRaises(ValueError):
            track.edit_range(0, 5, "move", MAX_FRAMES)
        np.testing.assert_array_equal(track.audio, previous)
        track.edit_range(5, 10, "trim", fade_frames=0)
        np.testing.assert_array_equal(track.audio, [0, 0, 0, 0, 0, 5, 6, 7, 8, 9])

    def test_range_fades_and_overlapping_move(self):
        track = Track("Tape", np.ones(10, dtype=np.float32))
        track.edit_range(2, 6, "fade in")
        np.testing.assert_allclose(track.audio[2:6], [0, 1 / 3, 2 / 3, 1])
        np.testing.assert_array_equal(track.audio[:2], [1, 1])
        track.edit_range(2, 6, "move", 4, fade_frames=0)
        np.testing.assert_allclose(track.audio[2:8], [0, 0, 0, 1 / 3, 2 / 3, 1])

    def test_bounce_combines_sources_without_changing_them(self):
        song = Song(master=0)
        song.tracks[0] = Track("Guitar", np.array([0.2, 0.4], dtype=np.float32), 0.5, -1, True)
        song.tracks[1] = Track("Voice", np.array([0.1], dtype=np.float32), 1, 1, False, True)
        song.tracks[2].write(0, np.ones(5))
        originals = [track.audio.copy() for track in song.tracks[:2]]
        record = song.bounce([0, 1], 2)
        np.testing.assert_allclose(song.tracks[2].audio, [0.2, 0.2])
        for track, original in zip(song.tracks, originals):
            np.testing.assert_array_equal(track.audio, original)
        self.assertEqual(record.sources, [0, 1])
        self.assertEqual(record.source_names, ["Guitar", "Voice"])
        self.assertEqual(record.frames, 2)

    def test_bounce_history_round_trip_keeps_original_names(self):
        song = Song()
        song.tracks[0].write(0, np.ones(3))
        record = song.bounce([0], 1)
        record.undone = True
        song.tracks[0].name = "Renamed source"
        song.tracks[1].name = "Renamed destination"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounce.porta"
            save_song(song, path)
            restored = load_song(path)
        self.assertEqual(restored.bounce_history, [record])
        self.assertEqual(restored.bounce_history[0].source_names, ["Track 1"])
        self.assertEqual(restored.bounce_history[0].destination_name, "Track 2")
        self.assertEqual(restored.tracks[0].name, "Renamed source")

    def test_bounce_renders_effects_and_invalidates_destination_cache(self):
        song = Song()
        source, destination = song.tracks[:2]
        source.write(0, np.full(8, 0.2))
        source.volume = 0.5
        gain = Effect.builtin("Gain")
        gain.parameters["gain_db"] = 6
        gain.load()
        source.effects.append(gain)
        destination.write(0, np.ones(4))
        destination.effects.append(Effect.builtin("Gain"))
        destination.prepare_effects()
        old_cache = destination.playback_audio
        record = song.bounce([0], 1)
        self.assertEqual(record.frames, 8 + SAMPLE_RATE * TAIL_SECONDS)
        np.testing.assert_allclose(destination.audio[:8], 0.1 * 10 ** (6 / 20), rtol=1e-5)
        np.testing.assert_allclose(source.audio, 0.2)
        destination.prepare_effects()
        self.assertIsNot(destination.playback_audio, old_cache)
        np.testing.assert_allclose(destination.playback_audio[:8, 0], destination.audio[:8])

    def test_bounce_limit_does_not_truncate_or_change_destination(self):
        song = Song()
        song.tracks[0].write(0, np.ones(8))
        song.tracks[0].effects.append(Effect.builtin("Gain"))
        with patch("eighttrack.model.MAX_FRAMES", 8):
            with self.assertRaises(ValueError):
                song.bounce([0], 1)
        self.assertEqual(len(song.tracks[1].audio), 0)
        self.assertEqual(song.bounce_history, [])

    def test_failed_bounce_preserves_destination_and_history(self):
        song = Song()
        song.tracks[0].write(0, np.ones(3))
        song.tracks[2].write(0, np.ones(5))
        previous = song.tracks[2].audio.copy()
        for sources, destination in [([], 2), ([0, 0], 2), ([0], 0), ([8], 2), ([1], 2), ([0], -1)]:
            with self.assertRaises(ValueError):
                song.bounce(sources, destination)
        with patch.object(Track, "prepare_effects", side_effect=ValueError("Missing plugin")):
            with self.assertRaises(ValueError):
                song.bounce([0], 2)
        np.testing.assert_array_equal(song.tracks[2].audio, previous)
        self.assertEqual(song.bounce_history, [])

    def test_bounce_history_rejects_invalid_metadata(self):
        song = Song()
        song.tracks[0].write(0, np.ones(3))
        data = asdict(song.bounce([0], 1))
        for key, value in [("sources", [8]), ("sources", [0, 0]), ("destination", 0),
                           ("source_names", []), ("frames", -1), ("timestamp", "yesterday"),
                           ("undone", "yes")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                BounceRecord.from_dict({**data, key: value})

    def test_legacy_project_without_history_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.porta"
            save_song(Song(), path)
            with zipfile.ZipFile(path) as archive:
                metadata = json.loads(archive.read("song.json"))
            metadata.pop("bounce_history")
            for version in (1, 2):
                metadata["version"] = version
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("song.json", json.dumps(metadata))
                self.assertEqual(load_song(path).bounce_history, [])

    def test_eight_independent_tracks(self):
        song = Song()
        self.assertEqual(len(song.tracks), 8)
        song.tracks[0].write(0, np.ones(4))
        self.assertEqual(len(song.tracks[1].audio), 0)

    def test_punch_in_preserves_surrounding_audio(self):
        track = Track("Guitar", np.arange(8, dtype=np.float32))
        track.write(3, np.array([20, 21]))
        np.testing.assert_array_equal(track.audio, [0, 1, 2, 20, 21, 5, 6, 7])

    def test_stereo_write_preserves_channels_and_rejects_mismatches(self):
        track = Track("Keys", np.zeros((0, 2), dtype=np.float32))
        track.write(2, np.array([[0.2, -0.4], [0.6, -0.8]]))
        self.assertEqual(track.channels, 2)
        np.testing.assert_allclose(track.audio, [[0, 0], [0, 0], [0.2, -0.4], [0.6, -0.8]])
        previous = track.audio.copy()
        for samples in (np.ones(2), np.ones((2, 1)), np.ones((2, 3)),
                        np.array([[float("nan"), 0]]), np.array(1)):
            with self.assertRaises(ValueError):
                track.write(0, samples)
            np.testing.assert_array_equal(track.audio, previous)

    def test_stereo_range_edits_follow_frame_axis(self):
        original = np.column_stack((np.arange(8), -np.arange(8))).astype(np.float32)
        for operation in ("silence", "trim", "copy", "move", "fade in", "fade out"):
            for fade in (0, 2):
                with self.subTest(operation=operation, fade=fade):
                    stereo = Track("Keys", original.copy())
                    mono = Track("Left", original[:, 0].copy())
                    stereo.edit_range(2, 6, operation, destination=7, fade_frames=fade)
                    mono.edit_range(2, 6, operation, destination=7, fade_frames=fade)
                    np.testing.assert_array_equal(stereo.audio[:, 0], mono.audio)
                    np.testing.assert_array_equal(stereo.audio[:, 1], -mono.audio)
                    self.assertEqual(stereo.channels, 2)

    def test_recording_after_end_inserts_silence(self):
        track = Track("Vocal")
        track.write(3, np.ones(2))
        np.testing.assert_array_equal(track.audio, [0, 0, 0, 1, 1])

    def test_stereo_project_takes_and_empty_track_round_trip(self):
        song = Song(master=1)
        track = song.tracks[0]
        track.convert_channels(2)
        track.write(0, np.array([[0.2, -0.4], [0.3, -0.6]], dtype=np.float32))
        track.keep_take("Original")
        song.tracks[1].convert_channels(2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stereo.8t"
            save_song(song, path)
            restored = load_song(path)
            np.testing.assert_array_equal(restored.tracks[0].audio, track.audio)
            np.testing.assert_array_equal(restored.tracks[0].takes[0].audio, track.audio)
            self.assertEqual(restored.tracks[1].audio.shape, (0, 2))
            self.assertEqual(restored.tracks[2].audio.shape, (0,))
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(json.loads(archive.read("song.json"))["version"], 5)

    def test_stereo_mix_balance_bounce_and_export(self):
        song = Song(master=1)
        original = np.tile([0.2, -0.4], (32, 1)).astype(np.float32)
        track = song.tracks[0] = Track("Keys", original.copy(), volume=1)
        np.testing.assert_array_equal(song.mix(0, 32), original)
        track.pan = -1
        np.testing.assert_allclose(song.mix(0, 32), np.column_stack((original[:, 0], np.zeros(32))), atol=1e-7)
        track.pan = 1
        np.testing.assert_allclose(song.mix(0, 32), np.column_stack((np.zeros(32), original[:, 1])), atol=1e-7)
        track.pan = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mix.wav"
            export_mix(song, path)
            samples, rate = sf.read(path)
            np.testing.assert_allclose(samples, original, atol=2e-7)
        song.bounce([0], 1)
        np.testing.assert_allclose(song.tracks[1].audio, original.mean(axis=1))
        song.tracks[2].convert_channels(2)
        song.bounce([0], 2)
        np.testing.assert_array_equal(song.tracks[2].audio, original)

    def test_stereo_conversion_includes_takes_and_invalidates_caches(self):
        track = Track("Keys", np.array([0.1, 0.2], dtype=np.float32))
        track.keep_take("Mono")
        track._fx_key = track._send_key = track._tape_key = ("old",)
        track.convert_channels(2)
        np.testing.assert_array_equal(track.audio, track.takes[0].audio)
        self.assertEqual(track.audio.shape, (2, 2))
        self.assertIsNone(track._fx_key)
        self.assertIsNone(track._send_key)
        self.assertIsNone(track._tape_key)
        track.convert_channels(1)
        np.testing.assert_allclose(track.audio, [0.1, 0.2])
        with self.assertRaises(ValueError):
            track.convert_channels(3)
        self.assertEqual(track.channels, 1)

    def test_stereo_processing_stems_match_playback(self):
        song = Song(master=0.8, tape_speed=1.25)
        track = song.tracks[0]
        track.convert_channels(2)
        phase = np.arange(1000) * 0.04
        track.write(0, np.column_stack((np.sin(phase), np.cos(phase))) * 0.1)
        original = track.audio.copy()
        track.eq_low, track.eq_mid, track.eq_high = 2, -2, 1
        track.tape_drive, track.wow_flutter = 1, 0.1
        track.reverb_send = track.delay_send = 0.1
        song.prepare_effects()
        expected = song.mix_tape(100, 640)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mix.wav"
            export_mix(song, path, include_tails=False)
            samples, rate = sf.read(path)
            np.testing.assert_allclose(samples, song.mix_tape(0, 800), atol=2e-7)
            export_mix(song, path, start=100, end=900, include_tails=False)
            samples, rate = sf.read(path)
            paths, clipped = export_stems(song, directory, start=100, end=900, include_tails=False)
            stem, rate = sf.read(paths[0])
            np.testing.assert_array_equal(samples, stem)
            self.assertEqual(samples.shape, expected.shape)
            self.assertTrue(np.isfinite(samples).all())
        np.testing.assert_array_equal(track.audio, original)

    def test_stereo_project_validation_and_legacy_mono_loading(self):
        song = Song()
        song.tracks[0].write(0, np.array([0.1, 0.2], dtype=np.float32))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.8t"
            save_song(song, path)
            with zipfile.ZipFile(path) as archive:
                metadata = json.loads(archive.read("song.json"))
                audio = archive.read("track-0.wav")
            for version in (1, 2, 3, 4):
                metadata["version"] = version
                for entry in metadata["tracks"]:
                    entry.pop("channels", None)
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("song.json", json.dumps(metadata))
                    archive.writestr("track-0.wav", audio)
                np.testing.assert_array_equal(load_song(path).tracks[0].audio, song.tracks[0].audio)
            metadata["version"] = 5
            for channels in (0, 3, True, "2", 2):
                metadata["tracks"][0]["channels"] = channels
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("song.json", json.dumps(metadata))
                    archive.writestr("track-0.wav", audio)
                with self.assertRaises(ValueError):
                    load_song(path)
            save_song(song, path)
            previous = path.read_bytes()
            song.tracks[0].keep_take("Mono")
            song.tracks[0].audio = np.zeros((1, 2), dtype=np.float32)
            with self.assertRaises(ValueError):
                save_song(song, path)
            self.assertEqual(path.read_bytes(), previous)

    def test_stereo_bounce_history_round_trip_retains_format(self):
        song = Song()
        song.tracks[0].write(0, np.ones(4, dtype=np.float32) * 0.1)
        song.tracks[1].convert_channels(2)
        record = song.bounce([0], 1)
        song.tracks[1].convert_channels(1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.8t"
            save_song(song, path)
            self.assertEqual(load_song(path).bounce_history[0].channels, 2)
        data = asdict(record)
        data.pop("channels")
        self.assertEqual(BounceRecord.from_dict(data).channels, 1)
        for invalid in (True, 0, 3, "2"):
            data["channels"] = invalid
            with self.assertRaises(ValueError):
                BounceRecord.from_dict(data)

    def test_stereo_import_preserves_channels_while_resampling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            sf.write(path, np.tile([0.25, -0.5], (4800, 1)), 48000, subtype="FLOAT")
            samples = import_audio(path, channels=2)
            self.assertEqual(samples.shape, (4410, 2))
            np.testing.assert_allclose(samples[100:-100, 0], 0.25, atol=0.001)
            np.testing.assert_allclose(samples[100:-100, 1], -0.5, atol=0.001)

    def test_invalid_recording_does_not_mutate_track(self):
        track = Track("Vocal")
        for start, samples in [(-1, [1]), (MAX_FRAMES, [1]), (0, [float("nan")])]:
            with self.assertRaises(ValueError):
                track.write(start, np.array(samples))
        self.assertEqual(len(track.audio), 0)

    def test_pan_mute_solo_and_record_exclusion(self):
        song = Song(master=1)
        left, right = song.tracks[:2]
        left.audio = right.audio = np.ones(4, dtype=np.float32)
        left.volume = right.volume = 1
        left.pan, right.pan = -1, 1
        np.testing.assert_allclose(song.mix(0, 4), np.ones((4, 2)), atol=1e-7)
        left.solo = True
        np.testing.assert_allclose(song.mix(0, 4)[:, 1], 0, atol=1e-7)
        left.muted = True
        self.assertFalse(song.mix(0, 4).any())
        left.solo = False
        self.assertFalse(song.mix(0, 4, exclude=1).any())

    def test_silence_past_end(self):
        song = Song()
        song.tracks[0].write(0, np.ones(4))
        self.assertFalse(song.mix(4, 20).any())

    def test_project_round_trip_and_export(self):
        song = Song(master=0.6, bpm=123)
        song.tracks[2] = Track("Guitar", np.linspace(-0.5, 0.5, 100, dtype=np.float32), 0.7, -0.2, True, True)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "test.porta"
            save_song(song, project)
            restored = load_song(project)
            self.assertEqual(restored.master, song.master)
            self.assertEqual(restored.bpm, 123)
            self.assertEqual(restored.tracks[2].name, "Guitar")
            self.assertTrue(restored.tracks[2].muted)
            self.assertTrue(restored.tracks[2].solo)
            np.testing.assert_array_equal(restored.tracks[2].audio, song.tracks[2].audio)
            self.assertFalse(export_mix(restored, Path(directory) / "mix.wav"))
            samples, rate = sf.read(Path(directory) / "mix.wav")
            self.assertEqual(samples.shape, (100, 2))
            self.assertEqual(rate, SAMPLE_RATE)

    def test_stereo_import_resamples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            sf.write(path, np.full((4800, 2), 0.25), 48000, subtype="FLOAT")
            audio = import_audio(path)
            self.assertEqual(audio.shape, (4410,))
            np.testing.assert_allclose(audio[100:-100], 0.25, atol=0.001)

    def test_export_reports_clipping(self):
        song = Song(master=1)
        for track in song.tracks:
            track.write(0, np.ones(32))
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(export_mix(song, Path(directory) / "mix.wav"))


if __name__ == "__main__":
    unittest.main()