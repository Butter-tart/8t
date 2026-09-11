"""Secondary tape, channel and mixdown panels for the eight-track desk."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QSpinBox, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from eighttrack.model import MAX_SECONDS, SAMPLE_RATE


def seconds_control(frames=0):
    control = QDoubleSpinBox()
    control.setRange(0, MAX_SECONDS)
    control.setDecimals(3)
    control.setSingleStep(0.1)
    control.setSuffix(" s")
    control.setValue(frames / SAMPLE_RATE)
    return control


def command(parent, text, icon, callback):
    button = QPushButton(text)
    button.setIcon(parent.style().standardIcon(icon))
    button.clicked.connect(callback)
    return button


class TapeSettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Tape and location memories")
        self.resize(480, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.count_in = QSpinBox()
        self.count_in.setRange(0, 16)
        self.count_in.setSuffix(" beats")
        self.count_in.setValue(window.song.count_in)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(50, 200)
        self.speed.setSuffix(" %")
        self.speed.setValue(window.song.tape_speed * 100)
        self.speed.setToolTip("Playback and mixdown speed; recording requires 100%")
        self.room = QDoubleSpinBox()
        self.room.setRange(0, 1)
        self.room.setSingleStep(0.05)
        self.room.setValue(window.song.reverb_room)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.01, 2)
        self.delay.setSuffix(" s")
        self.delay.setValue(window.song.delay_time)
        self.feedback = QDoubleSpinBox()
        self.feedback.setRange(0, 90)
        self.feedback.setSuffix(" %")
        self.feedback.setValue(window.song.delay_feedback * 100)
        form.addRow("Count-in", self.count_in)
        form.addRow("Tape speed", self.speed)
        form.addRow("Reverb room", self.room)
        form.addRow("Shared delay", self.delay)
        form.addRow("Delay feedback", self.feedback)
        layout.addLayout(form)
        self.locations = QListWidget()
        self.locations.setAccessibleName("Location memories")
        layout.addWidget(self.locations)
        self.name = QLineEdit()
        self.name.setMaxLength(32)
        self.name.setPlaceholderText("Location name")
        self.name.setAccessibleName("Location name")
        layout.addWidget(self.name)
        actions = QHBoxLayout()
        actions.addWidget(command(self, "Store", QStyle.StandardPixmap.SP_DialogSaveButton, self.store))
        actions.addWidget(command(self, "Locate", QStyle.StandardPixmap.SP_MediaSeekForward, self.locate))
        actions.addWidget(command(self, "Remove", QStyle.StandardPixmap.SP_TrashIcon, self.remove))
        layout.addLayout(actions)
        self.locations.itemDoubleClicked.connect(lambda item: self.locate())
        for control in (self.count_in, self.speed, self.room, self.delay, self.feedback):
            control.valueChanged.connect(self.apply_settings)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_locations()

    def apply_settings(self):
        song = self.window.song
        song.count_in, song.tape_speed = self.count_in.value(), self.speed.value() / 100
        song.reverb_room, song.delay_time = self.room.value(), self.delay.value()
        song.delay_feedback = self.feedback.value() / 100
        self.window.mark_dirty()

    def refresh_locations(self):
        self.locations.clear()
        for name, frame in self.window.song.markers.items():
            self.locations.addItem(f"{frame / SAMPLE_RATE:07.3f} s    {name}")
            self.locations.item(self.locations.count() - 1).setData(Qt.ItemDataRole.UserRole, name)

    def store(self):
        try:
            self.window.song.set_marker(self.name.text(), self.window.engine.position)
            self.window.mark_dirty()
            self.refresh_locations()
        except ValueError as error:
            self.window.show_error("Could not store location", error)

    def locate(self):
        item = self.locations.currentItem()
        if item is not None:
            self.window.engine.seek(self.window.song.markers[item.data(Qt.ItemDataRole.UserRole)])
            self.window.tick()

    def remove(self):
        item = self.locations.currentItem()
        if item is not None:
            del self.window.song.markers[item.data(Qt.ItemDataRole.UserRole)]
            self.window.mark_dirty()
            self.refresh_locations()


class TrackToolsDialog(QDialog):
    def __init__(self, window, index):
        super().__init__(window)
        self.window, self.index = window, index
        self.setWindowTitle(f"{index + 1:02d} - {self.track.name}")
        self.resize(480, 470)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        edit = QWidget()
        form = QFormLayout(edit)
        self.start = seconds_control(window.song.marker_a)
        self.end = seconds_control(window.song.marker_b or len(self.track.audio))
        self.destination = seconds_control(window.engine.position)
        self.operation = QComboBox()
        self.operation.addItems(["silence", "trim", "copy", "move", "fade in", "fade out"])
        self.fade = QDoubleSpinBox()
        self.fade.setRange(0, 100)
        self.fade.setValue(5)
        self.fade.setSuffix(" ms")
        self.fade.setToolTip("Edge fades for silence, trim, copy and move")
        form.addRow("Start", self.start)
        form.addRow("End", self.end)
        form.addRow("Operation", self.operation)
        form.addRow("Destination", self.destination)
        form.addRow("Edge fade", self.fade)
        self.destination.setEnabled(False)
        self.operation.currentTextChanged.connect(lambda value: self.destination.setEnabled(value in ("copy", "move")))
        form.addRow(command(self, "Apply edit", QStyle.StandardPixmap.SP_DialogApplyButton, self.edit))
        tabs.addTab(edit, "Tape edit")
        takes = QWidget()
        take_layout = QVBoxLayout(takes)
        self.active_take = QLabel()
        self.active_take.setWordWrap(True)
        take_layout.addWidget(self.active_take)
        self.takes = QListWidget()
        self.takes.setAccessibleName("Alternate takes")
        take_layout.addWidget(self.takes)
        self.take_name = QLineEdit()
        self.take_name.setMaxLength(64)
        self.take_name.setPlaceholderText("Take name")
        self.take_name.setAccessibleName("Take name")
        take_layout.addWidget(self.take_name)
        take_layout.addWidget(command(self, "Keep current take", QStyle.StandardPixmap.SP_DialogSaveButton, self.keep_take))
        take_layout.addWidget(command(self, "Swap with active take", QStyle.StandardPixmap.SP_BrowserReload, self.swap_take))
        take_layout.addWidget(command(self, "Remove alternate", QStyle.StandardPixmap.SP_TrashIcon, self.remove_take))
        tabs.addTab(takes, "Takes")
        channel = QWidget()
        channel_form = QFormLayout(channel)
        self.channel_format = QComboBox()
        self.channel_format.addItems(["Mono", "Stereo"])
        self.channel_format.setCurrentIndex(self.track.channels - 1)
        self.channel_format.setAccessibleName("Track channel format")
        channel_form.addRow("Format", self.channel_format)
        channel_form.addRow(command(self, "Convert format", QStyle.StandardPixmap.SP_BrowserReload, self.convert_format))
        self.channel_controls = {}
        for key, label, minimum, maximum, suffix, scale in (
            ("eq_low", "Low / 120 Hz", -12, 12, " dB", 1),
            ("eq_mid", "Mid / 1.2 kHz", -12, 12, " dB", 1),
            ("eq_high", "High / 8 kHz", -12, 12, " dB", 1),
            ("reverb_send", "Reverb send", 0, 100, " %", 100),
            ("delay_send", "Delay send", 0, 100, " %", 100),
            ("tape_drive", "Tape saturation", 0, 18, " dB", 1),
            ("wow_flutter", "Wow / flutter", 0, 100, " %", 100),
        ):
            control = QDoubleSpinBox()
            control.setRange(minimum, maximum)
            control.setSuffix(suffix)
            control.setValue(getattr(self.track, key) * scale)
            control.valueChanged.connect(lambda value, attribute=key, divisor=scale: self.set_channel(attribute, value / divisor))
            control.setAccessibleName(label)
            channel_form.addRow(label, control)
            self.channel_controls[key] = control
        tabs.addTab(channel, "Channel")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_takes()

    @property
    def track(self):
        return self.window.song.tracks[self.index]

    def edit(self):
        self.window.apply_track_edit(self.index, lambda: self.track.edit_range(
            round(self.start.value() * SAMPLE_RATE), round(self.end.value() * SAMPLE_RATE),
            self.operation.currentText(), round(self.destination.value() * SAMPLE_RATE),
            round(self.fade.value() * SAMPLE_RATE / 1000)))

    def keep_take(self):
        if self.window.apply_track_edit(self.index, lambda: self.track.keep_take(self.take_name.text()), takes=True):
            self.refresh_takes()

    def swap_take(self):
        if self.window.apply_track_edit(self.index, lambda: self.track.select_take(self.takes.currentRow()), takes=True):
            self.refresh_takes()

    def remove_take(self):
        row = self.takes.currentRow()
        if row >= 0 and self.window.apply_track_edit(self.index, lambda: self.track.takes.pop(row), takes=True):
            self.refresh_takes()

    def refresh_takes(self):
        self.active_take.setText(f"Active: {self.track.active_take_name}")
        self.takes.clear()
        for take in self.track.takes:
            self.takes.addItem(f"{take.name}  /  {len(take.audio) / SAMPLE_RATE:.3f} s")

    def set_channel(self, key, value):
        setattr(self.track, key, value)
        self.track._fx_key = None
        self.window.mark_dirty()

    def convert_format(self):
        channels = self.channel_format.currentIndex() + 1
        if channels == self.track.channels:
            return
        if len(self.track.audio) or self.track.takes:
            description = "Duplicate mono audio into left and right channels" if channels == 2 else "Average left and right channels into mono"
            answer = QMessageBox.question(self, "Convert track format",
                                          f"{description}, including all alternate takes?",
                                          defaultButton=QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.channel_format.setCurrentIndex(self.track.channels - 1)
                return
        self.window.apply_track_edit(self.index, lambda: self.track.convert_channels(channels), takes=True)
        self.channel_format.setCurrentIndex(self.track.channels - 1)
        self.refresh_takes()


class MixdownDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("Mixdown")
        form = QFormLayout(self)
        self.range = QComboBox()
        self.range.addItems(["Whole song", "A to B"])
        self.tails = QCheckBox("Include effect tails")
        self.tails.setChecked(True)
        self.stems = QCheckBox("Individual processed tracks")
        self.stems.setToolTip("Export all nonempty tracks, ignoring mute and solo; retain volume, pan and sends")
        form.addRow("Range", self.range)
        form.addRow(self.tails)
        form.addRow(self.stems)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)