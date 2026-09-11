"""A stopped-transport effects rack with explicit loading of trusted native code."""

from pathlib import Path

from pedalboard import VST3Plugin
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLayout, QListView, QListWidget, QMenu,
    QMessageBox, QPushButton, QScrollArea, QStyle, QVBoxLayout, QWidget,
)

from eighttrack.effects import BUILTINS, Effect, MAX_EFFECTS
from eighttrack.ui import tool_button, track_label


class EffectsPanel(QWidget):
    def __init__(self, window, track_index: int):
        super().__init__(window)
        self.window = window
        self.track_index = track_index
        self.track = window.song.tracks[track_index]
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.track_choice = QComboBox()
        self.track_choice.setAccessibleName("Effects track")
        self.track_choice.setMinimumWidth(150)
        self.track_choice.setMaximumWidth(220)
        self.track_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.track_choice.addItems([track_label(index, track.name) for index, track in enumerate(window.song.tracks)])
        self.track_choice.setCurrentIndex(track_index)
        self.track_choice.currentIndexChanged.connect(self.set_track)
        toolbar.addWidget(self.track_choice)
        self.builtin_choice = QComboBox()
        self.builtin_choice.addItems(list(BUILTINS))
        self.builtin_choice.setAccessibleName("Built-in effect")
        toolbar.addWidget(self.builtin_choice, 1)
        self.add_button = tool_button(self, QStyle.StandardPixmap.SP_ArrowRight, "Add built-in effect")
        self.add_button.setIcon(QIcon.fromTheme("list-add", self.add_button.icon()))
        self.add_button.clicked.connect(self.add_builtin)
        toolbar.addWidget(self.add_button)
        self.vst_button = QPushButton("Load VST3")
        self.vst_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        menu = QMenu(self.vst_button)
        menu.addAction("Plugin file...", lambda: self.add_vst3(False))
        menu.addAction("Plugin bundle folder...", lambda: self.add_vst3(True))
        self.vst_button.setMenu(menu)
        toolbar.addWidget(self.vst_button)
        layout.addLayout(toolbar)
        body = QVBoxLayout()
        rack = QHBoxLayout()
        self.effect_list = QListWidget()
        self.effect_list.setFlow(QListView.Flow.LeftToRight)
        self.effect_list.setWrapping(False)
        self.effect_list.setFixedHeight(62)
        self.effect_list.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.effect_list.setStyleSheet("QListWidget { background: #f9faf8; border: 1px solid #bbc3bc; } QListWidget::item { padding: 9px; } QListWidget::item:selected { background: #426d59; color: white; }")
        self.effect_list.setAccessibleName("Effect chain, first to last")
        self.effect_list.currentRowChanged.connect(self.show_parameters)
        rack.addWidget(self.effect_list, 1)
        ordering = QHBoxLayout()
        self.up_button = tool_button(self, QStyle.StandardPixmap.SP_ArrowLeft, "Move effect earlier")
        self.down_button = tool_button(self, QStyle.StandardPixmap.SP_ArrowRight, "Move effect later")
        self.remove_button = tool_button(self, QStyle.StandardPixmap.SP_TrashIcon, "Remove effect")
        self.up_button.clicked.connect(lambda: self.move_effect(-1))
        self.down_button.clicked.connect(lambda: self.move_effect(1))
        self.remove_button.clicked.connect(self.remove_effect)
        for button in (self.up_button, self.down_button, self.remove_button):
            ordering.addWidget(button)
        rack.addLayout(ordering)
        body.addLayout(rack)
        panel = QVBoxLayout()
        self.enabled = QCheckBox("Enabled")
        self.enabled.setToolTip("Uncheck to bypass this effect")
        self.enabled.toggled.connect(self.set_enabled)
        panel.addWidget(self.enabled)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setStyleSheet("color: #a6383c;")
        panel.addWidget(self.message)
        commands = QHBoxLayout()
        self.load_button = QPushButton("Load saved plugin")
        self.load_button.clicked.connect(self.load_saved)
        self.locate_button = QPushButton("Locate")
        locate_menu = QMenu(self.locate_button)
        locate_menu.addAction("Plugin file...", lambda: self.locate_plugin(False))
        locate_menu.addAction("Plugin bundle folder...", lambda: self.locate_plugin(True))
        self.locate_button.setMenu(locate_menu)
        commands.addWidget(self.load_button)
        commands.addWidget(self.locate_button)
        panel.addLayout(commands)
        self.editor_button = QPushButton("Open plugin editor")
        self.editor_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.editor_button.clicked.connect(self.open_editor)
        panel.addWidget(self.editor_button)
        self.parameters_area = QScrollArea()
        self.parameters_area.setWidgetResizable(True)
        self.parameters_area.setFrameShape(QScrollArea.Shape.NoFrame)
        panel.addWidget(self.parameters_area, 1)
        body.addLayout(panel, 1)
        layout.addLayout(body, 1)
        self.refresh_list(0)

    def set_track(self, index: int) -> None:
        if not 0 <= index < len(self.window.song.tracks):
            return
        self.track_index = index
        self.track = self.window.song.tracks[index]
        self.track_choice.blockSignals(True)
        self.track_choice.setCurrentIndex(index)
        self.track_choice.blockSignals(False)
        self.refresh_list(0)

    def refresh_tracks(self) -> None:
        for index, track in enumerate(self.window.song.tracks):
            self.track_choice.setItemText(index, track_label(index, track.name))
        if self.track is not self.window.song.tracks[self.track_index]:
            self.set_track(self.track_index)

    def selected(self) -> Effect | None:
        index = self.effect_list.currentRow()
        return self.track.effects[index] if 0 <= index < len(self.track.effects) else None

    def changed(self) -> None:
        self.track._fx_key = None
        self.window.mark_dirty()
        self.window.strips[self.track_index].refresh()

    def refresh_list(self, selected: int) -> None:
        self.effect_list.blockSignals(True)
        self.effect_list.clear()
        for index, effect in enumerate(self.track.effects):
            state = "BYPASS" if not effect.enabled else "NOT LOADED" if effect.processor is None else "ON"
            self.effect_list.addItem(f"{index + 1}. {effect.name} [{state}]")
            self.effect_list.item(index).setToolTip(effect.path or effect.name)
        self.effect_list.setCurrentRow(min(selected, len(self.track.effects) - 1))
        self.effect_list.blockSignals(False)
        self.add_button.setEnabled(len(self.track.effects) < MAX_EFFECTS)
        self.vst_button.setEnabled(len(self.track.effects) < MAX_EFFECTS)
        self.show_parameters()

    def show_parameters(self, row: int = -1) -> None:
        effect = self.selected()
        selected = self.effect_list.currentRow()
        self.up_button.setEnabled(effect is not None and selected > 0)
        self.down_button.setEnabled(effect is not None and selected < len(self.track.effects) - 1)
        self.remove_button.setEnabled(effect is not None)
        self.enabled.blockSignals(True)
        self.enabled.setEnabled(effect is not None)
        self.enabled.setChecked(effect.enabled if effect else False)
        self.enabled.blockSignals(False)
        external = effect is not None and effect.kind == "vst3"
        self.load_button.setVisible(external and effect.processor is None)
        self.locate_button.setVisible(external and effect.processor is None)
        self.editor_button.setVisible(external and effect.processor is not None)
        self.message.setText(effect.error if effect else "Empty rack")
        self.message.setVisible(bool(self.message.text()))
        container = QWidget()
        self.parameter_form = QGridLayout(container)
        self.parameter_form.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.parameter_form.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.parameter_form.setHorizontalSpacing(20)
        self.parameter_controls = {}
        self.parameter_labels = {}
        if effect is not None and effect.processor is not None:
            try:
                if effect.kind == "builtin":
                    specs = BUILTINS[effect.name][1]
                    for key, spec in specs.items():
                        self.add_parameter(key, effect.parameters[key], spec[1], spec[2], spec[3], False)
                else:
                    for key, parameter in effect.processor.parameters.items():
                        self.add_parameter(key, parameter.raw_value, 0, 1, 0.01, True)
            except Exception as error:
                self.message.setText(f"Could not read parameters: {error}")
                self.message.show()
        self.parameters_area.setWidget(container)
        self.layout_parameters()

    def layout_parameters(self) -> None:
        columns = max(1, min(4, self.parameters_area.viewport().width() // 240))
        for index, (key, control) in enumerate(self.parameter_controls.items()):
            label = self.parameter_labels[key]
            self.parameter_form.removeWidget(label)
            self.parameter_form.removeWidget(control)
            row, column = divmod(index, columns)
            self.parameter_form.addWidget(label, row * 2, column)
            self.parameter_form.addWidget(control, row * 2 + 1, column)
            self.parameter_form.activate()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.layout_parameters()

    def add_parameter(self, key: str, value: float, minimum: float,
                      maximum: float, step: float, normalized: bool) -> None:
        control = QDoubleSpinBox()
        control.setFixedWidth(180)
        control.setMinimumHeight(34)
        control.setDecimals(4 if normalized else 2)
        control.setRange(minimum, maximum)
        control.setSingleStep(step)
        control.setValue(value)
        control.setKeyboardTracking(False)
        label = QLabel(key.replace("_", " ").title())
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(110)
        label.setMaximumWidth(180)
        control.setAccessibleName(key)
        if normalized:
            control.setToolTip("Plugin parameter in its native normalized range: 0 to 1")
        control.valueChanged.connect(lambda value, name=key: self.set_parameter(name, value))
        self.parameter_controls[key] = control
        self.parameter_labels[key] = label

    def set_parameter(self, name: str, value: float) -> None:
        effect = self.selected()
        if effect is None or effect.processor is None:
            return
        try:
            if effect.kind == "builtin":
                setattr(effect.processor, name, value)
                effect.parameters[name] = value
            else:
                effect.processor.parameters[name].raw_value = value
                effect.capture_state()
            self.changed()
        except Exception as error:
            self.changed()
            self.window.show_error("Could not change parameter", error)
            self.show_parameters()

    def add_builtin(self) -> None:
        if len(self.track.effects) >= MAX_EFFECTS:
            return
        self.track.effects.append(Effect.builtin(self.builtin_choice.currentText()))
        self.changed()
        self.refresh_list(len(self.track.effects) - 1)

    def set_enabled(self, enabled: bool) -> None:
        effect = self.selected()
        if effect is not None:
            effect.enabled = enabled
            self.changed()
            self.refresh_list(self.effect_list.currentRow())

    def move_effect(self, direction: int) -> None:
        source = self.effect_list.currentRow()
        destination = source + direction
        if source >= 0 and 0 <= destination < len(self.track.effects):
            self.track.effects.insert(destination, self.track.effects.pop(source))
            self.changed()
            self.refresh_list(destination)

    def remove_effect(self) -> None:
        selected = self.effect_list.currentRow()
        if selected >= 0:
            del self.track.effects[selected]
            self.changed()
            self.refresh_list(selected)

    def choose_plugin(self, bundle: bool) -> str:
        folder = Path.home() / ".vst3"
        start = str(folder if folder.exists() else Path.home())
        if bundle:
            return QFileDialog.getExistingDirectory(self, "Select the .vst3 bundle folder", start)
        return QFileDialog.getOpenFileName(self, "Select a VST3 plugin", start, "VST3 plugins (*.vst3)")[0]

    def trust_plugin(self, path: str) -> bool:
        if Path(path).suffix.lower() != ".vst3":
            self.window.show_error("Unsupported plugin", ValueError("Select a native .vst3 file or bundle folder."))
            return False
        answer = QMessageBox.warning(
            self, "Load trusted plugin?",
            f"Load native code from:\n{path}\n\nPlugins run inside this app and may crash it. "
            "Only continue if you trust this plugin and any saved plugin state. Save your song first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def add_vst3(self, bundle: bool) -> None:
        if len(self.track.effects) >= MAX_EFFECTS:
            return
        path = self.choose_plugin(bundle)
        if not path or not self.trust_plugin(path):
            return
        try:
            names = VST3Plugin.get_plugin_names_for_file(path)
            if not names:
                raise ValueError("No native VST3 plugins were found in that file or bundle.")
            name = names[0]
            if len(names) > 1:
                name, accepted = QInputDialog.getItem(self, "Select plugin", "Plugin", names, editable=False)
                if not accepted:
                    return
            effect = Effect.vst3(path, name)
            effect.capture_state()
            self.track.effects.append(effect)
            self.changed()
            self.refresh_list(len(self.track.effects) - 1)
        except Exception as error:
            self.window.show_error("Could not load VST3", error)

    def load_saved(self) -> None:
        effect = self.selected()
        if effect is None or not self.trust_plugin(effect.path):
            return
        try:
            effect.load()
            self.changed()
        except Exception as error:
            self.window.show_error("Could not load saved plugin", error)
        self.refresh_list(self.effect_list.currentRow())

    def locate_plugin(self, bundle: bool) -> None:
        effect = self.selected()
        if effect is None:
            return
        path = self.choose_plugin(bundle)
        if not path or not self.trust_plugin(path):
            return
        previous = effect.path
        try:
            effect.path = str(Path(path).resolve())
            effect.load()
            self.changed()
        except Exception as error:
            effect.path = previous
            self.window.show_error("Could not relocate plugin", error)
        self.refresh_list(self.effect_list.currentRow())

    def open_editor(self) -> None:
        effect = self.selected()
        if effect is None or effect.processor is None:
            return
        try:
            effect.processor.show_editor()
        except Exception as error:
            self.window.show_error("Plugin editor unavailable", error)
        finally:
            self.changed()
            try:
                effect.capture_state()
            except Exception as error:
                self.window.show_error("Could not capture plugin state", error)
            self.show_parameters()