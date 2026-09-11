import unittest
from unittest.mock import patch

import numpy as np
import sounddevice as sd

from eighttrack.engine import AudioEngine, estimate_recording_offset
from eighttrack.model import MAX_FRAMES, SAMPLE_RATE, Song


class FakeStream:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.active = False


class EngineTests(unittest.TestCase):
    def test_loopback_offset_estimation_rejects_silence(self):
        reference = np.zeros(4096, dtype=np.float32)
        reference[100:300] = np.random.default_rng(1).uniform(-0.02, 0.02, 200)
        recorded = np.pad(reference, (317, 0))[:len(reference)] * -0.5
        self.assertEqual(estimate_recording_offset(reference, recorded), 317)
        with self.assertRaises(ValueError):
            estimate_recording_offset(reference, np.zeros_like(reference))

    def setUp(self):
        self.song = Song(master=1)
        self.engine = AudioEngine(self.song)

    @patch("eighttrack.engine.sd.OutputStream", FakeStream)
    def test_interface_playback_routes_only_selected_output_pair(self):
        self.song.tracks[0].write(0, np.full(3, 0.2))
        self.engine.output_device = 7
        self.engine.output_channel = 2
        self.engine.start()
        self.assertEqual(self.engine.stream.options["device"], 7)
        self.assertEqual(self.engine.stream.options["channels"], 4)
        output = np.ones((5, 4), dtype=np.float32)
        with self.assertRaises(sd.CallbackStop):
            self.engine._play_callback(output, 5, None, None)
        np.testing.assert_array_equal(output[:, :2], 0)
        np.testing.assert_allclose(output[:, 2:], self.song.mix(0, 5))
        self.engine.stop()

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_interface_recording_routes_input_monitor_mix_and_count_in(self):
        self.song.tracks[1].write(0, np.full(10, 0.2))
        self.song.count_in = 1
        self.song.bpm = 240
        self.engine.input_device = self.engine.output_device = 7
        self.engine.input_channel = 3
        self.engine.output_channel = 4
        self.engine.start(0)
        self.assertEqual(self.engine.stream.options["device"], (7, 7))
        self.assertEqual(self.engine.stream.options["channels"], (4, 6))
        count = round(SAMPLE_RATE / 4)
        incoming = np.zeros((count + 3, 4), dtype=np.float32)
        incoming[:, 3] = 0.4
        output = np.ones((count + 3, 6), dtype=np.float32)
        self.engine._record_callback(incoming, output, count + 3, None, None)
        np.testing.assert_array_equal(output[:, :4], 0)
        self.assertTrue(output[:count, 4:].any())
        np.testing.assert_allclose(output[count:, 4:], self.song.mix(0, 3, exclude=0))
        self.engine.stop()
        np.testing.assert_allclose(self.song.tracks[0].audio, [0.4] * 3)

    @patch("eighttrack.engine.estimate_recording_offset", return_value=123)
    @patch("eighttrack.engine.sd.playrec")
    def test_calibration_uses_selected_interface_channels(self, playrec, estimate):
        playrec.return_value = np.zeros((SAMPLE_RATE * 3, 1))
        self.engine.input_device = self.engine.output_device = 7
        self.engine.input_channel = 3
        self.engine.output_channel = 4
        self.assertEqual(self.engine.calibrate_latency(), 123)
        self.assertEqual(playrec.call_args.kwargs["device"], (7, 7))
        self.assertEqual(playrec.call_args.kwargs["input_mapping"], [4])
        self.assertEqual(playrec.call_args.kwargs["output_mapping"], [5, 6])

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_stereo_recording_routes_consecutive_inputs_without_monitoring(self):
        self.song.tracks[0].convert_channels(2)
        self.engine.input_channel = 1
        self.engine.output_channel = 2
        self.engine.start(0)
        self.assertEqual(self.engine.stream.options["channels"], (3, 4))
        incoming = np.tile([0.9, 0.2, -0.4], (8, 1)).astype(np.float32)
        output = np.ones((8, 4), dtype=np.float32)
        self.engine._record_callback(incoming, output, 8, None, None)
        np.testing.assert_array_equal(output, 0)
        undo = self.engine.stop()
        np.testing.assert_array_equal(self.song.tracks[0].audio, incoming[:, 1:3])
        self.assertEqual(undo[1].shape, (0, 2))

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_stereo_punch_preserves_both_channels_and_compensates_offset(self):
        track = self.song.tracks[0]
        track.convert_channels(2)
        original = np.tile([0.1, -0.2], (12, 1)).astype(np.float32)
        track.write(0, original)
        self.song.marker_a, self.song.marker_b, self.song.punch = 3, 7, True
        self.engine.recording_offset = 2
        self.engine.start(0)
        incoming = np.column_stack((np.arange(12) / 20, -np.arange(12) / 20)).astype(np.float32)
        output = np.zeros((12, 2), dtype=np.float32)
        self.engine._record_callback(incoming[:6], output[:6], 6, None, None)
        with self.assertRaises(sd.CallbackStop):
            self.engine._record_callback(incoming[6:], output[6:], 6, None, None)
        self.engine.stop()
        expected = original.copy()
        expected[3:7] = incoming[5:9]
        np.testing.assert_array_equal(track.audio, expected)
        np.testing.assert_array_equal(output[3:7], 0)

    @patch("eighttrack.engine.sd.OutputStream", FakeStream)
    def test_varispeed_playback_matches_model_across_callbacks(self):
        self.song.tape_speed = 0.75
        self.song.tracks[0].write(0, np.sin(np.arange(100) * 0.1) * 0.1)
        self.engine.start()
        first, second = np.zeros((7, 2), dtype=np.float32), np.zeros((9, 2), dtype=np.float32)
        self.engine._play_callback(first, 7, None, None)
        self.engine._play_callback(second, 9, None, None)
        np.testing.assert_allclose(np.concatenate([first, second]), self.song.mix_tape(0, 16), atol=1e-7)
        self.assertEqual(self.engine.position, 12)
        self.engine.stop()

    @patch("eighttrack.engine.sd.OutputStream", FakeStream)
    def test_loop_wraps_multiple_times_within_one_callback(self):
        self.song.tracks[0].write(0, np.arange(6) * 0.1)
        self.song.marker_a, self.song.marker_b, self.song.loop = 2, 5, True
        self.engine.start()
        output = np.zeros((8, 2), dtype=np.float32)
        self.engine._play_callback(output, 8, None, None)
        expected = self.song.mix(2, 3)
        np.testing.assert_allclose(output, np.concatenate([expected, expected, expected[:2]]))
        self.assertEqual(self.engine.position, 4)
        self.engine.stop()

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_count_in_does_not_advance_tape_or_record_click(self):
        self.song.count_in = 1
        self.song.bpm = 240
        self.engine.start(0)
        count = round(SAMPLE_RATE / 4)
        output = np.zeros((count + 3, 2), dtype=np.float32)
        self.engine._record_callback(np.full((count + 3, 1), 0.4), output, count + 3, None, None)
        self.assertTrue(output[:count].any())
        self.assertEqual(self.engine.position, 3)
        self.engine.stop()
        np.testing.assert_allclose(self.song.tracks[0].audio, [0.4] * 3)

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_punch_monitors_lead_in_and_compensates_delayed_input(self):
        self.song.tracks[0].write(0, np.full(12, 0.1))
        self.song.marker_a, self.song.marker_b, self.song.punch = 3, 7, True
        self.engine.recording_offset = 2
        before = self.song.mix(0, 12)
        self.engine.start(0)
        output = np.zeros((12, 2), dtype=np.float32)
        incoming = (np.arange(12, dtype=np.float32) / 20)[:, None]
        with self.assertRaises(sd.CallbackStop):
            self.engine._record_callback(incoming, output, 12, None, None)
        np.testing.assert_allclose(output[:3], before[:3])
        np.testing.assert_allclose(output[3:7], 0)
        np.testing.assert_allclose(output[7:9], before[7:9])
        self.engine.stop()
        np.testing.assert_allclose(self.song.tracks[0].audio, [0.1] * 3 + [0.25, 0.3, 0.35, 0.4] + [0.1] * 5)

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_cancel_count_in_keeps_original_audio(self):
        self.song.count_in = 4
        self.song.tracks[0].write(0, np.ones(5))
        self.engine.start(0)
        self.engine._record_callback(np.ones((8, 1)), np.zeros((8, 2)), 8, None, None)
        self.assertIsNone(self.engine.stop())
        np.testing.assert_array_equal(self.song.tracks[0].audio, 1)

    def test_invalid_punch_or_recording_speed_never_opens_stream(self):
        self.song.punch = True
        with patch("eighttrack.engine.sd.Stream") as stream, self.assertRaises(ValueError):
            self.engine.start(0)
        stream.assert_not_called()
        self.song.punch = False
        self.song.tape_speed = 0.8
        with self.assertRaises(ValueError):
            self.engine.start(0)

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_overdub_plays_other_tracks_and_commits_on_stop(self):
        self.song.tracks[0].write(0, np.full(10, 0.1))
        self.song.tracks[1].write(0, np.full(10, 0.2))
        self.engine.seek(3)
        self.engine.start(record_track=0)
        output = np.zeros((4, 2), dtype=np.float32)
        self.engine._record_callback(np.full((4, 1), 0.5), output, 4, None, None)
        np.testing.assert_allclose(output, self.song.mix(3, 4, exclude=0))
        np.testing.assert_allclose(self.song.tracks[0].audio, 0.1)
        undo = self.engine.stop()
        np.testing.assert_allclose(self.song.tracks[0].audio, [0.1] * 3 + [0.5] * 4 + [0.1] * 3)
        self.assertEqual(undo[0], 0)
        np.testing.assert_allclose(undo[1], 0.1)
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.stop())

    @patch("eighttrack.engine.sd.OutputStream", FakeStream)
    def test_playback_stops_at_end_and_pads_final_block(self):
        self.song.tracks[0].write(0, np.ones(3) * 0.1)
        self.engine.start()
        output = np.ones((5, 2), dtype=np.float32)
        with self.assertRaises(sd.CallbackStop):
            self.engine._play_callback(output, 5, None, None)
        self.assertEqual(self.engine.position, 3)
        np.testing.assert_array_equal(output[3:], 0)
        self.engine.stop()

    @patch("eighttrack.engine.sd.Stream", FakeStream)
    def test_recording_limit_input_channel_and_clipping(self):
        self.engine.seek(MAX_FRAMES - 2)
        self.engine.input_channel = 1
        self.engine.start(2)
        output = np.ones((4, 2), dtype=np.float32)
        with self.assertRaises(sd.CallbackStop):
            self.engine._record_callback(np.full((4, 2), 2), output, 4, None, None)
        self.assertEqual(self.engine.recorded_frames, 2)
        np.testing.assert_array_equal(self.engine.record_buffer, 1)
        self.assertTrue(self.engine.clipped)
        self.engine.record_track = None
        self.engine.stop()

    @patch("eighttrack.engine.sd.Stream", side_effect=sd.PortAudioError("No device"))
    def test_failed_device_start_does_not_leave_transport_running(self, stream):
        self.song.count_in = 4
        with self.assertRaises(sd.PortAudioError):
            self.engine.start(0)
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.stream)
        self.assertEqual(len(self.engine.record_buffer), 0)
        self.assertEqual(self.engine.count_remaining, 0)
        self.assertIsNone(self.engine.record_track)

    def test_click_is_not_stored_in_song(self):
        self.engine.metronome = True
        self.assertTrue(self.engine._render(512).any())
        self.assertEqual(self.song.length, 0)


if __name__ == "__main__":
    unittest.main()