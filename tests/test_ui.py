import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import sounddevice as sd
from PySide6.QtCore import Qt, QSettings
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QDockWidget, QLabel, QMessageBox

from eighttrack.model import SAMPLE_RATE, Song, load_song
from eighttrack.ui import AudioDevicesDialog, BounceDialog, BounceHistoryDialog, StudioWindow
from eighttrack.effects import Effect
from eighttrack.effects_ui import EffectsPanel
from eighttrack.tape_ui import TapeSettingsDialog, TrackToolsDialog


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = QSettings(str(Path(directory.name) / "settings.ini"), QSettings.Format.IniFormat)
        settings_patch = patch("eighttrack.ui.QSettings", return_value=settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.window = StudioWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_8t_branding_and_save_defaults(self):
        self.assertTrue(self.window.windowTitle().endswith(" | 8T: 8 Track DAW"))
        self.assertIn("8T: 8 Track DAW", [label.text() for label in self.window.findChildren(QLabel)])
        self.assertEqual(self.window.recovery_path.suffix, ".8t")
        with patch.object(self.window, "choose_save_path", return_value=None) as choose:
            self.assertFalse(self.window.save_as())
            choose.assert_called_once_with("Save song", "Untitled.8t", "8t")
        self.window.path = Path("Legacy.porta")
        with patch.object(self.window, "choose_save_path", return_value=None) as choose:
            self.assertFalse(self.window.save_as())
            choose.assert_called_once_with("Save song", "Legacy.8t", "8t")
        self.assertEqual(self.window.path, Path("Legacy.porta"))

    def test_open_dialog_accepts_new_and_legacy_projects(self):
        with patch("eighttrack.ui.QFileDialog.getOpenFileName", return_value=("", "")) as choose:
            self.window.open_song()
            self.assertEqual(choose.call_args.args[-1], "8T song (*.8t *.porta)")
            self.window.recover_autosave()
            self.assertEqual(choose.call_args.args[-1], "8T song (*.8t *.porta)")

    def test_only_one_track_can_be_armed(self):
        self.window.strips[0].arm.setChecked(True)
        self.window.strips[3].arm.setChecked(True)
        self.assertEqual(self.window.armed, 3)
        self.assertFalse(self.window.strips[0].arm.isChecked())
        self.assertTrue(self.window.record_button.isEnabled())

    def test_stereo_conversion_waveforms_and_undo_include_takes(self):
        track = self.window.song.tracks[0]
        track.write(0, np.array([0.2, 0.4], dtype=np.float32))
        track.keep_take("Original")
        dialog = TrackToolsDialog(self.window, 0)
        self.addCleanup(dialog.deleteLater)
        dialog.channel_format.setCurrentIndex(1)
        with patch("eighttrack.tape_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            dialog.convert_format()
        self.assertEqual(track.channels, 1)
        self.assertEqual(dialog.channel_format.currentIndex(), 0)
        dialog.channel_format.setCurrentIndex(1)
        with patch("eighttrack.tape_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog.convert_format()
        self.assertEqual(track.audio.shape, (2, 2))
        self.assertEqual(track.takes[0].audio.shape, (2, 2))
        self.assertEqual(self.window.strips[0].waveform.peaks.shape, (2, 2))
        self.assertEqual(self.window.strips[0].pan_label.text(), "BAL  C")
        self.window.undo()
        self.assertEqual(track.audio.shape, (2,))
        self.assertEqual(track.takes[0].audio.shape, (2,))
        self.window.redo()
        self.assertEqual(track.channels, 2)
        with patch("eighttrack.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.window.clear_track(0)
        self.assertEqual(track.audio.shape, (0, 2))
        self.window.undo()
        self.assertEqual(track.audio.shape, (2, 2))

    def test_stereo_import_uses_track_format(self):
        self.window.song.tracks[0].convert_channels(2)
        samples = np.array([[0.2, -0.4], [0.1, -0.3]], dtype=np.float32)
        with patch("eighttrack.ui.QFileDialog.getOpenFileName", return_value=("keys.wav", "")), patch(
                "eighttrack.ui.import_audio", return_value=samples) as importer:
            self.window.import_track(0)
        importer.assert_called_once_with("keys.wav", channels=2)
        np.testing.assert_array_equal(self.window.song.tracks[0].audio, samples)
        np.testing.assert_allclose(self.window.strips[0].waveform.peaks, np.abs(samples))
        self.window.undo()
        self.assertEqual(self.window.song.tracks[0].audio.shape, (0, 2))

    def test_stereo_audio_device_input_pairs_validate_both_channels(self):
        self.window.song.tracks[0].convert_channels(2)
        self.window.arm_track(0, True)
        dialog = self.make_audio_dialog()
        self.assertEqual(dialog.input_channel.count(), 0)
        dialog.inputs.setCurrentIndex(dialog.inputs.findData(0))
        self.assertEqual(dialog.input_channel.count(), 3)
        self.assertEqual(dialog.input_channel.itemText(2), "Inputs 3 / 4")
        dialog.input_channel.setCurrentIndex(2)
        with patch("eighttrack.ui.sd.check_input_settings") as check_input, patch(
                "eighttrack.ui.sd.check_output_settings"), patch("eighttrack.ui.sd.Stream") as stream:
            dialog.accept()
        self.assertEqual(check_input.call_args.kwargs["channels"], 4)
        self.assertEqual(stream.call_args.kwargs["channels"][0], 4)
        self.assertEqual(self.window.engine.input_channel, 2)

    def test_stereo_bounce_format_is_historical(self):
        song = self.window.song
        song.tracks[0].write(0, np.ones(4, dtype=np.float32) * 0.1)
        song.tracks[1].convert_channels(2)
        dialog = BounceDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        dialog.destination.setCurrentIndex(1)
        self.assertEqual(dialog.format_label.text(), "Stereo / 44.1 kHz")
        self.assertTrue(self.window.bounce_tracks([0], 1))
        song.tracks[1].convert_channels(1)
        history = BounceHistoryDialog(self.window)
        self.addCleanup(history.deleteLater)
        self.assertEqual(history.table.item(0, 4).text(), "Stereo")

    def make_audio_dialog(self, missing_defaults=False):
        devices = [
            dict(name="USB Interface", hostapi=0, max_input_channels=4, max_output_channels=6, default_samplerate=48000),
            dict(name="Built-in Audio", hostapi=0, max_input_channels=1, max_output_channels=2, default_samplerate=44100),
            dict(name="USB Interface", hostapi=1, max_input_channels=2, max_output_channels=2, default_samplerate=44100),
            dict(name="Mono speaker", hostapi=0, max_input_channels=0, max_output_channels=1, default_samplerate=44100),
        ]
        def query_devices(kind=None):
            if kind and missing_defaults:
                raise sd.PortAudioError("No default device")
            return devices[1] if kind else devices
        with patch("eighttrack.ui.sd.query_devices", side_effect=query_devices), patch(
                "eighttrack.ui.sd.query_hostapis", return_value=[{"name": "ALSA"}, {"name": "JACK"}]):
            dialog = AudioDevicesDialog(self.window.engine, self.window)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_audio_device_labels_and_channel_limits(self):
        dialog = self.make_audio_dialog()
        self.assertIn("System default - Built-in Audio [ALSA]", dialog.inputs.itemText(0))
        self.assertIn("USB Interface [ALSA] - 4 in / 6 out", dialog.inputs.itemText(dialog.inputs.findData(0)))
        self.assertIn("USB Interface [JACK]", dialog.inputs.itemText(dialog.inputs.findData(2)))
        self.assertEqual(dialog.outputs.findData(3), -1)
        dialog.inputs.setCurrentIndex(dialog.inputs.findData(0))
        dialog.outputs.setCurrentIndex(dialog.outputs.findData(0))
        self.assertEqual(dialog.input_channel.count(), 4)
        self.assertEqual(dialog.output_pair.count(), 3)
        dialog.input_channel.setCurrentIndex(3)
        dialog.output_pair.setCurrentIndex(2)
        dialog.inputs.setCurrentIndex(dialog.inputs.findData(1))
        dialog.outputs.setCurrentIndex(dialog.outputs.findData(1))
        self.assertEqual(dialog.input_channel.count(), 1)
        self.assertEqual(dialog.input_channel.currentData(), 0)
        self.assertEqual(dialog.output_pair.count(), 1)
        self.assertEqual(dialog.output_pair.currentData(), 0)

    def test_audio_devices_validate_duplex_and_apply_selected_channels(self):
        dialog = self.make_audio_dialog()
        dialog.inputs.setCurrentIndex(dialog.inputs.findData(0))
        dialog.outputs.setCurrentIndex(dialog.outputs.findData(0))
        dialog.input_channel.setCurrentIndex(3)
        dialog.output_pair.setCurrentIndex(2)
        with patch("eighttrack.ui.sd.check_input_settings") as check_input, patch(
                "eighttrack.ui.sd.check_output_settings") as check_output, patch("eighttrack.ui.sd.Stream") as stream:
            dialog.accept()
        self.assertEqual(check_input.call_args.kwargs["channels"], 4)
        self.assertEqual(check_output.call_args.kwargs["channels"], 6)
        self.assertEqual(stream.call_args.kwargs["device"], (0, 0))
        self.assertEqual(stream.call_args.kwargs["channels"], (4, 6))
        stream.return_value.close.assert_called_once()
        stream.return_value.start.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.window.engine.input_channel, 3)
        self.assertEqual(self.window.engine.output_channel, 4)
        reopened = self.make_audio_dialog()
        self.assertEqual(reopened.input_channel.currentData(), 3)
        self.assertEqual(reopened.output_pair.currentData(), 4)

    def test_audio_device_failure_keeps_dialog_open_and_engine_unchanged(self):
        dialog = self.make_audio_dialog()
        dialog.inputs.setCurrentIndex(dialog.inputs.findData(0))
        with patch("eighttrack.ui.sd.check_input_settings"), patch("eighttrack.ui.sd.check_output_settings"), patch(
                "eighttrack.ui.sd.Stream", side_effect=RuntimeError("Incompatible drivers")), patch(
                "eighttrack.ui.QMessageBox.warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIsNone(self.window.engine.input_device)
        self.assertEqual(self.window.engine.input_channel, 0)
        dialog.reject()
        self.assertIsNone(self.window.engine.input_device)

    def test_audio_devices_without_defaults_require_explicit_devices(self):
        dialog = self.make_audio_dialog(missing_defaults=True)
        accept = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.assertFalse(accept.isEnabled())
        dialog.inputs.setCurrentIndex(dialog.inputs.findData(0))
        dialog.outputs.setCurrentIndex(dialog.outputs.findData(0))
        self.assertTrue(accept.isEnabled())

    def test_songwriting_typing_and_undo_do_not_trigger_audio_shortcuts(self):
        editor = self.window.lyrics_editor
        self.window.workspace_tabs.setCurrentWidget(editor)
        editor.setFocus()
        self.app.processEvents()
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.apply_track_edit(0, lambda: self.window.song.tracks[0].write(0, np.zeros(10)))
        self.window.engine.position = 5
        with patch.object(self.window.engine, "start") as start:
            QTest.keyClicks(editor, "[Verse] R r words")
            self.assertEqual(editor.toPlainText(), "[Verse] R r words")
            QTest.keyClick(editor, Qt.Key.Key_Home)
            self.assertEqual(editor.textCursor().position(), 0)
            QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(editor.toPlainText(), "")
            QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(editor.toPlainText(), "[Verse] R r words")
        start.assert_not_called()
        self.assertEqual(self.window.engine.position, 5)
        np.testing.assert_array_equal(self.window.song.tracks[0].audio, 0)
        self.assertEqual(self.window.song.lyrics, editor.toPlainText())
        self.assertTrue(self.window.dirty)

    def test_songwriting_save_as_reopen_and_clear(self):
        text = "[Verse]\nAm  F\nCaf\u00e9 lights\n\n[Chorus]\nAgain\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "My song.8t"
            editor = self.window.lyrics_editor
            editor.setPlainText(text)
            with patch.object(self.window, "choose_save_path", return_value=path):
                self.assertTrue(self.window.save_as())
            self.assertFalse(self.window.dirty)
            self.assertEqual(path.with_suffix(".lyrics.txt").read_bytes(), text.encode("utf-8"))
            self.window.replace_song(load_song(path), path)
            self.assertEqual(editor.toPlainText(), text)
            self.assertFalse(self.window.dirty)
            editor.clear()
            self.assertTrue(self.window.save())
            self.assertEqual(path.with_suffix(".lyrics.txt").read_text(encoding="utf-8"), "")
            self.assertEqual(load_song(path).lyrics, "")
            self.window.replace_song(Song(lyrics="Another song"), None)
            self.window.new_song()
            self.assertEqual(editor.toPlainText(), "")
            self.assertFalse(editor.document().isUndoAvailable())
            self.assertFalse(self.window.dirty)

    def test_songwriting_copy_failure_keeps_project_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window.path = Path(directory) / "song.porta"
            self.window.recovery_path = Path(directory) / "recovery.porta"
            self.window.lyrics_editor.setPlainText("Keep these words")
            self.window.autosave()
            self.wait_for_job()
            with patch("eighttrack.ui.export_lyrics", side_effect=OSError("Disk full")), patch.object(self.window, "show_error") as error:
                self.assertFalse(self.window.save())
            self.assertEqual(load_song(self.window.path).lyrics, "Keep these words")
            self.assertTrue(self.window.recovery_path.exists())
            self.assertTrue(self.window.dirty)
            error.assert_called_once()

    def test_songwriting_recovery_and_worker_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window.recovery_path = Path(directory) / "snapshot.porta"
            self.window.lyrics_editor.setPlainText("Unfinished chorus")
            self.window.autosave()
            self.assertFalse(self.window.lyrics_editor.isEnabled())
            self.wait_for_job()
            self.assertTrue(self.window.lyrics_editor.isEnabled())
            self.window.restore_recovery(self.window.recovery_path)
            self.assertEqual(self.window.lyrics_editor.toPlainText(), "Unfinished chorus")
            self.assertTrue(self.window.dirty)
            self.assertEqual(list(Path(directory).glob("*.txt")), [])

    def test_songwriting_remains_editable_during_transport(self):
        editor = self.window.lyrics_editor
        self.window.view_tabs.setCurrentIndex(1)
        self.assertIs(self.window.workspace_tabs.currentWidget(), editor)
        for mode in ("playing", "recording"):
            with self.subTest(mode=mode):
                self.window.engine.mode = mode
                self.window.update_controls()
                self.assertTrue(editor.isEnabled())
                editor.insertPlainText(f"{mode}\n")
                self.assertEqual(self.window.song.lyrics, editor.toPlainText())
                self.assertEqual(self.window.engine.mode, mode)
        self.window.engine.mode = "stopped"

    def test_follow_toggle_freezes_and_resynchronizes_waveform_heads(self):
        self.assertTrue(self.window.follow_button.isChecked())
        self.window.engine.position = SAMPLE_RATE
        self.window.tick()
        for mode in ("playing", "recording"):
            with self.subTest(mode=mode):
                self.window.engine.mode = mode
                self.window.update_controls()
                self.assertTrue(self.window.follow_button.isEnabled())
                self.window.follow_button.click()
                previous = self.window.strips[0].waveform.position
                self.window.engine.position += SAMPLE_RATE
                self.window.tick()
                for strip in self.window.strips:
                    self.assertEqual(strip.waveform.position, previous)
                self.assertEqual(self.window.seek_slider.value(), round(
                    self.window.engine.position / self.window.duration * 10000))
                self.window.follow_button.click()
                for strip in self.window.strips:
                    self.assertEqual(strip.waveform.position, self.window.engine.position)
                self.assertEqual(self.window.engine.mode, mode)
        self.window.engine.mode = "stopped"
        self.assertFalse(self.window.dirty)

    def test_detached_waveform_click_does_not_seek_transport(self):
        self.window.engine.position = SAMPLE_RATE
        self.window.follow_button.click()
        for mode in ("stopped", "playing", "recording"):
            with self.subTest(mode=mode):
                self.window.engine.mode = mode
                self.window.strips[0].waveform.seek_requested.emit(0.5)
                self.window.tick()
                self.assertEqual(self.window.engine.position, SAMPLE_RATE)
                for strip in self.window.strips:
                    self.assertEqual(strip.waveform.position, self.window.duration // 2)
        self.window.engine.mode = "stopped"

    def test_following_waveform_click_seeks_only_while_stopped(self):
        self.window.strips[0].waveform.seek_requested.emit(0.5)
        expected = self.window.duration // 2
        self.assertEqual(self.window.engine.position, expected)
        self.window.engine.mode = "playing"
        self.window.strips[0].waveform.seek_requested.emit(0.25)
        self.assertEqual(self.window.engine.position, expected)
        self.window.engine.mode = "stopped"

    def wait_for_job(self):
        job = self.window._job
        if job is not None:
            completed = QSignalSpy(job.finished)
            if not job.isFinished():
                self.assertTrue(completed.wait(5000))
            self.app.processEvents()
        self.assertFalse(self.window.busy)

    def test_worker_completes_and_restores_controls(self):
        results = []
        self.window.run_job(lambda: 42, results.append, "Test render")
        self.assertTrue(self.window.busy)
        self.assertFalse(self.window.play_button.isEnabled())
        self.wait_for_job()
        self.assertEqual(results, [42])
        self.assertTrue(self.window.play_button.isEnabled())

    def test_fast_tape_preparation_runs_as_background_job(self):
        self.window.song.tracks[0].write(0, np.full(1000, 0.1))
        self.window.song.tape_speed = 1.25
        with patch.object(self.window.engine, "start") as start:
            self.window.start_transport(None)
            self.assertTrue(self.window.busy)
            self.wait_for_job()
        start.assert_called_once_with(None)

    def test_autosave_recovery_preserves_unsaved_audio_and_takes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window.recovery_path = Path(directory) / "snapshot.porta"
            track = self.window.song.tracks[0]
            track.write(0, np.ones(100))
            track.keep_take("Safety")
            self.window.mark_dirty()
            self.window.autosave()
            self.wait_for_job()
            self.assertTrue(self.window.recovery_path.is_file())
            self.assertTrue(self.window.dirty)
            original = self.window.recovery_path
            self.window.restore_recovery(original)
            self.assertTrue(original.is_file())
            self.assertIsNone(self.window.path)
            self.assertEqual(self.window.song.tracks[0].takes[0].name, "Safety")
            self.assertTrue(self.window.dirty)

    def test_effected_bounce_uses_worker_and_remains_undoable(self):
        self.window.song.tracks[0].write(0, np.full(100, 0.2))
        self.window.song.tracks[0].effects.append(Effect.builtin("Gain"))
        self.assertTrue(self.window.bounce_tracks([0], 1))
        self.wait_for_job()
        self.assertEqual(len(self.window.song.bounce_history), 1)
        self.window.undo()
        self.assertTrue(self.window.song.bounce_history[0].undone)
        self.assertEqual(len(self.window.song.tracks[1].audio), 0)

    def test_worker_failure_restores_controls_without_starting_audio(self):
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.song.tracks[0].effects.append(Effect("vst3", "Missing", path="/missing.vst3"))
        with patch.object(self.window, "show_error") as error, patch.object(self.window.engine, "start") as start:
            self.window.start_transport(None)
            self.wait_for_job()
        start.assert_not_called()
        error.assert_called_once()
        self.assertTrue(self.window.play_button.isEnabled())

    def test_multiple_undo_redo_and_take_bank(self):
        track = self.window.song.tracks[0]
        track.write(0, np.ones(100))
        self.window.apply_track_edit(0, lambda: track.keep_take("First"), takes=True)
        self.window.apply_track_edit(0, lambda: track.edit_range(0, 100, "silence", fade_frames=0))
        self.window.undo()
        np.testing.assert_array_equal(track.audio, 1)
        self.window.undo()
        self.assertEqual(track.takes, [])
        self.window.redo()
        self.assertEqual(track.takes[0].name, "First")
        self.window.redo()
        np.testing.assert_array_equal(track.audio, 0)
        self.window.undo()
        self.window.apply_track_edit(0, lambda: track.write(0, np.full(100, 0.5)))
        self.assertFalse(self.window.redo_action.isEnabled())

    def test_tape_settings_and_track_tools(self):
        self.window.song.tracks[0].write(0, np.ones(4410))
        self.window.engine.seek(2205)
        self.window.set_marker_here("marker_a")
        self.assertEqual(self.window.song.marker_a, 2205)
        settings = TapeSettingsDialog(self.window)
        settings.count_in.setValue(4)
        settings.speed.setValue(80)
        settings.name.setText("Verse")
        settings.store()
        self.assertEqual(self.window.song.count_in, 4)
        self.assertEqual(self.window.song.tape_speed, 0.8)
        self.assertEqual(self.window.song.markers, {"Verse": 2205})
        tools = TrackToolsDialog(self.window, 0)
        tools.channel_controls["eq_low"].setValue(3)
        tools.take_name.setText("Dry")
        tools.keep_take()
        self.assertEqual(self.window.song.tracks[0].eq_low, 3)
        self.assertEqual(tools.takes.count(), 1)
        settings.close()
        tools.close()

    def test_invalid_loop_toggle_and_marker_change_are_rejected(self):
        with patch.object(self.window, "show_error") as error:
            self.window.loop.setChecked(True)
            self.assertFalse(self.window.song.loop)
            self.assertFalse(self.window.loop.isChecked())
            self.window.set_tape_value("marker_b", SAMPLE_RATE)
            self.window.loop.setChecked(True)
            self.window.set_tape_value("marker_a", SAMPLE_RATE * 2)
        self.assertEqual(error.call_count, 2)
        self.assertEqual(self.window.song.marker_a, 0)
        self.assertTrue(self.window.song.loop)

    def test_track_naming_and_recording_status(self):
        strip = self.window.strips[0]
        strip.name.setText("  Lead vocal  ")
        strip.name.editingFinished.emit()
        self.assertEqual(strip.track.name, "Lead vocal")
        self.assertEqual(strip.name.cursorPosition(), 0)
        self.assertTrue(self.window.dirty)
        strip.arm.setChecked(True)
        self.assertIn("01 - Lead vocal", self.window.record_button.toolTip())
        with patch.object(self.window.engine, "start"):
            self.window.start_transport(0)
        self.assertEqual(self.window.statusBar().currentMessage(), "Recording: 01 - Lead vocal")
        strip.name.setText("   ")
        strip.name.editingFinished.emit()
        self.assertEqual(strip.track.name, "Track 1")

    def test_bounce_dialog_selection_and_undo_history(self):
        self.window.song.tracks[0].name = "Drums"
        self.window.song.tracks[0].write(0, np.ones(10) * 0.2)
        dialog = BounceDialog(self.window)
        self.assertEqual(dialog.destination.currentData(), 1)
        self.assertFalse(dialog.bounce_button.isEnabled())
        dialog.sources.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.bounce_button.isEnabled())
        self.assertIn("Drums", dialog.sources.item(0).text())
        dialog.perform_bounce()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        record = self.window.song.bounce_history[0]
        np.testing.assert_allclose(self.window.song.tracks[1].audio, 0.16)
        self.assertTrue(self.window.undo_action.isEnabled())
        self.window.undo()
        self.assertTrue(record.undone)
        self.assertEqual(len(self.window.song.tracks[1].audio), 0)
        self.window.song.tracks[0].name = "Renamed"
        history = BounceHistoryDialog(self.window)
        self.assertEqual(history.table.item(0, 1).text(), "01 - Drums")
        self.assertEqual(history.table.item(0, 5).text(), "Undone")
        history.close()
        dialog.close()

    def test_bounce_destination_cannot_be_a_source(self):
        self.window.song.tracks[0].write(0, np.ones(10))
        dialog = BounceDialog(self.window)
        dialog.sources.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.destination.setCurrentIndex(0)
        self.assertEqual(dialog.selected_sources(), [])
        self.assertFalse(dialog.bounce_button.isEnabled())
        self.assertFalse(dialog.sources.item(0).flags() & Qt.ItemFlag.ItemIsEnabled)
        dialog.close()

    def test_declining_overwrite_preserves_audio_and_history(self):
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.song.tracks[1].write(0, np.ones(5))
        with patch("eighttrack.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.window.bounce_tracks([0], 1))
        np.testing.assert_array_equal(self.window.song.tracks[1].audio, np.ones(5))
        self.assertEqual(self.window.song.bounce_history, [])
        self.assertIsNone(self.window.undo_audio)
        self.assertFalse(self.window.dirty)

    def test_bounce_undo_restores_existing_destination(self):
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.song.tracks[1].write(0, np.ones(5) * 0.1)
        with patch("eighttrack.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.window.bounce_tracks([0], 1))
        self.window.undo()
        np.testing.assert_allclose(self.window.song.tracks[1].audio, 0.1)
        self.assertEqual(len(self.window.song.tracks[1].audio), 5)

    def test_later_edit_undo_does_not_mark_bounce_undone(self):
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.bounce_tracks([0], 1)
        with patch("eighttrack.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.window.clear_track(0)
        self.window.undo()
        self.assertFalse(self.window.song.bounce_history[0].undone)
        self.window.replace_song(Song(), None)
        self.assertIsNone(self.window.undo_bounce)
        self.assertEqual(self.window.song.bounce_history, [])

    def test_bounce_is_disabled_during_transport(self):
        self.window.engine.mode = "playing"
        self.window.update_controls()
        self.assertFalse(self.window.bounce_action.isEnabled())
        self.assertTrue(self.window.history_action.isEnabled())
        self.assertFalse(self.window.bounce_tracks([0], 1))
        with patch("eighttrack.ui.BounceDialog") as dialog:
            self.window.show_bounce()
        dialog.assert_not_called()
        self.window.engine.mode = "stopped"

    def test_failed_bounce_keeps_previous_undo_and_history(self):
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.bounce_tracks([0], 1)
        previous_undo = self.window.undo_audio
        previous_record = self.window.undo_bounce
        with patch.object(self.window.song, "bounce", side_effect=ValueError("Missing plugin")), \
                patch.object(self.window, "show_error") as show_error:
            self.assertFalse(self.window.bounce_tracks([0], 2))
        show_error.assert_called_once()
        self.assertIs(self.window.undo_audio, previous_undo)
        self.assertIs(self.window.undo_bounce, previous_record)
        self.assertEqual(len(self.window.song.bounce_history), 1)
        self.assertEqual(len(self.window.song.tracks[2].audio), 0)
        self.assertIsNone(QApplication.overrideCursor())

    def test_bounce_dialog_layouts_with_long_names(self):
        for track in self.window.song.tracks:
            track.name = "Long recording name " * 3
            track.write(0, np.ones(10))
        self.window.song.bounce([0, 1, 2, 3, 4, 5, 6], 7)
        self.window.refresh()
        for strip in self.window.strips:
            self.assertEqual(strip.name.cursorPosition(), 0)
            self.assertIn(strip.track.name, strip.name.toolTip())
        for dialog_class in (BounceDialog, BounceHistoryDialog):
            dialog = dialog_class(self.window)
            dialog.show()
            for width, height in ((480, 400), (880, 500)):
                dialog.resize(width, height)
                self.app.processEvents()
                self.assertLessEqual(dialog.width(), width)
                self.assertFalse(dialog.grab().toImage().isNull())
                if isinstance(dialog, BounceDialog):
                    corner = dialog.bounce_button.mapTo(dialog, dialog.bounce_button.rect().bottomRight())
                    self.assertLess(corner.x(), dialog.width())
                    self.assertLess(corner.y(), dialog.height())
                else:
                    self.assertGreater(dialog.table.rowHeight(0), 100)
            dialog.close()

    def test_bounce_history_empty_and_newest_first(self):
        empty = BounceHistoryDialog(self.window)
        self.assertEqual(empty.summary.text(), "No bounces yet")
        empty.close()
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.bounce_tracks([0], 1)
        self.window.bounce_tracks([1], 2)
        history = BounceHistoryDialog(self.window)
        self.assertEqual(history.table.item(0, 2).text(), "03 - Track 3")
        self.assertEqual(history.table.item(1, 2).text(), "02 - Track 2")
        history.close()

    def test_mixer_updates_model_and_dirty_state(self):
        strip = self.window.strips[2]
        strip.volume.setValue(40)
        strip.pan.setValue(-75)
        strip.solo.setChecked(True)
        self.window.tempo.setValue(125)
        self.assertEqual(self.window.song.tracks[2].volume, 0.4)
        self.assertEqual(self.window.song.tracks[2].pan, -0.75)
        self.assertTrue(self.window.song.tracks[2].solo)
        self.assertEqual(self.window.song.bpm, 125)
        self.assertTrue(self.window.dirty)

    def test_seek_and_undo(self):
        self.window.seek_fraction(0.5)
        self.assertEqual(self.window.engine.position, SAMPLE_RATE * 5)
        self.window.song.tracks[0].write(0, np.ones(10))
        self.window.undo_audio = (0, np.zeros(0, dtype=np.float32))
        self.window.undo()
        self.assertEqual(self.window.song.length, 0)
        self.assertFalse(self.window.undo_action.isEnabled())

    def test_save_and_replace_song(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window.path = Path(directory) / "Song.porta"
            self.window.song.tracks[0].write(0, np.ones(10))
            self.window.mark_dirty()
            self.assertTrue(self.window.save())
            self.assertFalse(self.window.dirty)
            self.assertEqual(load_song(self.window.path).length, 10)
            self.window.replace_song(Song(), None)
            self.assertEqual(self.window.song.length, 0)
            self.assertIs(self.window.engine.song, self.window.song)
            self.assertEqual(self.window.tempo.value(), 100)

    def test_save_as_uses_the_confirmed_destination_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "my.song.porta"
            with patch.object(self.window, "choose_save_path", return_value=destination):
                self.assertTrue(self.window.save_as())
            self.assertEqual(self.window.path, destination)
            self.assertTrue(destination.is_file())

    @patch("eighttrack.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel)
    def test_cancel_keeps_unsaved_song(self, question):
        self.window.mark_dirty()
        original = self.window.song
        self.window.new_song()
        self.assertIs(self.window.song, original)
        self.assertTrue(self.window.dirty)

    def test_layout_at_small_and_desktop_sizes(self):
        self.window.effects_dock.hide()
        for width, height in [(680, 650), (1200, 800)]:
            self.window.resize(width, height)
            self.app.processEvents()
            self.assertGreaterEqual(self.window.strips[0].volume.height(), 115)
            self.assertLessEqual(self.window.clock.geometry().right(), self.window.centralWidget().width())
            for strip in self.window.strips:
                self.assertGreater(strip.waveform.width(), 90)
            image = self.window.grab().toImage()
            self.assertFalse(image.isNull())
            if width == 1200:
                strip = self.window.strips[0]
                viewport = strip.parentWidget().parentWidget()
                bottom = strip.clear_button.mapTo(viewport, strip.clear_button.rect().bottomRight())
                self.assertLess(bottom.y(), viewport.height())

    def test_effects_rack_add_edit_bypass_reorder_remove(self):
        dialog = EffectsPanel(self.window, 0)
        dialog.add_builtin()
        self.assertEqual(len(self.window.song.tracks[0].effects), 1)
        dialog.parameter_controls["gain_db"].setValue(-6)
        self.assertEqual(dialog.selected().parameters["gain_db"], -6)
        dialog.builtin_choice.setCurrentText("Reverb")
        dialog.add_builtin()
        dialog.move_effect(-1)
        self.assertEqual(self.window.song.tracks[0].effects[0].name, "Reverb")
        dialog.enabled.setChecked(False)
        self.assertFalse(dialog.selected().enabled)
        dialog.remove_effect()
        self.assertEqual(len(self.window.song.tracks[0].effects), 1)
        self.assertTrue(self.window.dirty)
        dialog.close()

    def test_rack_is_disabled_during_transport(self):
        self.window.engine.mode = "playing"
        self.window.update_controls()
        self.assertFalse(self.window.strips[0].effects_button.isEnabled())
        self.assertFalse(self.window.effects_panel.isEnabled())
        self.window.edit_effects(3)
        self.assertEqual(self.window.effects_panel.track_index, 0)
        self.window.engine.mode = "stopped"
        self.window.update_controls()
        self.assertTrue(self.window.effects_panel.isEnabled())

    def test_effects_dock_visibility_track_switch_and_song_replacement(self):
        dock = self.window.effects_dock
        panel = self.window.effects_panel
        self.assertEqual(self.window.dockWidgetArea(dock), Qt.DockWidgetArea.BottomDockWidgetArea)
        self.assertTrue(dock.isVisible())
        dock.close()
        self.assertFalse(dock.toggleViewAction().isChecked())
        dock.toggleViewAction().trigger()
        self.assertTrue(dock.isVisible())
        dock.close()
        self.window.strips[2].effects_button.click()
        self.assertTrue(dock.isVisible())
        self.assertEqual(panel.track_choice.currentIndex(), 2)
        panel.add_button.click()
        self.assertEqual(len(self.window.song.tracks[2].effects), 1)
        self.assertEqual(self.window.strips[2].effects_button.text(), "Effects (1)")
        panel.track_choice.setCurrentIndex(1)
        self.assertEqual(panel.effect_list.count(), 0)
        self.window.strips[1].name.setText("Vocals")
        self.window.strips[1].rename()
        self.assertIn("Vocals", panel.track_choice.currentText())
        original = self.window.song
        self.window.replace_song(Song(), None)
        panel.add_button.click()
        self.assertIs(panel.track, self.window.song.tracks[1])
        self.assertEqual(len(original.tracks[1].effects), 0)
        self.assertEqual(len(self.window.song.tracks[1].effects), 1)

    def test_effects_dock_locks_during_worker(self):
        self.window.run_job(lambda: 42, lambda result: None, "Test render")
        self.assertFalse(self.window.effects_panel.isEnabled())
        self.window.edit_effects(2)
        self.assertEqual(self.window.effects_panel.track_index, 0)
        self.window.effects_panel.add_button.click()
        self.assertEqual(len(self.window.song.tracks[0].effects), 0)
        self.window._job.wait()
        self.app.processEvents()
        self.assertFalse(self.window.busy)
        self.assertTrue(self.window.effects_panel.isEnabled())

    def test_effects_dock_layout_at_small_and_desktop_sizes(self):
        panel = self.window.effects_panel
        panel.builtin_choice.setCurrentText("Reverb")
        panel.add_button.click()
        for width, height in [(1200, 800), (680, 650)]:
            with self.subTest(width=width):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual(self.window.width(), width)
                self.assertEqual(self.window.height(), height)
                self.assertGreater(self.window.workspace_tabs.height(), 80)
                self.assertTrue(self.window.effects_dock.isVisible())
                self.assertLess(panel.vst_button.geometry().right(), panel.width())
                self.assertEqual(panel.parameters_area.horizontalScrollBar().maximum(), 0)
                for control in panel.parameter_controls.values():
                    self.assertEqual(control.width(), 180)
                    self.assertGreaterEqual(control.height(), control.minimumSizeHint().height())
                    for label in panel.parameter_labels.values():
                        self.assertFalse(control.geometry().intersects(label.geometry()))
                self.assertFalse(self.window.grab().isNull())

    def test_effects_dock_layout_persists_and_resets(self):
        dock = self.window.effects_dock
        self.window.float_effects_action.trigger()
        self.assertTrue(dock.isFloating())
        self.window.reset_docks()
        self.assertFalse(dock.isFloating())
        self.window.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, dock)
        self.window.lock_docks_action.setChecked(True)
        self.assertFalse(dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.assertFalse(self.window.float_effects_action.isEnabled())
        dock.close()
        self.window.close()
        reopened = StudioWindow()
        try:
            reopened.show()
            self.app.processEvents()
            self.assertFalse(reopened.effects_dock.isVisible())
            self.assertTrue(reopened.lock_docks_action.isChecked())
            self.assertEqual(reopened.dockWidgetArea(reopened.effects_dock), Qt.DockWidgetArea.TopDockWidgetArea)
            reopened.reset_docks()
            self.assertTrue(reopened.effects_dock.isVisible())
            self.assertFalse(reopened.lock_docks_action.isChecked())
            self.assertEqual(reopened.dockWidgetArea(reopened.effects_dock), Qt.DockWidgetArea.BottomDockWidgetArea)
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_declining_plugin_trust_never_loads_native_code(self):
        self.window.song.tracks[0].effects.append(Effect("vst3", "Example", path="/tmp/example.vst3"))
        dialog = EffectsPanel(self.window, 0)
        with patch.object(dialog, "trust_plugin", return_value=False), patch("eighttrack.effects.pb.load_plugin") as loader:
            dialog.load_saved()
        loader.assert_not_called()
        self.assertIsNone(dialog.selected().processor)
        dialog.close()


if __name__ == "__main__":
    unittest.main()