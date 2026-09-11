"""PySide6 desktop interface. Change layouts here without changing audio math."""

from datetime import datetime
import math
import os
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np
import sounddevice as sd
from PySide6.QtCore import Qt, QSettings, QStandardPaths, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSlider, QSpinBox, QStackedWidget, QStyle, QTabBar, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from eighttrack.engine import AudioEngine, BLOCK_SIZE
from eighttrack.model import (
    BounceRecord, MAX_FRAMES, SAMPLE_RATE, Song, Take, Track, export_lyrics, export_mix, export_stems, import_audio, load_song, save_song,
)


COLORS = ["#168777", "#3676b2", "#b27b17", "#ac556b", "#598137", "#5577a0", "#a55e35", "#6c7280"]
STYLE = """
QMainWindow, QDialog { background: #e8eae7; color: #232927; }
QWidget { font-family: 'DejaVu Sans'; font-size: 12px; }
QLabel { color: #26322c; }
QToolButton, QPushButton { background: #f9faf8; border: 1px solid #b4bdb6; border-radius: 4px; padding: 7px; }
QToolButton:hover, QPushButton:hover { background: #d6e7dd; border-color: #55846c; }
QToolButton:disabled, QPushButton:disabled { color: #969e98; background: #e1e4df; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #f9faf8; color: #232927; border: 1px solid #bbc3bc; border-radius: 3px; padding: 5px; }
QLineEdit:focus { border-color: #168777; }
QSlider::groove:vertical { width: 5px; background: #bbc3bc; border-radius: 2px; }
QSlider::handle:vertical { height: 23px; margin: 0 -11px; background: #fdfefb; border: 1px solid #708075; border-radius: 3px; }
QSlider::groove:horizontal { height: 4px; background: #b8c1ba; }
QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #426d59; border-radius: 3px; }
QProgressBar { background: #cdd4cd; border: none; border-radius: 2px; }
QProgressBar::chunk { background: #168777; }
QCheckBox { spacing: 5px; color: #26322c; }
QCheckBox::indicator { width: 14px; height: 14px; }
QMenuBar, QMenu { background: #f4f5f1; color: #232927; }
QStatusBar { background: #d5dcd5; color: #36473c; }
QDockWidget { color: #232927; }
QDockWidget::title { background: #d5dcd5; padding: 5px; }
QMainWindow::separator { background: #b4bdb6; height: 5px; width: 5px; }
QMainWindow::separator:hover { background: #168777; }
QToolTip { background: #f9faf8; color: #232927; border: 1px solid #899c8d; }
"""


def timecode(frames: int) -> str:
    seconds = frames / SAMPLE_RATE
    return f"{int(seconds // 60):02d}:{seconds % 60:06.3f}"


def track_label(index: int, name: str) -> str:
    return f"{index + 1:02d} - {name}"


def snapshot_takes(track: Track) -> tuple[str, list[Take]]:
    return track.active_take_name, [Take(take.name, take.audio.copy()) for take in track.takes]


def tool_button(parent: QWidget, icon: QStyle.StandardPixmap, tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setIcon(parent.style().standardIcon(icon))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setFixedSize(40, 36)
    return button


class AudioDevicesDialog(QDialog):
    def __init__(self, engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        armed = getattr(parent, "armed", None)
        self.input_width = engine.song.tracks[armed].channels if armed is not None else 1
        self.devices = list(sd.query_devices())
        self.hostapis = sd.query_hostapis()
        self.defaults = {}
        for kind in ("input", "output"):
            try:
                self.defaults[kind] = sd.query_devices(kind=kind)
            except sd.PortAudioError:
                self.defaults[kind] = None
        self.setWindowTitle("Audio devices")
        self.resize(600, 360)
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.inputs, self.outputs = QComboBox(), QComboBox()
        for kind, combo, selected in (("input", self.inputs, engine.input_device),
                                      ("output", self.outputs, engine.output_device)):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(24)
            default = self.defaults[kind]
            combo.addItem("System default - " + (self.device_label(default) if default else "Unavailable"), None)
            for index, device in sorted(enumerate(self.devices), key=lambda item: str(item[1]["name"]).casefold()):
                if device[f"max_{kind}_channels"] >= (1 if kind == "input" else 2):
                    combo.addItem(self.device_label(device), index)
                    combo.setItemData(combo.count() - 1,
                                      f"{device['name']}\nDevice ID: {index}\n"
                                      f"Default rate: {device['default_samplerate']:g} Hz",
                                      Qt.ItemDataRole.ToolTipRole)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
        self.input_channel, self.output_pair = QComboBox(), QComboBox()
        self.gain = QDoubleSpinBox()
        self.gain.setRange(0.0, 4.0)
        self.gain.setSingleStep(0.1)
        self.gain.setValue(engine.input_gain)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(0, 2000)
        self.offset.setDecimals(2)
        self.offset.setSuffix(" ms")
        self.offset.setValue(engine.recording_offset * 1000 / SAMPLE_RATE)
        self.offset.setToolTip("Round-trip delay: positive values align late input earlier on tape")
        form.addRow("Recording device", self.inputs)
        form.addRow("Mono input" if self.input_width == 1 else "Stereo inputs (L / R)", self.input_channel)
        form.addRow("Input gain", self.gain)
        form.addRow("Playback device", self.outputs)
        form.addRow("Stereo outputs (L / R)", self.output_pair)
        form.addRow("Sample rate", QLabel(f"{SAMPLE_RATE / 1000:g} kHz"))
        form.addRow("Recording offset", self.offset)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self.inputs.currentIndexChanged.connect(self.update_channels)
        self.outputs.currentIndexChanged.connect(self.update_channels)
        self.update_channels()
        self.input_channel.setCurrentIndex(max(0, self.input_channel.findData(engine.input_channel)))
        self.output_pair.setCurrentIndex(max(0, self.output_pair.findData(engine.output_channel)))

    def device_label(self, device) -> str:
        name = " ".join(str(device["name"]).split())
        api = self.hostapis[device["hostapi"]]["name"]
        return f"{name} [{api}] - {device['max_input_channels']} in / {device['max_output_channels']} out"

    def update_channels(self) -> None:
        for kind, devices, channels in (("input", self.inputs, self.input_channel),
                                        ("output", self.outputs, self.output_pair)):
            selected = channels.currentData()
            index = devices.currentData()
            device = self.defaults[kind] if index is None else self.devices[index]
            count = int(device[f"max_{kind}_channels"]) if device else 0
            channels.clear()
            if kind == "input":
                for channel in range(count - self.input_width + 1):
                    label = f"Input {channel + 1}" if self.input_width == 1 else f"Inputs {channel + 1} / {channel + 2}"
                    channels.addItem(label, channel)
            else:
                for channel in range(0, count - 1, 2):
                    channels.addItem(f"Outputs {channel + 1} / {channel + 2}", channel)
            channels.setCurrentIndex(max(0, channels.findData(selected)))
            channels.setEnabled(channels.count() > 0)
            devices.setToolTip(devices.currentText())
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.input_channel.count() > 0 and self.output_pair.count() > 0)

    def accept(self) -> None:
        input_channel, output_channel = self.input_channel.currentData(), self.output_pair.currentData()
        if input_channel is None or output_channel is None:
            return
        try:
            sd.check_input_settings(device=self.inputs.currentData(), channels=input_channel + self.input_width,
                                    dtype="float32", samplerate=SAMPLE_RATE)
            sd.check_output_settings(device=self.outputs.currentData(), channels=output_channel + 2,
                                     dtype="float32", samplerate=SAMPLE_RATE)
            stream = sd.Stream(device=(self.inputs.currentData(), self.outputs.currentData()),
                               channels=(input_channel + self.input_width, output_channel + 2),
                               dtype="float32", samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE)
            stream.close()
        except Exception as error:
            QMessageBox.warning(self, "Unsupported audio settings",
                                f"The selected devices could not be opened together at 44.1 kHz.\n\n{error}")
            return
        self.engine.input_device = self.inputs.currentData()
        self.engine.output_device = self.outputs.currentData()
        self.engine.input_channel = input_channel
        self.engine.output_channel = output_channel
        self.engine.input_gain = self.gain.value()
        self.engine.recording_offset = round(self.offset.value() * SAMPLE_RATE / 1000)
        super().accept()


class RenderJob(QThread):
    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation()
        except Exception as error:
            self.error = error


class LevelMeter(QWidget):
    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.setFixedSize(78, 34)
        self.setToolTip("Peak level in dBFS")

    def setValue(self, value):
        self.level = max(value / 100, self.level * 0.75)
        self.setAccessibleDescription(f"{20 * math.log10(max(self.level, 0.0001)):.1f} dBFS")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f1f3e9"))
        painter.setPen(QPen(QColor("#9ca79d"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setFont(QFont("DejaVu Sans Mono", 6))
        for horizontal, label in ((5, "-40"), (30, "-12"), (61, "0")):
            painter.setPen(QColor("#b43f46" if label == "0" else "#47594d"))
            painter.drawText(horizontal, 10, label)
            painter.drawLine(horizontal + 5, 12, horizontal + 5, 15)
        decibels = 20 * math.log10(max(self.level, 0.0001))
        horizontal = 8 + min(1, max(0, (decibels + 40) / 40)) * 60
        painter.setPen(QPen(QColor("#b43f46" if self.level >= 1 else "#26392e"), 2))
        painter.drawLine(39, 32, round(horizontal), 14)


class BounceDialog(QDialog):
    def __init__(self, window: "StudioWindow"):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Bounce tracks")
        self.resize(480, 450)
        layout = QVBoxLayout(self)
        self.sources = QListWidget()
        self.sources.setAccessibleName("Source tracks")
        self.sources.setToolTip("Combine full tracks with effects and volume; ignore pan, mute/solo and master level")
        for index, track in enumerate(window.song.tracks):
            item = QListWidgetItem(track_label(index, track.name), self.sources)
            item.setToolTip(item.text())
            item.setCheckState(Qt.CheckState.Unchecked)
        source_label = QLabel("Source tracks")
        source_label.setBuddy(self.sources)
        layout.addWidget(source_label)
        layout.addWidget(self.sources)
        self.destination = QComboBox()
        self.destination.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.destination.setMinimumContentsLength(20)
        self.destination.setAccessibleName("Destination track")
        self.destination.setToolTip("Replace the whole destination in its channel format; keep its name and mixer/effect settings")
        for index, track in enumerate(window.song.tracks):
            label = track_label(index, track.name)
            self.destination.addItem(label, index)
            self.destination.setItemData(index, label, Qt.ItemDataRole.ToolTipRole)
        self.destination.setCurrentIndex(next(
            (index for index, track in enumerate(window.song.tracks) if not len(track.audio)), 7,
        ))
        form = QFormLayout()
        form.addRow("Destination", self.destination)
        self.format_label = QLabel()
        form.addRow("Format", self.format_label)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.bounce_button = buttons.addButton("Bounce", QDialogButtonBox.ButtonRole.AcceptRole)
        self.bounce_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
        buttons.accepted.connect(self.perform_bounce)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.destination.currentIndexChanged.connect(self.update_sources)
        self.sources.itemChanged.connect(self.update_sources)
        self.update_sources()

    def selected_sources(self) -> list[int]:
        return [index for index in range(self.sources.count())
                if self.sources.item(index).checkState() == Qt.CheckState.Checked]

    def update_sources(self) -> None:
        destination = self.window.song.tracks[self.destination.currentData()]
        self.format_label.setText(("Mono" if destination.channels == 1 else "Stereo") + " / 44.1 kHz")
        self.sources.blockSignals(True)
        for index, track in enumerate(self.window.song.tracks):
            item = self.sources.item(index)
            enabled = bool(len(track.audio)) and index != self.destination.currentData()
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
                          | (Qt.ItemFlag.ItemIsEnabled if enabled else Qt.ItemFlag.NoItemFlags))
            if not enabled:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.sources.blockSignals(False)
        self.bounce_button.setEnabled(bool(self.selected_sources()))

    def perform_bounce(self) -> None:
        if self.window.bounce_tracks(self.selected_sources(), self.destination.currentData()):
            self.accept()


class BounceHistoryDialog(QDialog):
    def __init__(self, window: "StudioWindow"):
        super().__init__(window)
        self.setWindowTitle("Bounce history")
        self.resize(880, 420)
        layout = QVBoxLayout(self)
        history = window.song.bounce_history
        self.summary = QLabel(f"{len(history)} bounces" if history else "No bounces yet")
        layout.addWidget(self.summary)
        self.table = QTableWidget(len(history), 6)
        self.table.setAccessibleName("Bounce history")
        self.table.setHorizontalHeaderLabels(["When (local)", "Sources", "Destination", "Duration", "Format", "Status"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((160, 230, 180, 100, 80, 100)):
            self.table.setColumnWidth(column, width)
        for row, record in enumerate(reversed(history)):
            values = [
                datetime.fromisoformat(record.timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "\n".join(track_label(index, name) for index, name in zip(record.sources, record.source_names)),
                track_label(record.destination, record.destination_name), timecode(record.frames),
                "Mono" if record.channels == 1 else "Stereo",
                "Undone" if record.undone else "Completed",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()
        self.table.horizontalHeader().sectionResized.connect(lambda: self.table.resizeRowsToContents())
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class Waveform(QWidget):
    seek_requested = Signal(float)

    def __init__(self, color: str):
        super().__init__()
        self.color = color
        self.peaks = np.zeros(0)
        self.audio_frames = 0
        self.duration = SAMPLE_RATE * 10
        self.position = 0
        self.setFixedHeight(94)
        self.setMinimumWidth(96)
        self.setToolTip("Click to move the playhead while stopped")
        self.setAccessibleName("Track waveform")

    def set_audio(self, audio: np.ndarray) -> None:
        self.audio_frames = len(audio)
        if len(audio):
            step = max(1, int(np.ceil(len(audio) / 256)))
            padding = (0, (-len(audio)) % step)
            if audio.ndim == 2:
                padded = np.pad(audio, (padding, (0, 0)))
                self.peaks = np.max(np.abs(padded.reshape(-1, step, 2)), axis=1)
            else:
                padded = np.pad(audio, padding)
                self.peaks = np.max(np.abs(padded.reshape(-1, step)), axis=1)
        else:
            self.peaks = np.zeros(0)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#192721"))
        painter.setPen(QPen(QColor("#334a3d"), 1))
        for division in range(1, 4):
            horizontal = int(self.width() * division / 4)
            painter.drawLine(horizontal, 0, horizontal, self.height())
        extent = self.width() * self.audio_frames / max(1, self.duration)
        lanes = self.peaks.T if self.peaks.ndim == 2 else [self.peaks]
        lane_height = self.height() / len(lanes)
        for channel, peaks in enumerate(lanes):
            center = (channel + 0.5) * lane_height
            painter.setPen(QPen(QColor("#334a3d"), 1))
            painter.drawLine(0, int(center), self.width(), int(center))
            painter.setPen(QPen(QColor(self.color).lighter(145), 1))
            for index, peak in enumerate(peaks):
                horizontal = int(index / max(1, len(peaks)) * extent)
                amplitude = min(float(peak), 1) * (lane_height / 2 - 8)
                painter.drawLine(horizontal, int(center - amplitude), horizontal, int(center + amplitude))
        painter.setPen(QPen(QColor("#f0d879"), 2))
        playhead = min(self.width() - 1, int(self.position / max(1, self.duration) * self.width()))
        painter.drawLine(playhead, 0, playhead, self.height())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_requested.emit(event.position().x() / max(1, self.width()))


class ChannelStrip(QWidget):
    def __init__(self, window: "StudioWindow", index: int):
        super().__init__()
        self.window = window
        self.index = index
        self.setMinimumWidth(116)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(6)
        number = QLabel(f"{index + 1:02d}")
        number.setStyleSheet(f"color: {COLORS[index]}; font-size: 23px; font-weight: bold; border-bottom: 3px solid {COLORS[index]}; padding-bottom: 6px;")
        name_heading = QHBoxLayout()
        name_heading.addWidget(number)
        name_label = QLabel("Name")
        name_heading.addWidget(name_label)
        layout.addLayout(name_heading)
        self.name = QLineEdit()
        self.name.setMaxLength(64)
        self.name.setPlaceholderText("Track name")
        name_label.setBuddy(self.name)
        self.name.setAccessibleName(f"Track {index + 1} name")
        self.name.editingFinished.connect(self.rename)
        layout.addWidget(self.name)
        self.waveform = Waveform(COLORS[index])
        self.waveform.seek_requested.connect(window.seek_waveform)
        layout.addWidget(self.waveform)
        self.length_label = QLabel("00:00.000")
        self.length_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.length_label)
        self.arm = QCheckBox("REC ARM")
        self.arm.setStyleSheet("QCheckBox { color: #a6383c; font-weight: bold; }")
        self.arm.setToolTip("Select this track for recording; existing audio in the take region is replaced")
        self.arm.toggled.connect(lambda checked: window.arm_track(index, checked))
        layout.addWidget(self.arm)
        toggles = QHBoxLayout()
        self.mute = QCheckBox("M")
        self.mute.setToolTip("Mute track")
        self.solo = QCheckBox("S")
        self.solo.setToolTip("Solo track")
        self.mute.toggled.connect(lambda value: self.change("muted", value))
        self.solo.toggled.connect(lambda value: self.change("solo", value))
        toggles.addWidget(self.mute)
        toggles.addWidget(self.solo)
        layout.addLayout(toggles)
        self.effects_button = QPushButton("Effects (0)")
        self.effects_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.effects_button.setToolTip("Edit this track's effects rack while stopped")
        self.effects_button.clicked.connect(lambda: window.edit_effects(index))
        layout.addWidget(self.effects_button)
        self.pan_label = QLabel("PAN  C")
        self.pan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pan_label)
        self.pan = QSlider(Qt.Orientation.Horizontal)
        self.pan.setRange(-100, 100)
        self.pan.setToolTip("Stereo pan: left / center / right")
        self.pan.setAccessibleName(f"Track {index + 1} pan")
        self.pan.valueChanged.connect(self.set_pan)
        layout.addWidget(self.pan)
        self.volume = QSlider(Qt.Orientation.Vertical)
        self.volume.setRange(0, 100)
        self.volume.setMinimumHeight(115)
        self.volume.setToolTip("Track playback volume")
        self.volume.setAccessibleName(f"Track {index + 1} volume")
        self.volume.valueChanged.connect(self.set_volume)
        layout.addWidget(self.volume, 1, Qt.AlignmentFlag.AlignHCenter)
        self.volume_label = QLabel("80%")
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.volume_label)
        commands = QHBoxLayout()
        self.import_button = tool_button(self, QStyle.StandardPixmap.SP_DialogOpenButton, "Import audio at playhead")
        self.clear_button = tool_button(self, QStyle.StandardPixmap.SP_TrashIcon, "Erase track")
        self.import_button.clicked.connect(lambda: window.import_track(index))
        self.clear_button.clicked.connect(lambda: window.clear_track(index))
        commands.addWidget(self.import_button)
        commands.addWidget(self.clear_button)
        self.tools_button = tool_button(self, QStyle.StandardPixmap.SP_FileDialogContentsView, "Tape edits, alternate takes and channel EQ")
        self.tools_button.clicked.connect(lambda: window.track_tools(index))
        commands.addWidget(self.tools_button)
        for button in (self.import_button, self.clear_button, self.tools_button):
            button.setFixedWidth(28)
        layout.addLayout(commands)
        self.refresh()

    @property
    def track(self) -> Track:
        return self.window.song.tracks[self.index]

    def change(self, attribute: str, value) -> None:
        setattr(self.track, attribute, value)
        self.window.mark_dirty()

    def rename(self) -> None:
        name = self.name.text().strip() or f"Track {self.index + 1}"
        if name != self.track.name:
            self.change("name", name)
        self.name.setText(name)
        self.name.setCursorPosition(0)
        self.name.setToolTip(f"Rename {track_label(self.index, name)}")
        self.window.update_controls()

    def set_pan(self, value: int) -> None:
        self.change("pan", value / 100)
        self.update_pan_label(value)

    def update_pan_label(self, value: int) -> None:
        label = "BAL" if self.track.channels == 2 else "PAN"
        self.pan_label.setText(f"{label}  C" if value == 0 else f"{label}  {'L' if value < 0 else 'R'} {abs(value)}")
        self.pan.setToolTip("Stereo balance: left / center / right" if self.track.channels == 2 else "Stereo pan: left / center / right")

    def set_volume(self, value: int) -> None:
        self.change("volume", value / 100)
        self.volume_label.setText(f"{value}%")

    def refresh(self) -> None:
        for widget, value in [(self.mute, self.track.muted), (self.solo, self.track.solo),
                              (self.pan, round(self.track.pan * 100)), (self.volume, round(self.track.volume * 100))]:
            widget.blockSignals(True)
            if isinstance(widget, QCheckBox):
                widget.setChecked(value)
            else:
                widget.setValue(value)
            widget.blockSignals(False)
        self.name.setText(self.track.name)
        self.name.setCursorPosition(0)
        self.name.setToolTip(f"Rename {track_label(self.index, self.track.name)}")
        pan = round(self.track.pan * 100)
        self.update_pan_label(pan)
        self.volume_label.setText(f"{round(self.track.volume * 100)}%")
        self.waveform.set_audio(self.track.audio)
        self.length_label.setText(timecode(len(self.track.audio)) + (" ST" if self.track.channels == 2 else ""))
        unloaded = any(effect.enabled and effect.processor is None for effect in self.track.effects)
        self.effects_button.setText(f"Effects ({len(self.track.effects)})")
        self.effects_button.setStyleSheet("color: #a6383c;" if unloaded else "")

    def set_running(self, running: bool) -> None:
        for widget in (self.arm, self.import_button, self.clear_button, self.name, self.effects_button, self.tools_button):
            widget.setEnabled(not running)


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.song = Song()
        self.engine = AudioEngine(self.song)
        self.path: Path | None = None
        self.dirty = False
        self.armed: int | None = None
        self.undo_audio: tuple[int, np.ndarray] | None = None
        self.undo_bounce: BounceRecord | None = None
        self.undo_takes: tuple[str, list[Take]] | None = None
        self._undo_stack = []
        self._redo_stack = []
        self.busy = False
        self._job = None
        self._job_callback = None
        self._job_title = ""
        self.settings = QSettings("8T", "8T")
        if not self.settings.contains("recording_offset"):
            legacy_settings = QSettings("Portastudio", "Portastudio08")
            self.settings.setValue("recording_offset", legacy_settings.value("recording_offset", 0, type=int))
        self.engine.recording_offset = max(0, min(SAMPLE_RATE * 2, self.settings.value("recording_offset", 0, type=int)))
        recovery_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "recovery"
        self.recovery_path = recovery_root / f"{os.getpid()}-{uuid4().hex}.8t"
        self.recovered_path = None
        self.duration = SAMPLE_RATE * 10
        self.resize(1200, 800)
        self.setMinimumSize(680, 650)
        self.setStyleSheet(STYLE)
        self._build_menu()
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 12, 20, 10)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("8T: 8 Track DAW")
        title.setStyleSheet("font-size: 25px; font-weight: bold;")
        title.setToolTip("44.1 kHz / eight mono or stereo tracks")
        heading.addWidget(title)
        heading.addStretch()
        self.view_tabs = QTabBar()
        self.view_tabs.addTab("Mixer")
        self.view_tabs.addTab("Songwriting")
        self.view_tabs.setAccessibleName("Workspace view")
        self.view_tabs.setDrawBase(False)
        heading.addWidget(self.view_tabs)
        layout.addLayout(heading)
        transport = QHBoxLayout()
        self.rewind_button = tool_button(self, QStyle.StandardPixmap.SP_MediaSkipBackward, "Return to start (Home)")
        self.play_button = tool_button(self, QStyle.StandardPixmap.SP_MediaPlay, "Play / stop (Space)")
        self.stop_button = tool_button(self, QStyle.StandardPixmap.SP_MediaStop, "Stop")
        self.record_button = QPushButton("Record")
        self.record_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton))
        self.record_button.setStyleSheet("QPushButton { background: #b43f46; color: white; border-color: #96363c; } QPushButton:disabled { background: #cda5a6; }")
        self.record_button.setToolTip("Record onto the armed track (R)")
        self.rewind_button.clicked.connect(self.rewind)
        self.play_button.clicked.connect(self.play)
        self.stop_button.clicked.connect(self.stop)
        self.record_button.clicked.connect(self.record)
        for button in (self.rewind_button, self.play_button, self.stop_button, self.record_button):
            transport.addWidget(button)
        transport.addStretch()
        self.clock = QLabel("00:00.000")
        self.clock.setFont(QFont("DejaVu Sans Mono", 23))
        self.clock.setFixedWidth(205)
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock.setStyleSheet("background: #192721; color: #d1edb6; padding: 7px; border-radius: 3px; font-family: 'DejaVu Sans Mono'; font-size: 23px;")
        transport.addWidget(self.clock)
        self.state_label = QLabel("STOPPED")
        self.state_label.setFixedWidth(90)
        transport.addWidget(self.state_label)
        layout.addLayout(transport)
        timeline = QHBoxLayout()
        timeline.addWidget(QLabel("TAPE"))
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 10000)
        self.seek_slider.setAccessibleName("Song playhead")
        self.seek_slider.sliderMoved.connect(lambda value: self.seek_fraction(value / 10000))
        self.seek_slider.valueChanged.connect(self.seek_value)
        timeline.addWidget(self.seek_slider, 1)
        self.duration_label = QLabel("00:10.000")
        timeline.addWidget(self.duration_label)
        self.follow_button = QPushButton("Follow")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
        self.follow_button.setToolTip("Keep waveform heads synchronized with the transport; turn off to position them independently")
        self.follow_button.setAccessibleName("Follow transport")
        self.follow_button.setStyleSheet("QPushButton:checked { background: #496752; color: white; }")
        self.follow_button.toggled.connect(self.update_follow)
        layout.addLayout(timeline)
        from eighttrack.tape_ui import seconds_control

        locations = QHBoxLayout()
        self.marker_controls = []
        for key, label in (("marker_a", "A"), ("marker_b", "B")):
            locations.addWidget(QLabel(label))
            control = seconds_control()
            control.setAccessibleName(f"Marker {label}")
            control.valueChanged.connect(lambda value, attribute=key: self.set_tape_value(attribute, round(value * SAMPLE_RATE)))
            locations.addWidget(control)
            store = tool_button(self, QStyle.StandardPixmap.SP_ArrowDown, f"Set {label} at playhead")
            store.setFixedWidth(28)
            store.clicked.connect(lambda checked=False, attribute=key: self.set_marker_here(attribute))
            locations.addWidget(store)
            self.marker_controls.append((key, control, store))
        self.loop = QCheckBox("Loop")
        self.punch = QCheckBox("Punch")
        self.loop.toggled.connect(lambda value: self.set_tape_value("loop", value))
        self.punch.toggled.connect(lambda value: self.set_tape_value("punch", value))
        locations.addWidget(self.loop)
        locations.addWidget(self.punch)
        locations.addWidget(self.follow_button)
        locations.addStretch()
        self.tape_button = tool_button(self, QStyle.StandardPixmap.SP_FileDialogDetailedView, "Count-in, tape speed, locations and shared effects")
        self.tape_button.clicked.connect(self.tape_settings)
        locations.addWidget(self.tape_button)
        layout.addLayout(locations)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        mixer = QWidget()
        mixer_layout = QHBoxLayout(mixer)
        mixer_layout.setContentsMargins(0, 0, 0, 0)
        mixer_layout.setSpacing(0)
        self.strips = [ChannelStrip(self, index) for index in range(8)]
        for strip in self.strips:
            mixer_layout.addWidget(strip, 1)
        scroll.setWidget(mixer)
        self.workspace_tabs = QStackedWidget()
        self.workspace_tabs.addWidget(scroll)
        self.lyrics_editor = QPlainTextEdit()
        self.lyrics_editor.setAccessibleName("Songwriting: lyrics, chords and notes")
        self.lyrics_editor.setStyleSheet("QPlainTextEdit { background: #f9faf8; color: #232927; border: none; padding: 12px; font-family: 'DejaVu Sans Mono'; font-size: 14px; }")
        self.lyrics_editor.textChanged.connect(self.update_lyrics)
        self.workspace_tabs.addWidget(self.lyrics_editor)
        self.view_tabs.currentChanged.connect(self.workspace_tabs.setCurrentIndex)
        self.workspace_tabs.currentChanged.connect(self.view_tabs.setCurrentIndex)
        layout.addWidget(self.workspace_tabs, 1)
        bottom = QHBoxLayout()
        self.click = QCheckBox("Click")
        self.click.setToolTip("Playback-only metronome; not included in recordings or exports")
        self.click.toggled.connect(lambda value: setattr(self.engine, "metronome", value))
        bottom.addWidget(self.click)
        self.tempo = QSpinBox()
        self.tempo.setRange(40, 240)
        self.tempo.setValue(100)
        self.tempo.setSuffix(" BPM")
        self.tempo.valueChanged.connect(self.set_tempo)
        bottom.addWidget(self.tempo)
        bottom.addSpacing(12)
        bottom.addWidget(QLabel("MASTER"))
        self.master = QSlider(Qt.Orientation.Horizontal)
        self.master.setRange(0, 100)
        self.master.setValue(80)
        self.master.setMaximumWidth(130)
        self.master.setAccessibleName("Master output volume")
        self.master.valueChanged.connect(self.set_master)
        bottom.addWidget(self.master)
        bottom.addStretch()
        bottom.addWidget(QLabel("IN"))
        self.input_meter = self._meter()
        bottom.addWidget(self.input_meter)
        bottom.addWidget(QLabel("OUT"))
        self.output_meter = self._meter()
        bottom.addWidget(self.output_meter)
        self.clip_label = QLabel("CLIP")
        self.clip_label.setStyleSheet("color: #89948c;")
        bottom.addWidget(self.clip_label)
        layout.addLayout(bottom)
        self._build_docks()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)
        self.recovery_timer = QTimer(self)
        self.recovery_timer.setInterval(30000)
        self.recovery_timer.timeout.connect(self.autosave)
        self.recovery_timer.start()
        self.refresh()
        self.statusBar().showMessage("Ready")

    def _meter(self) -> LevelMeter:
        return LevelMeter()

    def _build_docks(self) -> None:
        from eighttrack.effects_ui import EffectsPanel

        self.effects_dock = QDockWidget("Effects", self)
        self.effects_dock.setObjectName("effects_dock")
        self.effects_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.effects_panel = EffectsPanel(self, 0)
        self.effects_dock.setWidget(self.effects_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.effects_dock)
        self.resizeDocks([self.effects_dock], [240], Qt.Orientation.Vertical)
        docks_menu = self.menuBar().addMenu("Docks")
        docks_menu.addAction(self.effects_dock.toggleViewAction())
        self.float_effects_action = QAction("Float effects", self)
        self.float_effects_action.setCheckable(True)
        self.float_effects_action.toggled.connect(self.effects_dock.setFloating)
        self.effects_dock.topLevelChanged.connect(self.float_effects_action.setChecked)
        docks_menu.addAction(self.float_effects_action)
        docks_menu.addSeparator()
        self.lock_docks_action = QAction("Lock dock positions", self)
        self.lock_docks_action.setCheckable(True)
        self.lock_docks_action.toggled.connect(self.lock_docks)
        docks_menu.addAction(self.lock_docks_action)
        docks_menu.addAction("Reset dock layout", self.reset_docks)
        state = self.settings.value("docks/state")
        if state is not None:
            self.restoreState(state, 1)
        self.lock_docks_action.setChecked(self.settings.value("docks/locked", False, type=bool))

    def lock_docks(self, locked: bool) -> None:
        features = QDockWidget.DockWidgetFeature.DockWidgetClosable
        if not locked:
            features |= QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        self.effects_dock.setFeatures(features)
        self.float_effects_action.setEnabled(not locked)

    def reset_docks(self) -> None:
        self.lock_docks_action.setChecked(False)
        self.effects_dock.setFloating(False)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.effects_dock)
        self.effects_dock.show()
        self.resizeDocks([self.effects_dock], [240], Qt.Orientation.Vertical)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        self.stopped_actions = []
        for title, shortcut, callback in [
            ("New song", "Ctrl+N", self.new_song), ("Open song...", "Ctrl+O", self.open_song),
            ("Save song", "Ctrl+S", self.save), ("Save song as...", "Ctrl+Shift+S", self.save_as),
            ("Export stereo WAV...", "Ctrl+E", self.export),
            ("Recover autosave...", "", self.recover_autosave),
        ]:
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
            self.stopped_actions.append(action)
        edit_menu = self.menuBar().addMenu("Edit")
        self.undo_action = QAction("Undo last audio edit", self)
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.triggered.connect(self.undo)
        edit_menu.addAction(self.undo_action)
        self.redo_action = QAction("Redo audio edit", self)
        self.redo_action.setShortcut("Ctrl+Shift+Z")
        self.redo_action.triggered.connect(self.redo)
        edit_menu.addAction(self.redo_action)
        tape_menu = self.menuBar().addMenu("Tape")
        for title, shortcut, callback in [
            ("Tape settings and locations...", "", self.tape_settings),
            ("Locate A", "[", lambda: self.locate_marker("marker_a")),
            ("Locate B", "]", lambda: self.locate_marker("marker_b")),
        ]:
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            tape_menu.addAction(action)
            self.stopped_actions.append(action)
        tracks_menu = self.menuBar().addMenu("Tracks")
        self.bounce_action = QAction("Bounce tracks...", self)
        self.bounce_action.triggered.connect(self.show_bounce)
        tracks_menu.addAction(self.bounce_action)
        self.stopped_actions.append(self.bounce_action)
        self.history_action = QAction("Bounce history...", self)
        self.history_action.triggered.connect(self.show_bounce_history)
        tracks_menu.addAction(self.history_action)
        audio_menu = self.menuBar().addMenu("Audio")
        devices = QAction("Audio devices...", self)
        devices.triggered.connect(self.audio_devices)
        audio_menu.addAction(devices)
        self.stopped_actions.append(devices)
        calibrate = QAction("Calibrate recording offset...", self)
        calibrate.triggered.connect(self.calibrate_latency)
        audio_menu.addAction(calibrate)
        self.stopped_actions.append(calibrate)
        for shortcut, callback in [("Space", self.toggle_play), ("Home", self.rewind), ("R", self.record)]:
            action = QAction(self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            self.addAction(action)

    def mark_dirty(self) -> None:
        self.dirty = True
        self.update_title()

    def update_lyrics(self) -> None:
        self.song.lyrics = self.lyrics_editor.toPlainText()
        self.mark_dirty()

    def run_job(self, operation, callback, title) -> None:
        if self.busy or self.engine.running:
            return
        self.busy = True
        self._job_callback, self._job_title = callback, title
        self._job = RenderJob(operation, self)
        self._job.finished.connect(self.finish_job)
        self.centralWidget().setEnabled(False)
        self.update_controls()
        self.state_label.setText("WORKING")
        self.statusBar().showMessage(title)
        self._job.start()

    def finish_job(self) -> None:
        job, callback, title = self._job, self._job_callback, self._job_title
        self._job = self._job_callback = None
        self.busy = False
        self.centralWidget().setEnabled(True)
        try:
            if job.error is not None:
                self.show_error(title, job.error)
            elif callback is not None:
                callback(job.result)
        except Exception as error:
            self.show_error(title, error)
        finally:
            job.deleteLater()
            self.update_controls()

    def autosave(self) -> None:
        if not self.dirty or self.busy or self.engine.running or QApplication.activeModalWidget() is not None:
            return
        try:
            self.recovery_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.statusBar().showMessage(f"Recovery unavailable: {error}")
            return
        self.run_job(lambda: save_song(self.song, self.recovery_path),
                     lambda result: self.statusBar().showMessage("Recovery snapshot saved"), "Saving recovery snapshot")

    def clear_recovery(self) -> None:
        for path in (self.recovery_path, self.recovered_path):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self.recovered_path = None

    def recover_autosave(self) -> None:
        if self.engine.running or self.busy or not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Recover autosave", str(self.recovery_path.parent), "8T song (*.8t *.porta)")
        if path:
            self.restore_recovery(Path(path))

    def restore_recovery(self, path) -> None:
        try:
            song = load_song(path)
            if path == self.recovery_path:
                self.recovery_path = path.parent / f"{os.getpid()}-{uuid4().hex}.8t"
            if path == self.recovered_path:
                self.recovered_path = None
            self.replace_song(song, None)
            self.recovered_path = path
            self.mark_dirty()
            self.statusBar().showMessage("Recovered song; save to keep it")
        except Exception as error:
            self.show_error("Recovery failed", error)

    def offer_recovery(self) -> None:
        candidates = []
        legacy_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / "Portastudio" / "recovery"
        roots = {self.recovery_path.parent, legacy_root}
        for path in (path for root in roots for pattern in ("*.8t", "*.porta") for path in root.glob(pattern)):
            try:
                process = int(path.name.split("-", 1)[0])
                os.kill(process, 0)
            except ProcessLookupError:
                candidates.append(path)
            except (ValueError, PermissionError):
                continue
        if candidates:
            newest = max(candidates, key=lambda path: path.stat().st_mtime)
            answer = QMessageBox.question(self, "Recover interrupted session", "An interrupted session has a recovery snapshot. Restore it?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Yes:
                self.restore_recovery(newest)

    def set_tape_value(self, key, value) -> None:
        previous = getattr(self.song, key)
        setattr(self.song, key, value)
        if self.song.loop or self.song.punch:
            try:
                self.song.tape_range()
            except ValueError as error:
                setattr(self.song, key, previous)
                self.show_error("Invalid tape range", error)
                self.refresh()
                return
        self.mark_dirty()

    def set_marker_here(self, key) -> None:
        self.set_tape_value(key, min(MAX_FRAMES, self.engine.position))
        self.refresh()

    def locate_marker(self, key) -> None:
        if not self.engine.running and not self.busy:
            self.engine.seek(getattr(self.song, key))
            self.tick()

    def tape_settings(self) -> None:
        if self.engine.running or self.busy:
            return
        from eighttrack.tape_ui import TapeSettingsDialog

        dialog = TapeSettingsDialog(self)
        dialog.exec()
        dialog.deleteLater()
        self.refresh()

    def track_tools(self, index) -> None:
        if self.engine.running or self.busy:
            return
        from eighttrack.tape_ui import TrackToolsDialog

        dialog = TrackToolsDialog(self, index)
        dialog.exec()
        dialog.deleteLater()
        self.refresh()

    def apply_track_edit(self, index, operation, takes=False) -> bool:
        if self.engine.running or self.busy:
            return False
        track = self.song.tracks[index]
        previous = track.audio.copy()
        bank = snapshot_takes(track) if takes else None
        try:
            operation()
        except Exception as error:
            self.show_error("Tape edit failed", error)
            return False
        self.push_undo((index, previous), takes=bank)
        self.mark_dirty()
        self.refresh()
        return True

    def push_undo(self, audio, bounce=None, takes=None, clear_redo=True) -> None:
        if self.undo_audio is not None:
            self._undo_stack.append((self.undo_audio, self.undo_bounce, self.undo_takes))
        self.undo_audio, self.undo_bounce, self.undo_takes = audio, bounce, takes
        if clear_redo:
            self._redo_stack.clear()
        def size(entry):
            return entry[0][1].nbytes + sum(take.audio.nbytes for take in (entry[2][1] if entry[2] else []))
        current = (audio, bounce, takes)
        while self._undo_stack and (len(self._undo_stack) >= 32
                or size(current) + sum(size(entry) for entry in self._undo_stack) > 128 * 1024 * 1024):
            self._undo_stack.pop(0)

    def update_title(self) -> None:
        name = self.path.stem if self.path else "Untitled"
        self.setWindowTitle(f"{'* ' if self.dirty else ''}{name} | 8T: 8 Track DAW")

    def set_master(self, value: int) -> None:
        self.song.master = value / 100
        self.mark_dirty()

    def set_tempo(self, value: int) -> None:
        self.song.bpm = value
        self.mark_dirty()

    def arm_track(self, index: int, checked: bool) -> None:
        self.armed = index if checked else None
        for strip in self.strips:
            strip.arm.blockSignals(True)
            strip.arm.setChecked(strip.index == self.armed)
            strip.arm.blockSignals(False)
        self.update_controls()

    def seek_value(self, value: int) -> None:
        if not self.engine.running:
            self.seek_fraction(value / 10000)

    def seek_fraction(self, fraction: float) -> None:
        if not self.engine.running and not self.busy:
            self.engine.seek(int(fraction * self.duration))
            self.tick()

    def seek_waveform(self, fraction: float) -> None:
        if self.follow_button.isChecked():
            self.seek_fraction(fraction)
        elif not self.busy:
            position = int(max(0.0, min(1.0, fraction)) * self.duration)
            for strip in self.strips:
                strip.waveform.position = position
                strip.waveform.update()

    def update_follow(self, checked: bool) -> None:
        for strip in self.strips:
            strip.waveform.setToolTip(
                "Click to move the playhead while stopped" if checked
                else "Click to position waveform heads without moving the transport"
            )
        self.tick()

    def rewind(self) -> None:
        if self.busy:
            return
        self.stop()
        self.engine.seek(0)
        self.tick()

    def toggle_play(self) -> None:
        if isinstance(QApplication.focusWidget(), (QLineEdit, QSpinBox, QDoubleSpinBox)):
            return
        self.stop() if self.engine.running else self.play()

    def play(self) -> None:
        self.start_transport(None)

    def record(self) -> None:
        if self.engine.running or isinstance(QApplication.focusWidget(), QLineEdit):
            return
        if self.armed is None:
            self.statusBar().showMessage("Select REC ARM on a track first.")
            return
        self.start_transport(self.armed)

    def start_transport(self, track: int | None) -> None:
        if self.engine.running or self.busy:
            return
        if self.song.tape_speed > 1 or any(channel.has_effects or channel.reverb_send or channel.delay_send for channel in self.song.tracks):
            exclude = None if self.song.punch else track
            self.run_job(lambda: self.song.prepare_effects(exclude=exclude),
                         lambda result: self.begin_transport(track), "Preparing effects")
        else:
            self.begin_transport(track)

    def begin_transport(self, track) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.engine.start(track)
            self.statusBar().showMessage(
                f"Recording: {track_label(track, self.song.tracks[track].name)}" if track is not None else "Playing"
            )
        except Exception as error:
            self.show_error("Could not start audio", error)
        finally:
            QApplication.restoreOverrideCursor()
        self.update_controls()

    def stop(self) -> None:
        if self.busy:
            return
        undo = self.engine.stop()
        if undo is not None:
            self.push_undo(undo)
            self.mark_dirty()
        self.refresh()

    def update_controls(self) -> None:
        running = self.engine.running or self.busy
        self.effects_panel.setEnabled(not running)
        self.effects_panel.refresh_tracks()
        self.effects_dock.setToolTip("Stop transport to edit effects" if running else "")
        self.play_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.record_button.setEnabled(not running and self.armed is not None)
        self.record_button.setToolTip(
            f"Record onto {track_label(self.armed, self.song.tracks[self.armed].name)} (R)"
            if self.armed is not None else "Record onto the armed track (R)"
        )
        self.seek_slider.setEnabled(not running)
        self.tempo.setEnabled(not running)
        self.undo_action.setEnabled(not running and self.undo_audio is not None)
        self.redo_action.setEnabled(not running and bool(self._redo_stack))
        for key, control, store in self.marker_controls:
            control.setEnabled(not running)
            store.setEnabled(not running)
        for widget in (self.loop, self.punch, self.tape_button):
            widget.setEnabled(not running)
        for action in self.stopped_actions:
            action.setEnabled(not running)
        self.history_action.setEnabled(not self.busy)
        for strip in self.strips:
            strip.set_running(running)
        self.state_label.setText(self.engine.mode.upper())
        self.state_label.setStyleSheet("color: #b43f46; font-weight: bold;" if self.engine.mode == "recording" else "color: #496752;")

    def refresh(self) -> None:
        limit = max(MAX_FRAMES, self.song.playback_length)
        self.duration = min(limit, max(SAMPLE_RATE * 10, self.song.playback_length + SAMPLE_RATE * 5, self.engine.position + SAMPLE_RATE))
        for strip in self.strips:
            strip.refresh()
        self.master.blockSignals(True)
        self.master.setValue(round(self.song.master * 100))
        self.master.blockSignals(False)
        self.tempo.blockSignals(True)
        self.tempo.setValue(self.song.bpm)
        self.tempo.blockSignals(False)
        for key, control, store in self.marker_controls:
            control.blockSignals(True)
            control.setValue(getattr(self.song, key) / SAMPLE_RATE)
            control.blockSignals(False)
        for control, value in ((self.loop, self.song.loop), (self.punch, self.song.punch)):
            control.blockSignals(True)
            control.setChecked(value)
            control.blockSignals(False)
        self.update_title()
        self.update_controls()
        self.tick()

    def tick(self) -> None:
        if self.engine.finished:
            self.stop()
            self.statusBar().showMessage(self.engine.warning or "Stopped at end of tape")
        if self.engine.position > self.duration:
            self.duration = min(max(MAX_FRAMES, self.song.playback_length), self.engine.position + SAMPLE_RATE * 5)
        self.clock.setText(timecode(self.engine.position))
        if not self.busy:
            if self.engine.count_remaining:
                beats = math.ceil(self.engine.count_remaining / (SAMPLE_RATE * 60 / self.song.bpm))
                self.state_label.setText(f"COUNT {beats}")
            elif self.engine.mode == "recording" and self.engine.position < self.engine.record_start:
                self.state_label.setText("PRE-ROLL")
            else:
                self.state_label.setText(self.engine.mode.upper())
        self.duration_label.setText(timecode(self.duration))
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(round(self.engine.position / self.duration * 10000))
        self.seek_slider.blockSignals(False)
        for strip in self.strips:
            if self.follow_button.isChecked():
                strip.waveform.position = self.engine.position
            strip.waveform.duration = self.duration
            strip.waveform.update()
        self.input_meter.setValue(min(100, int(self.engine.input_peak * 100)))
        self.output_meter.setValue(min(100, int(self.engine.output_peak * 100)))
        self.clip_label.setStyleSheet("color: #b43f46; font-weight: bold;" if self.engine.clipped else "color: #89948c;")
        if self.engine.warning:
            self.statusBar().showMessage(self.engine.warning)

    def show_error(self, title: str, error: Exception) -> None:
        QMessageBox.warning(self, title, str(error))

    def show_bounce(self) -> None:
        if self.engine.running:
            return
        dialog = BounceDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def show_bounce_history(self) -> None:
        dialog = BounceHistoryDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def bounce_tracks(self, sources: list[int], destination: int) -> bool:
        if self.engine.running or self.busy:
            return False
        try:
            if not 0 <= destination < len(self.song.tracks):
                raise ValueError("Choose a destination track.")
            track = self.song.tracks[destination]
            if len(track.audio):
                answer = QMessageBox.question(
                    self, "Replace destination audio",
                    f"Replace all audio on {track_label(destination, track.name)}? Source tracks will be kept.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
            previous = track.audio.copy()
            if any(self.song.tracks[index].has_effects for index in sources):
                self.run_job(lambda: self.song.bounce(sources, destination),
                             lambda record: self.finish_bounce(record, previous), "Bouncing tracks")
                return True
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                record = self.song.bounce(sources, destination)
            finally:
                QApplication.restoreOverrideCursor()
            self.finish_bounce(record, previous)
            return True
        except Exception as error:
            self.show_error("Bounce failed", error)
            return False

    def finish_bounce(self, record, previous) -> None:
        destination = record.destination
        track = self.song.tracks[destination]
        self.push_undo((destination, previous), bounce=record)
        self.mark_dirty()
        self.refresh()
        message = f"Bounced to {track_label(destination, track.name)}"
        if np.any(np.abs(track.audio) > 1):
            message += " - above full scale; lower destination volume before playback"
        self.statusBar().showMessage(message)

    def edit_effects(self, index: int) -> None:
        if self.engine.running or self.busy:
            return
        self.effects_panel.set_track(index)
        self.effects_dock.show()
        self.effects_dock.raise_()

    def import_track(self, index: int) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import audio at playhead", "", "Audio (*.wav *.flac *.aiff *.aif *.ogg);;All files (*)")
        if not path:
            return
        try:
            track = self.song.tracks[index]
            audio = import_audio(path, channels=track.channels)
            previous = track.audio.copy()
            track.write(self.engine.position, audio)
            self.push_undo((index, previous))
            self.mark_dirty()
            self.refresh()
            self.statusBar().showMessage(f"Imported {Path(path).name}")
        except Exception as error:
            self.show_error("Import failed", error)

    def clear_track(self, index: int) -> None:
        track = self.song.tracks[index]
        if not len(track.audio):
            return
        answer = QMessageBox.question(self, "Erase track", f"Erase all audio on {track.name}?", defaultButton=QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.push_undo((index, track.audio.copy()))
            track.audio = track.audio[:0].copy()
            track.invalidate_audio()
            self.mark_dirty()
            self.refresh()

    def undo(self) -> None:
        if self.undo_audio is not None and not self.engine.running and not self.busy:
            index, previous = self.undo_audio
            track = self.song.tracks[index]
            bank = snapshot_takes(track) if self.undo_takes is not None else None
            self._redo_stack.append(((index, track.audio.copy()), self.undo_bounce, bank))
            track.audio = previous
            track.invalidate_audio()
            if self.undo_takes is not None:
                track.active_take_name, track.takes = self.undo_takes
            if self.undo_bounce is not None:
                self.undo_bounce.undone = True
            self.undo_audio, self.undo_bounce, self.undo_takes = self._undo_stack.pop() if self._undo_stack else (None, None, None)
            self.mark_dirty()
            self.refresh()
            self.statusBar().showMessage("Last audio edit undone")

    def redo(self) -> None:
        if self._redo_stack and not self.engine.running and not self.busy:
            (index, audio), bounce, takes = self._redo_stack.pop()
            track = self.song.tracks[index]
            bank = snapshot_takes(track) if takes is not None else None
            self.push_undo((index, track.audio.copy()), bounce, bank, clear_redo=False)
            track.audio = audio
            track.invalidate_audio()
            if takes is not None:
                track.active_take_name, track.takes = takes
            if bounce is not None:
                bounce.undone = False
            self.mark_dirty()
            self.refresh()

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved song", "Save changes to this song?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def replace_song(self, song: Song, path: Path | None) -> None:
        self.clear_recovery()
        self.song = song
        self.engine.song = song
        self.engine.seek(0)
        self.path = path
        self.dirty = False
        self.lyrics_editor.blockSignals(True)
        self.lyrics_editor.setPlainText(song.lyrics)
        self.lyrics_editor.blockSignals(False)
        self.undo_audio = None
        self.undo_bounce = None
        self.undo_takes = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.armed = None
        for strip in self.strips:
            strip.arm.blockSignals(True)
            strip.arm.setChecked(False)
            strip.arm.blockSignals(False)
        self.refresh()

    def new_song(self) -> None:
        if self.confirm_discard():
            self.replace_song(Song(), None)

    def open_song(self) -> None:
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open song", "", "8T song (*.8t *.porta)")
        if path:
            try:
                song = load_song(path)
                self.replace_song(song, Path(path))
                self.statusBar().showMessage(f"Opened {Path(path).name}")
                if any(effect.kind == "vst3" and effect.enabled for track in song.tracks for effect in track.effects):
                    QMessageBox.information(self, "External plugins not loaded", "This song contains VST3 effects. Open each affected track's Effects rack to load trusted plugins, locate missing ones, or bypass them before playback/export.")
            except Exception as error:
                self.show_error("Could not open song", error)

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        try:
            save_song(self.song, self.path)
            text_path = self.path.with_suffix(".lyrics.txt")
            try:
                export_lyrics(self.song, text_path)
            except Exception as error:
                self.mark_dirty()
                self.show_error("Could not save songwriting text",
                                f"The project was saved, including your writing, but {text_path.name} could not be updated: {error}")
                return False
            self.dirty = False
            self.clear_recovery()
            self.update_title()
            self.statusBar().showMessage(f"Saved {self.path.name} and {text_path.name}")
            return True
        except Exception as error:
            self.show_error("Could not save song", error)
            return False

    def choose_save_path(self, title: str, suggested: str, extension: str) -> Path | None:
        dialog = QFileDialog(self, title)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setNameFilter(f"{extension.upper()} files (*.{extension})")
        dialog.setDefaultSuffix(extension)
        dialog.selectFile(suggested)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return Path(dialog.selectedFiles()[0])
        return None

    def save_as(self) -> bool:
        suggested = self.path.with_suffix(".8t") if self.path else Path("Untitled.8t")
        destination = self.choose_save_path("Save song", str(suggested), "8t")
        if destination is None:
            return False
        previous = self.path
        self.path = destination
        if self.save():
            return True
        self.path = previous
        return False

    def export(self) -> None:
        from eighttrack.tape_ui import MixdownDialog

        dialog = MixdownDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        try:
            start, end = self.song.tape_range() if dialog.range.currentIndex() else (0, None)
        except ValueError as error:
            self.show_error("Invalid mixdown range", error)
            dialog.deleteLater()
            return
        tails, stems = dialog.tails.isChecked(), dialog.stems.isChecked()
        dialog.deleteLater()
        if stems:
            directory = QFileDialog.getExistingDirectory(self, "Export stems to empty folder")
            if directory:
                self.run_job(lambda: export_stems(self.song, directory, start=start, end=end, include_tails=tails),
                             lambda result: self.statusBar().showMessage(f"Exported {len(result[0])} stems" +
                                                                         (" - clipping detected" if result[1] else "")),
                             "Exporting stems")
            return
        destination = self.choose_save_path("Export stereo mix", "Mix.wav", "wav")
        if destination is not None:
            def completed(clipped):
                self.statusBar().showMessage(f"Exported {destination.name}")
                if clipped:
                    QMessageBox.warning(self, "Mix clipped", "The mix exceeded 0 dBFS. Lower the master or track volumes and export again.")
            self.run_job(lambda: export_mix(self.song, destination, start=start, end=end, include_tails=tails),
                         completed, "Exporting stereo mix")

    def calibrate_latency(self) -> None:
        if self.engine.running or self.busy:
            return
        answer = QMessageBox.question(self, "Loopback calibration",
            "Calibration sends a quiet test burst. Connect a line output to the selected line input, "
            "disable direct monitoring, turn phantom power off, and lower speaker levels. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        def completed(offset):
            self.engine.recording_offset = offset
            self.settings.setValue("recording_offset", offset)
            self.statusBar().showMessage(f"Recording offset: {offset * 1000 / SAMPLE_RATE:.2f} ms")
        self.run_job(self.engine.calibrate_latency, completed, "Calibrating recording offset")

    def audio_devices(self) -> None:
        if self.engine.running or self.busy:
            return
        try:
            dialog = AudioDevicesDialog(self.engine, self)
        except Exception as error:
            self.show_error("Audio devices unavailable", error)
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.setValue("recording_offset", self.engine.recording_offset)
        dialog.deleteLater()

    def closeEvent(self, event) -> None:
        if self.busy:
            self.statusBar().showMessage("Finish the current operation before closing")
            event.ignore()
            return
        self.stop()
        if self.confirm_discard():
            self.settings.setValue("docks/state", self.saveState(1))
            self.settings.setValue("docks/locked", self.lock_docks_action.isChecked())
            self.clear_recovery()
            self.timer.stop()
            self.recovery_timer.stop()
            event.accept()
        else:
            event.ignore()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("8T")
    app.setApplicationDisplayName("8T: 8 Track DAW")
    app.setStyle("Fusion")
    window = StudioWindow()
    window.show()
    QTimer.singleShot(0, window.offer_recovery)
    sys.exit(app.exec())