"""Plugin hosting and offline rendering, separate from Qt and audio callbacks."""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path

import numpy as np
import pedalboard as pb
from scipy.ndimage import map_coordinates


MAX_EFFECTS = 8
TAIL_SECONDS = 3
MAX_STATE_BYTES = 16 * 1024 * 1024

BUILTINS = {
    "Gain": (pb.Gain, {"gain_db": (0.0, -60.0, 24.0, 0.5)}),
    "Compressor": (pb.Compressor, {
        "threshold_db": (-18.0, -60.0, 0.0, 1.0), "ratio": (4.0, 1.0, 20.0, 0.5),
        "attack_ms": (10.0, 0.1, 200.0, 1.0), "release_ms": (100.0, 1.0, 1000.0, 10.0),
    }),
    "Reverb": (pb.Reverb, {
        "room_size": (0.5, 0.0, 1.0, 0.05), "damping": (0.5, 0.0, 1.0, 0.05),
        "wet_level": (0.25, 0.0, 1.0, 0.05), "dry_level": (0.8, 0.0, 1.0, 0.05),
        "width": (1.0, 0.0, 1.0, 0.05),
    }),
    "Delay": (pb.Delay, {
        "delay_seconds": (0.3, 0.01, 2.0, 0.01), "feedback": (0.3, 0.0, 0.9, 0.05),
        "mix": (0.25, 0.0, 1.0, 0.05),
    }),
    "Distortion": (pb.Distortion, {"drive_db": (6.0, 0.0, 36.0, 0.5)}),
    "Lowpass": (pb.LowpassFilter, {"cutoff_frequency_hz": (6000.0, 20.0, 20000.0, 100.0)}),
    "Highpass": (pb.HighpassFilter, {"cutoff_frequency_hz": (80.0, 20.0, 20000.0, 10.0)}),
}


@dataclass
class Effect:
    kind: str
    name: str
    enabled: bool = True
    path: str = ""
    plugin_name: str = ""
    parameters: dict[str, float] = field(default_factory=dict)
    state: bytes = b""
    processor: object = field(default=None, repr=False, compare=False)
    error: str = ""

    @classmethod
    def builtin(cls, name: str) -> "Effect":
        if name not in BUILTINS:
            raise ValueError(f"Unknown built-in effect: {name}")
        effect = cls("builtin", name, parameters={key: spec[0] for key, spec in BUILTINS[name][1].items()})
        effect.load()
        return effect

    @classmethod
    def vst3(cls, path: str, plugin_name: str = "") -> "Effect":
        effect = cls("vst3", Path(path).stem, path=str(Path(path).expanduser().resolve()), plugin_name=plugin_name)
        effect.load()
        return effect

    def load(self) -> None:
        """Load only on the main thread after the user approves external code."""
        try:
            if self.kind == "builtin":
                constructor, specs = BUILTINS[self.name]
                if set(self.parameters) != set(specs):
                    raise ValueError("Invalid built-in effect parameters.")
                for key, value in self.parameters.items():
                    if not isinstance(value, (int, float)) or not math.isfinite(value) or not specs[key][1] <= value <= specs[key][2]:
                        raise ValueError(f"Invalid value for {key}.")
                processor = constructor(**self.parameters)
            elif self.kind == "vst3":
                if Path(self.path).suffix.lower() != ".vst3":
                    raise ValueError("Select a native VST3 file or .vst3 bundle directory.")
                processor = pb.load_plugin(self.path, plugin_name=self.plugin_name or None)
                if not processor.is_effect or processor.is_instrument:
                    raise ValueError("This rack supports audio effects, not MIDI instruments.")
                if self.state:
                    if processor.name != self.name:
                        raise ValueError("The selected plugin does not match the saved effect. Add it as a new effect instead.")
                    processor.raw_state = self.state
                self.name = processor.name
            else:
                raise ValueError(f"Unsupported effect type: {self.kind}")
            self.processor = processor
            self.error = ""
        except Exception as error:
            self.processor = None
            self.error = str(error)
            raise

    def capture_state(self) -> None:
        if self.kind == "vst3" and self.processor is not None:
            state = bytes(self.processor.raw_state)
            if len(state) > MAX_STATE_BYTES:
                raise ValueError("Plugin state exceeds the 16 MB per-effect limit.")
            self.state = state

    def cache_key(self) -> tuple:
        return (self.kind, self.name, self.enabled, self.path, self.plugin_name,
                json.dumps(self.parameters, sort_keys=True), self.state, id(self.processor))

    def to_dict(self) -> dict:
        self.capture_state()
        return {"kind": self.kind, "name": self.name, "enabled": self.enabled,
                "path": self.path, "plugin_name": self.plugin_name, "parameters": self.parameters.copy()}

    @classmethod
    def from_dict(cls, data: dict, state: bytes = b"") -> "Effect":
        if not isinstance(data, dict):
            raise ValueError("Invalid effect metadata.")
        for key in ("kind", "name", "path", "plugin_name"):
            if not isinstance(data.get(key), str) or len(data[key]) > 4096:
                raise ValueError("Invalid effect metadata.")
        if data["kind"] not in ("builtin", "vst3") or not isinstance(data.get("enabled"), bool):
            raise ValueError("Unsupported effect type or bypass setting.")
        if not isinstance(data.get("parameters"), dict) or len(state) > MAX_STATE_BYTES:
            raise ValueError("Invalid effect parameters or state.")
        effect = cls(data["kind"], data["name"], data["enabled"], data["path"],
                     data["plugin_name"], data["parameters"].copy(), state)
        if effect.kind == "builtin":
            effect.load()
        else:
            effect.error = "Not loaded. Load this trusted plugin from the effects rack, or bypass it."
        return effect


def render_effects(audio: np.ndarray, effects: list[Effect], sample_rate: int) -> np.ndarray:
    """Render a complete stereo track, including a bounded tail, without changing its take."""
    active = [effect for effect in effects if effect.enabled]
    source = audio if audio.ndim == 2 else np.column_stack((audio, audio))
    if not active or not len(audio):
        return source.copy()
    for effect in active:
        if effect.processor is None:
            raise ValueError(f"{effect.name}: {effect.error or 'Plugin is not loaded.'}")
    stereo = np.ascontiguousarray(np.pad(source, ((0, int(sample_rate * TAIL_SECONDS)), (0, 0))).T)
    board = pb.Pedalboard([effect.processor for effect in active])
    rendered = board.process(stereo, sample_rate, buffer_size=512, reset=True)
    if rendered.shape != stereo.shape or not np.isfinite(rendered).all():
        raise ValueError("Plugin returned incomplete or invalid audio. Bypass it and try again.")
    return np.ascontiguousarray(rendered.T, dtype=np.float32)


def render_channel(audio: np.ndarray, sample_rate: int, low: float, mid: float,
                   high: float, drive: float, flutter: float) -> np.ndarray:
    processors = []
    if low:
        processors.append(pb.LowShelfFilter(cutoff_frequency_hz=120, gain_db=low))
    if mid:
        processors.append(pb.PeakFilter(cutoff_frequency_hz=1200, gain_db=mid, q=0.7))
    if high:
        processors.append(pb.HighShelfFilter(cutoff_frequency_hz=8000, gain_db=high))
    if drive:
        processors.extend([pb.Distortion(drive_db=drive), pb.Gain(gain_db=-drive / 2),
                           pb.LowpassFilter(cutoff_frequency_hz=14000)])
    stereo = audio.T if audio.ndim == 2 else np.stack((audio, audio))
    rendered = pb.Pedalboard(processors).process(stereo, sample_rate, reset=True) if processors else stereo.copy()
    if flutter:
        positions = np.arange(rendered.shape[1], dtype=np.float64)
        seconds = positions / sample_rate
        displacement = flutter * sample_rate * (0.0015 * np.sin(2 * np.pi * 0.6 * seconds)
                                                + 0.00015 * np.sin(2 * np.pi * 6 * seconds))
        for channel in range(2):
            rendered[channel] = map_coordinates(rendered[channel], [positions + displacement],
                                                 order=1, mode="constant", prefilter=False)
    return np.ascontiguousarray(rendered.T, dtype=np.float32)


def render_send(audio: np.ndarray, sample_rate: int, kind: str, room: float,
                delay: float, feedback: float) -> np.ndarray:
    stereo = audio.T if audio.ndim == 2 else np.stack((audio, audio))
    stereo = np.pad(stereo, ((0, 0), (0, sample_rate * TAIL_SECONDS)))
    processor = (pb.Reverb(room_size=room, wet_level=1, dry_level=0)
                 if kind == "reverb" else pb.Delay(delay_seconds=delay, feedback=feedback, mix=1))
    return np.ascontiguousarray(pb.Pedalboard([processor]).process(stereo, sample_rate, reset=True).T)