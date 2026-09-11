"""Song data and file operations, independent of the interface and audio hardware."""

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from io import BytesIO
import json
import math
from pathlib import Path
import tempfile
import zipfile

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt
import soundfile as sf

from eighttrack.effects import Effect, MAX_EFFECTS, MAX_STATE_BYTES, TAIL_SECONDS, render_channel, render_effects, render_send


SAMPLE_RATE = 44_100
TRACK_COUNT = 8
MAX_SECONDS = 600
MAX_FRAMES = SAMPLE_RATE * MAX_SECONDS
MAX_BOUNCES = 1000
MAX_TAKES = 4
MAX_LYRICS_BYTES = 2_000_000


def audio_channels(audio: np.ndarray) -> int:
    if audio.ndim == 1:
        return 1
    if audio.ndim == 2 and audio.shape[1] == 2:
        return 2
    raise ValueError("Track audio must contain mono or stereo samples.")


def pad_audio(audio: np.ndarray, frames: int) -> np.ndarray:
    padding = (0, frames) if audio.ndim == 1 else ((0, frames), (0, 0))
    return np.pad(audio, padding)


def convert_audio(audio: np.ndarray, channels: int) -> np.ndarray:
    if type(channels) is not int or channels not in (1, 2):
        raise ValueError("Choose mono or stereo audio.")
    if audio_channels(audio) == channels:
        return audio.copy()
    return np.column_stack((audio, audio)) if channels == 2 else audio.mean(axis=1)


@dataclass
class Take:
    name: str
    audio: np.ndarray


@dataclass
class BounceRecord:
    sources: list[int]
    source_names: list[str]
    destination: int
    destination_name: str
    frames: int
    timestamp: str
    undone: bool = False
    channels: int = 1

    @classmethod
    def from_dict(cls, data: object) -> "BounceRecord":
        if not isinstance(data, dict):
            raise ValueError("Invalid bounce history entry.")
        try:
            record = cls(**data)
            if (not isinstance(record.sources, list) or not record.sources
                    or any(type(index) is not int or not 0 <= index < TRACK_COUNT for index in record.sources)
                    or len(set(record.sources)) != len(record.sources)
                    or type(record.destination) is not int or not 0 <= record.destination < TRACK_COUNT
                    or record.destination in record.sources
                    or not isinstance(record.source_names, list)
                    or len(record.source_names) != len(record.sources)
                    or any(not isinstance(name, str) or len(name) > 64
                           for name in [*record.source_names, record.destination_name])
                    or type(record.frames) is not int or not 0 < record.frames <= MAX_FRAMES
                    or not isinstance(record.timestamp, str) or len(record.timestamp) > 40
                    or type(record.channels) is not int or record.channels not in (1, 2)
                    or type(record.undone) is not bool):
                raise ValueError("Invalid bounce history entry.")
            if datetime.fromisoformat(record.timestamp).tzinfo is None:
                raise ValueError("Bounce timestamp must include a timezone.")
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid bounce history entry.") from error
        return record


@dataclass
class Track:
    name: str
    audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    volume: float = 0.8
    pan: float = 0.0
    muted: bool = False
    solo: bool = False
    effects: list[Effect] = field(default_factory=list)
    takes: list[Take] = field(default_factory=list)
    active_take_name: str = "Current"
    eq_low: float = 0.0
    eq_mid: float = 0.0
    eq_high: float = 0.0
    reverb_send: float = 0.0
    delay_send: float = 0.0
    tape_drive: float = 0.0
    wow_flutter: float = 0.0
    _fx_audio: np.ndarray | None = field(default=None, init=False, repr=False, compare=False)
    _fx_source: np.ndarray | None = field(default=None, init=False, repr=False, compare=False)
    _fx_key: tuple | None = field(default=None, init=False, repr=False, compare=False)
    _send_audio: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _send_key: tuple | None = field(default=None, init=False, repr=False, compare=False)
    _tape_audio: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _tape_key: tuple | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def channels(self) -> int:
        return audio_channels(self.audio)

    def invalidate_audio(self) -> None:
        self._fx_key = None
        self._send_key = None
        self._tape_key = None

    def convert_channels(self, channels: int) -> None:
        audio = convert_audio(self.audio, channels)
        takes = [Take(take.name, convert_audio(take.audio, channels)) for take in self.takes]
        self.audio, self.takes = audio, takes
        self.invalidate_audio()

    @property
    def has_effects(self) -> bool:
        return bool(len(self.audio)) and (any(effect.enabled for effect in self.effects)
                                         or any((self.eq_low, self.eq_mid, self.eq_high, self.tape_drive, self.wow_flutter)))

    @property
    def playback_length(self) -> int:
        return len(self.audio) + (SAMPLE_RATE * TAIL_SECONDS if self.has_effects else 0)

    def prepare_effects(self) -> None:
        """Cache effects off the audio thread while the caller owns the track."""
        if not self.has_effects:
            self._fx_audio = self._fx_source = self._fx_key = None
            return
        for effect in self.effects:
            effect.capture_state()
        key = (tuple(effect.cache_key() for effect in self.effects), self.eq_low, self.eq_mid,
               self.eq_high, self.tape_drive, self.wow_flutter)
        if self._fx_source is self.audio and self._fx_key == key:
            return
        rendered = render_effects(self.audio, self.effects, SAMPLE_RATE)
        if not any(effect.enabled for effect in self.effects):
            rendered = np.pad(rendered, ((0, SAMPLE_RATE * TAIL_SECONDS), (0, 0)))
        rendered = render_channel(rendered, SAMPLE_RATE, self.eq_low, self.eq_mid, self.eq_high,
                                  self.tape_drive, self.wow_flutter)
        self._fx_audio = rendered
        self._fx_source = self.audio
        self._fx_key = key

    @property
    def playback_audio(self) -> np.ndarray:
        if not self.has_effects:
            return self.audio
        if self._fx_audio is None or self._fx_source is not self.audio or self._fx_key is None:
            raise ValueError(f"Prepare effects before mixing {self.name}.")
        return self._fx_audio

    def write(self, start: int, audio: np.ndarray) -> None:
        """Overwrite a tape region without erasing audio outside that region."""
        samples = np.asarray(audio, dtype=np.float32)
        if audio_channels(samples) != self.channels or not np.isfinite(samples).all():
            raise ValueError("Samples must be finite and match the track's channel count.")
        if start < 0 or start + len(samples) > MAX_FRAMES:
            raise ValueError(f"Songs are limited to {MAX_SECONDS // 60} minutes.")
        if not len(samples):
            return
        end = start + len(samples)
        if end > len(self.audio):
            self.audio = pad_audio(self.audio, end - len(self.audio))
        self.audio[start:end] = samples
        self.invalidate_audio()

    def keep_take(self, name: str) -> None:
        if len(self.takes) >= MAX_TAKES:
            raise ValueError(f"Each track supports {MAX_TAKES} alternate takes. Remove one first.")
        if not name.strip() or len(name) > 64 or not len(self.audio):
            raise ValueError("Name a nonempty take using up to 64 characters.")
        self.takes.append(Take(name.strip(), self.audio.copy()))

    def select_take(self, index: int) -> None:
        if not 0 <= index < len(self.takes):
            raise ValueError("Select an alternate take.")
        take = self.takes[index]
        if audio_channels(take.audio) != self.channels:
            raise ValueError("The take must match the track's channel count.")
        self.audio, take.audio = take.audio, self.audio
        self.active_take_name, take.name = take.name, self.active_take_name
        self.invalidate_audio()

    def edit_range(self, start: int, end: int, operation: str, destination: int = 0,
                   fade_frames: int = 221) -> None:
        if not 0 <= start < end <= len(self.audio):
            raise ValueError("Select a nonempty range within the track.")
        if operation not in ("silence", "trim", "copy", "move", "fade in", "fade out"):
            raise ValueError("Unknown tape edit.")
        if operation in ("copy", "move") and not 0 <= destination <= MAX_FRAMES - (end - start):
            raise ValueError("The destination exceeds the tape limit.")
        result = self.audio.copy()
        segment = result[start:end].copy()
        fade = min(max(0, fade_frames), len(segment) // 2)
        if operation in ("copy", "move", "trim") and fade:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            if self.channels == 2:
                ramp = ramp[:, None]
            segment[:fade] *= ramp
            segment[-fade:] *= ramp[::-1]
        if operation == "trim":
            result = np.zeros_like(self.audio[:end])
            result[start:end] = segment
        elif operation in ("fade in", "fade out"):
            ramp = np.linspace(0, 1, end - start, dtype=np.float32)
            if self.channels == 2:
                ramp = ramp[:, None]
            result[start:end] *= ramp if operation == "fade in" else ramp[::-1]
        else:
            if operation in ("silence", "move"):
                result[start:end] = 0
                if fade:
                    ramp = np.linspace(0, 1, fade, dtype=np.float32)
                    if self.channels == 2:
                        ramp = ramp[:, None]
                    result[start:start + fade] = self.audio[start:start + fade] * ramp[::-1]
                    result[end - fade:end] = self.audio[end - fade:end] * ramp
            if operation in ("copy", "move"):
                target_end = destination + len(segment)
                result = pad_audio(result, max(0, target_end - len(result)))
                result[destination:target_end] = segment
        self.invalidate_audio()
        self.audio = result


@dataclass
class Song:
    tracks: list[Track] = field(
        default_factory=lambda: [Track(f"Track {index + 1}") for index in range(TRACK_COUNT)]
    )
    master: float = 0.8
    bpm: int = 100
    bounce_history: list[BounceRecord] = field(default_factory=list)
    marker_a: int = 0
    marker_b: int = 0
    markers: dict[str, int] = field(default_factory=dict)
    count_in: int = 0
    loop: bool = False
    punch: bool = False
    tape_speed: float = 1.0
    reverb_room: float = 0.5
    delay_time: float = 0.3
    delay_feedback: float = 0.3
    lyrics: str = ""

    def tape_range(self) -> tuple[int, int]:
        if not 0 <= self.marker_a < self.marker_b <= MAX_FRAMES:
            raise ValueError("Set A before B within the ten-minute tape.")
        return self.marker_a, self.marker_b

    def set_marker(self, name: str, frame: int) -> None:
        if not name.strip() or len(name) > 32:
            raise ValueError("Marker names must contain 1 to 32 characters.")
        if name not in self.markers and len(self.markers) >= 8:
            raise ValueError("A song supports eight location memories.")
        if not 0 <= frame < MAX_FRAMES:
            raise ValueError("The marker is outside the tape.")
        self.markers[name.strip()] = int(frame)

    def bounce(self, sources: list[int], destination: int) -> BounceRecord:
        """Bounce full sources with gain and effects into the destination's channel format."""
        if (not sources or any(type(index) is not int or not 0 <= index < len(self.tracks) for index in sources)
                or len(set(sources)) != len(sources)
                or type(destination) is not int or not 0 <= destination < len(self.tracks)
                or destination in sources):
            raise ValueError("Choose source tracks and a separate destination.")
        if len(self.bounce_history) >= MAX_BOUNCES:
            raise ValueError(f"Songs support up to {MAX_BOUNCES} bounce history entries.")
        selected = [self.tracks[index] for index in sources]
        if any(not len(track.audio) for track in selected):
            raise ValueError("Every source track must contain audio.")
        frames = max(track.playback_length for track in selected)
        if frames > MAX_FRAMES:
            raise ValueError("The bounce including effects tails exceeds the ten-minute song limit.")
        channels = self.tracks[destination].channels
        audio = np.zeros(frames if channels == 1 else (frames, 2), dtype=np.float32)
        for track in selected:
            track.prepare_effects()
            samples = convert_audio(track.playback_audio, channels)
            audio[:len(samples)] += samples * track.volume
        if not np.isfinite(audio).all():
            raise ValueError("The bounce contains invalid samples.")
        record = BounceRecord(
            list(sources), [track.name for track in selected], destination,
            self.tracks[destination].name, frames, datetime.now(timezone.utc).isoformat(),
            channels=channels,
        )
        self.tracks[destination].audio = audio
        self.tracks[destination].invalidate_audio()
        self.bounce_history.append(record)
        return record

    @property
    def length(self) -> int:
        return max((len(track.audio) for track in self.tracks), default=0)

    @property
    def playback_length(self) -> int:
        return max((track.playback_length + (SAMPLE_RATE * TAIL_SECONDS
                    if len(track.audio) and (track.reverb_send or track.delay_send) else 0)
                    for track in self.tracks), default=0)

    def prepare_effects(self, exclude: int | None = None) -> None:
        for index, track in enumerate(self.tracks):
            if index != exclude:
                track.prepare_effects()
                key = (id(track.audio), track._fx_key, bool(track.reverb_send), bool(track.delay_send),
                       self.reverb_room, self.delay_time, self.delay_feedback)
                if key != track._send_key:
                    sends = {}
                    for kind, level in (("reverb", track.reverb_send), ("delay", track.delay_send)):
                        if level and len(track.audio):
                            sends[kind] = render_send(track.playback_audio, SAMPLE_RATE, kind, self.reverb_room,
                                                      self.delay_time, self.delay_feedback)
                    track._send_audio = sends
                    track._send_key = key
                speed_key = (id(track.playback_audio), track._fx_key, key, self.tape_speed)
                if track._tape_key != speed_key:
                    buffers = {}
                    if self.tape_speed > 1 and len(track.audio):
                        coefficients = butter(8, 0.9 / self.tape_speed, output="sos")
                        buffers = {name: sosfilt(coefficients, audio, axis=0).astype(np.float32)
                                   for name, audio in {"dry": track.playback_audio, **track._send_audio}.items()}
                    track._tape_audio, track._tape_key = buffers, speed_key

    def mix(self, start: int, frames: int, exclude: int | None = None, *, tape: bool = False) -> np.ndarray:
        """Mix with live volume, equal-power mono pan and unity-center stereo balance."""
        output = np.zeros((frames, 2), dtype=np.float32)
        has_solo = any(track.solo for track in self.tracks)
        for index, track in enumerate(self.tracks):
            if index == exclude or track.muted or (has_solo and not track.solo):
                continue
            count = min(frames, track.playback_length - start)
            angle = (track.pan + 1) * math.pi / 4
            left_gain, right_gain = math.cos(angle), math.sin(angle)
            if track.channels == 2:
                left_gain = math.cos(max(0, track.pan) * math.pi / 2)
                right_gain = math.cos(min(0, track.pan) * math.pi / 2)
            if count > 0:
                source = track._tape_audio["dry"] if tape and self.tape_speed > 1 else track.playback_audio
                samples = source[start:start + count] * track.volume * self.master
                output[:count, 0] += (samples if samples.ndim == 1 else samples[:, 0]) * left_gain
                output[:count, 1] += (samples if samples.ndim == 1 else samples[:, 1]) * right_gain
            for kind, level in (("reverb", track.reverb_send), ("delay", track.delay_send)):
                if not level or not len(track.audio):
                    continue
                if kind not in track._send_audio:
                    raise ValueError("Prepare shared effects before mixing.")
                source = track._tape_audio[kind] if tape and self.tape_speed > 1 else track._send_audio[kind]
                wet = source[start:start + frames]
                output[:len(wet), 0] += wet[:, 0] * level * track.volume * self.master * left_gain
                output[:len(wet), 1] += wet[:, 1] * level * track.volume * self.master * right_gain
        return output

    def mix_tape(self, start: float, frames: int) -> np.ndarray:
        if self.tape_speed == 1 and start == int(start):
            return self.mix(int(start), frames)
        positions = start + np.arange(frames) * self.tape_speed
        first = int(start)
        count = math.ceil(frames * self.tape_speed) + 2
        source = self.mix(first, count, tape=True)
        coordinates = np.arange(count)
        return np.column_stack([np.interp(positions - first, coordinates, source[:, channel])
                                for channel in range(2)]).astype(np.float32)


def import_audio(path: str | Path, *, channels: int = 1) -> np.ndarray:
    """Read mono/stereo audio in the requested format and resample along time."""
    with sf.SoundFile(path) as source:
        if source.frames / source.samplerate > MAX_SECONDS:
            raise ValueError(f"Audio must be no longer than {MAX_SECONDS // 60} minutes.")
        if source.channels not in (1, 2):
            raise ValueError("Import a mono or stereo audio file.")
        samples = convert_audio(source.read(dtype="float32"), channels)
        source_rate = source.samplerate
    if source_rate != SAMPLE_RATE:
        divisor = math.gcd(source_rate, SAMPLE_RATE)
        samples = resample_poly(samples, SAMPLE_RATE // divisor, source_rate // divisor)
    if not np.isfinite(samples).all() or len(samples) > MAX_FRAMES:
        raise ValueError("The audio contains invalid samples or exceeds the song limit.")
    return np.asarray(samples, dtype=np.float32)


def save_song(song: Song, path: str | Path) -> None:
    """Atomically save metadata and lossless audio in one portable ZIP-based file."""
    if song.loop or song.punch:
        song.tape_range()
    lyrics = song.lyrics.encode("utf-8")
    if len(lyrics) > MAX_LYRICS_BYTES:
        raise ValueError("Songwriting text exceeds the 2 MB limit.")
    destination = Path(path)
    metadata = {"version": 5, "sample_rate": SAMPLE_RATE, "master": song.master, "bpm": song.bpm, "tracks": [],
                "bounce_history": [asdict(record) for record in song.bounce_history],
                "tape": {key: getattr(song, key) for key in
                         ("marker_a", "marker_b", "markers", "count_in", "loop", "punch", "tape_speed")},
                "sends": {"reverb_room": song.reverb_room, "delay_time": song.delay_time,
                          "delay_feedback": song.delay_feedback}}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, track in enumerate(song.tracks):
                channels = track.channels
                for samples in [track.audio, *(take.audio for take in track.takes)]:
                    if (audio_channels(samples) != channels or len(samples) > MAX_FRAMES
                            or not np.isfinite(samples).all()):
                        raise ValueError("Invalid track or take audio.")
                if len(track.effects) > MAX_EFFECTS:
                    raise ValueError(f"Each track supports up to {MAX_EFFECTS} effects.")
                effects_metadata = []
                for slot, effect in enumerate(track.effects):
                    effects_metadata.append(effect.to_dict())
                    if effect.state:
                        archive.writestr(f"effects/{index}-{slot}.bin", effect.state)
                metadata["tracks"].append({
                    "name": track.name, "volume": track.volume, "pan": track.pan,
                    "channels": channels,
                    "muted": track.muted, "solo": track.solo,
                    "effects": effects_metadata,
                    "takes": [take.name for take in track.takes],
                    "active_take_name": track.active_take_name,
                    "channel": {key: getattr(track, key) for key in
                                ("eq_low", "eq_mid", "eq_high", "reverb_send", "delay_send", "tape_drive", "wow_flutter")},
                })
                if len(track.takes) > MAX_TAKES:
                    raise ValueError("Too many alternate takes.")
                for take_index, take in enumerate(track.takes):
                    audio_file = BytesIO()
                    sf.write(audio_file, take.audio, SAMPLE_RATE, format="WAV", subtype="FLOAT")
                    archive.writestr(f"takes/{index}-{take_index}.wav", audio_file.getvalue())
                if len(track.audio):
                    audio_file = BytesIO()
                    sf.write(audio_file, track.audio, SAMPLE_RATE, format="WAV", subtype="FLOAT")
                    archive.writestr(f"track-{index}.wav", audio_file.getvalue())
            encoded = json.dumps(metadata).encode("utf-8")
            if len(encoded) > 2_000_000:
                raise ValueError("Song metadata is too large.")
            archive.writestr("song.json", encoded)
            archive.writestr("lyrics.txt", lyrics)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_lyrics(song: Song, path: str | Path) -> None:
    """Atomically write songwriting text as a standalone UTF-8 file."""
    encoded = song.lyrics.encode("utf-8")
    if len(encoded) > MAX_LYRICS_BYTES:
        raise ValueError("Songwriting text exceeds the 2 MB limit.")
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _level(value: object, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid mixer value in song file.")
    if not minimum <= value <= maximum:
        raise ValueError("Mixer value is outside the supported range.")
    return float(value)


def load_song(path: str | Path) -> Song:
    with zipfile.ZipFile(path) as archive:
        if archive.getinfo("song.json").file_size > 2_000_000:
            raise ValueError("Song metadata is too large.")
        metadata = json.loads(archive.read("song.json"))
        if metadata.get("version") not in (1, 2, 3, 4, 5) or metadata.get("sample_rate") != SAMPLE_RATE:
            raise ValueError("Unsupported song version or sample rate.")
        entries = metadata.get("tracks")
        if not isinstance(entries, list) or len(entries) != TRACK_COUNT:
            raise ValueError("A song must contain exactly eight tracks.")
        song = Song(master=_level(metadata["master"], 0, 1), bpm=int(_level(metadata.get("bpm", 100), 40, 240)))
        if "lyrics.txt" in archive.namelist():
            if archive.getinfo("lyrics.txt").file_size > MAX_LYRICS_BYTES:
                raise ValueError("Songwriting text exceeds the 2 MB limit.")
            song.lyrics = archive.read("lyrics.txt").decode("utf-8")
        tape = metadata.get("tape", {})
        if not isinstance(tape, dict):
            raise ValueError("Invalid tape settings.")
        for key, maximum in (("marker_a", MAX_FRAMES), ("marker_b", MAX_FRAMES), ("count_in", 16)):
            value = tape.get(key, 0)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("Invalid tape position or count-in.")
            setattr(song, key, value)
        for key in ("loop", "punch"):
            value = tape.get(key, False)
            if type(value) is not bool:
                raise ValueError("Invalid transport mode.")
            setattr(song, key, value)
        song.tape_speed = _level(tape.get("tape_speed", 1), 0.5, 2)
        sends = metadata.get("sends", {})
        if not isinstance(sends, dict):
            raise ValueError("Invalid shared effects settings.")
        song.reverb_room = _level(sends.get("reverb_room", 0.5), 0, 1)
        song.delay_time = _level(sends.get("delay_time", 0.3), 0.01, 2)
        song.delay_feedback = _level(sends.get("delay_feedback", 0.3), 0, 0.9)
        markers = tape.get("markers", {})
        if not isinstance(markers, dict):
            raise ValueError("Invalid location memories.")
        for name, frame in markers.items():
            if type(frame) is not int:
                raise ValueError("Invalid location memory position.")
            song.set_marker(name, frame)
        if song.loop or song.punch:
            song.tape_range()
        history = metadata.get("bounce_history", [])
        if not isinstance(history, list) or len(history) > MAX_BOUNCES:
            raise ValueError("Invalid bounce history.")
        song.bounce_history = [BounceRecord.from_dict(entry) for entry in history]
        for index, entry in enumerate(entries):
            track = song.tracks[index]
            channels = entry.get("channels", 1) if metadata["version"] == 5 else 1
            if type(channels) is not int or channels not in (1, 2):
                raise ValueError("Invalid track channel count.")
            if channels == 2:
                track.audio = np.zeros((0, 2), dtype=np.float32)
            if not isinstance(entry["name"], str) or len(entry["name"]) > 64:
                raise ValueError("Invalid track name.")
            track.name = entry["name"]
            track.active_take_name = entry.get("active_take_name", "Current")
            if not isinstance(track.active_take_name, str) or not track.active_take_name.strip() or len(track.active_take_name) > 64:
                raise ValueError("Invalid active take name.")
            track.volume = _level(entry["volume"], 0, 1)
            track.pan = _level(entry["pan"], -1, 1)
            if not isinstance(entry["muted"], bool) or not isinstance(entry["solo"], bool):
                raise ValueError("Invalid mute or solo setting.")
            track.muted, track.solo = entry["muted"], entry["solo"]
            channel = entry.get("channel", {})
            if not isinstance(channel, dict):
                raise ValueError("Invalid channel settings.")
            for key, minimum, maximum in (("eq_low", -12, 12), ("eq_mid", -12, 12), ("eq_high", -12, 12),
                                           ("reverb_send", 0, 1), ("delay_send", 0, 1),
                                           ("tape_drive", 0, 18), ("wow_flutter", 0, 1)):
                setattr(track, key, _level(channel.get(key, 0), minimum, maximum))
            effects_metadata = entry.get("effects", [])
            if not isinstance(effects_metadata, list) or len(effects_metadata) > MAX_EFFECTS:
                raise ValueError("Invalid effects rack.")
            for slot, effect_data in enumerate(effects_metadata):
                state_path = f"effects/{index}-{slot}.bin"
                state = b""
                if state_path in archive.namelist():
                    if archive.getinfo(state_path).file_size > MAX_STATE_BYTES:
                        raise ValueError("Plugin state exceeds the 16 MB per-effect limit.")
                    state = archive.read(state_path)
                track.effects.append(Effect.from_dict(effect_data, state))
            filename = f"track-{index}.wav"
            if filename in archive.namelist():
                track.audio = _read_project_audio(archive, filename, channels)
            takes = entry.get("takes", [])
            if not isinstance(takes, list) or len(takes) > MAX_TAKES:
                raise ValueError("Invalid take bank.")
            for take_index, name in enumerate(takes):
                if not isinstance(name, str) or not name.strip() or len(name) > 64:
                    raise ValueError("Invalid take name.")
                track.takes.append(Take(name, _read_project_audio(archive, f"takes/{index}-{take_index}.wav", channels)))
    return song


def _read_project_audio(archive: zipfile.ZipFile, filename: str, channels: int = 1) -> np.ndarray:
    if archive.getinfo(filename).file_size > MAX_FRAMES * 4 * channels + 4096:
        raise ValueError("Track exceeds the song limit.")
    with sf.SoundFile(BytesIO(archive.read(filename))) as source:
        if source.samplerate != SAMPLE_RATE or source.channels != channels or source.frames > MAX_FRAMES:
            raise ValueError("Invalid track audio format.")
        samples = source.read(dtype="float32")
    if not np.isfinite(samples).all():
        raise ValueError("Invalid audio samples.")
    return samples


def export_mix(song: Song, path: str | Path, *, start: int = 0, end: int | None = None,
               include_tails: bool = True) -> bool:
    """Write a stereo WAV in blocks; report whether the mix needed clipping."""
    if not song.length:
        raise ValueError("Record or import some audio before exporting.")
    boundary = song.length if end is None else end
    if not 0 <= start < boundary <= MAX_FRAMES:
        raise ValueError("Choose a nonempty export range within the tape.")
    render_song = song if end is None else replace(song, tracks=[
        replace(track, audio=track.audio[:boundary], takes=[]) for track in song.tracks])
    render_song.prepare_effects()
    finish = max(boundary, render_song.playback_length) if include_tails else boundary
    total = math.ceil((finish - start) / song.tape_speed)
    clipped = False
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".wav", delete=False) as handle:
            temporary = Path(handle.name)
        with sf.SoundFile(temporary, "w", samplerate=SAMPLE_RATE, channels=2, subtype="PCM_24") as output:
            for offset in range(0, total, 8192):
                block = render_song.mix_tape(start + offset * song.tape_speed, min(8192, total - offset))
                clipped = clipped or bool(np.any(np.abs(block) > 1))
                output.write(np.clip(block, -1, 1))
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return clipped


def export_stems(song: Song, directory: str | Path, *, start: int = 0, end: int | None = None,
                 include_tails: bool = True) -> tuple[list[Path], bool]:
    destination = Path(directory)
    selected = [(index, track) for index, track in enumerate(song.tracks) if len(track.audio)]
    if not selected:
        raise ValueError("Record or import audio before exporting stems.")
    paths = [destination / f"{index + 1:02d}-{''.join(character if character.isalnum() or character in '-_' else '_' for character in track.name)}.wav"
             for index, track in selected]
    if any(path.exists() for path in paths):
        raise ValueError("Stem files already exist. Choose an empty destination folder.")
    boundary = song.length if end is None else end
    clipped = False
    with tempfile.TemporaryDirectory(dir=destination) as staging:
        for (index, track), path in zip(selected, paths):
            tracks = [Track(other.name) for other in song.tracks]
            tracks[index] = replace(track, muted=False, solo=False, takes=[])
            stem = replace(song, tracks=tracks)
            clipped = export_mix(stem, Path(staging) / path.name, start=start, end=boundary,
                                 include_tails=include_tails) or clipped
        for path in paths:
            (Path(staging) / path.name).replace(path)
    return paths, clipped