# SPDX-License-Identifier: GPL-3.0-or-later
"""In-app audio CD playback via GStreamer (cdio / cdparanoia).

Prefer ``cdiocddasrc`` for listening: it streams with low latency and avoids
the heavy error-correction buffering of archival ``cdparanoiasrc``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum, auto

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, float, float], None]  # track, position_s, duration_s
StateCb = Callable[['PlayState', int | None], None]
ErrorCb = Callable[[str], None]
EosCb = Callable[[int], None]


class PlayState(Enum):
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


def gstreamer_available() -> bool:
    """True when Gst + a CDDA source element can be loaded."""
    try:
        import gi

        gi.require_version('Gst', '1.0')
        from gi.repository import Gst

        Gst.init(None)
        reg = Gst.Registry.get()
        return (
            reg.find_feature('cdiocddasrc', Gst.ElementFactory.__gtype__) is not None
            or reg.find_feature('cdparanoiasrc', Gst.ElementFactory.__gtype__)
            is not None
        )
    except Exception:
        return False


def _pick_cdda_factory() -> str | None:
    """Prefer cdio for smooth listening; fall back to cdparanoia."""
    import gi

    gi.require_version('Gst', '1.0')
    from gi.repository import Gst

    Gst.init(None)
    reg = Gst.Registry.get()
    for name in ('cdiocddasrc', 'cdparanoiasrc'):
        if reg.find_feature(name, Gst.ElementFactory.__gtype__) is not None:
            return name
    return None


class DiscPlayer:
    """Play one CD track at a time from an optical device."""

    def __init__(self) -> None:
        self._pipeline = None
        self._bus = None
        self._bus_handler = None
        self._tick_id: int | None = None
        self._device = '/dev/sr0'
        self._track: int | None = None
        self._tracks: list[int] = []
        # TOC lengths (seconds) keyed by track number — CDDA often lacks
        # a reliable Gst duration query until well into playback.
        self._durations: dict[int, float] = {}
        self._state = PlayState.STOPPED
        self.on_state: StateCb | None = None
        self.on_progress: ProgressCb | None = None
        self.on_error: ErrorCb | None = None
        self.on_eos: EosCb | None = None

    @property
    def state(self) -> PlayState:
        return self._state

    @property
    def track(self) -> int | None:
        return self._track

    @property
    def available(self) -> bool:
        return gstreamer_available()

    def set_playlist(
        self,
        tracks: list[int],
        *,
        device: str,
        durations: dict[int, float] | None = None,
    ) -> None:
        self._tracks = sorted(n for n in tracks if n > 0)
        self._device = device
        if durations is not None:
            self._durations = {
                int(n): float(s) for n, s in durations.items() if int(n) > 0 and float(s) > 0
            }

    def play(self, track: int | None = None) -> bool:
        """Start or resume *track* (defaults to current / first)."""
        if not self.available:
            if self.on_error:
                self.on_error(
                    'CD playback needs GStreamer with cdio or cdparanoia plugins'
                )
            return False

        target = track
        if target is None:
            target = self._track if self._track is not None else (
                self._tracks[0] if self._tracks else None
            )
        if target is None:
            if self.on_error:
                self.on_error('No track to play')
            return False

        if (
            self._state is PlayState.PAUSED
            and self._track == target
            and self._pipeline is not None
        ):
            return self._set_pipeline_state(PlayState.PLAYING)

        self.stop()
        if not self._build_pipeline(target):
            return False
        self._track = target
        return self._set_pipeline_state(PlayState.PLAYING)

    def pause(self) -> bool:
        if self._state is not PlayState.PLAYING:
            return False
        return self._set_pipeline_state(PlayState.PAUSED)

    def toggle(self) -> bool:
        if self._state is PlayState.PLAYING:
            return self.pause()
        return self.play(self._track)

    def stop(self, *, wait: bool = True) -> None:
        """Stop playback and optionally wait until the CDDA device is released.

        GStreamer state changes to NULL are asynchronous. Starting a rip
        before the pipeline finishes shutting down races cdparanoia for
        ``/dev/srN`` and produces truncated WAVs / failed secure extracts.
        """
        self._stop_tick()
        self._disconnect_bus()
        if self._pipeline is not None:
            pipeline = self._pipeline
            self._pipeline = None
            try:
                from gi.repository import Gst

                pipeline.set_state(Gst.State.NULL)
                if wait:
                    # Block until NULL (or timeout) so the optical device
                    # is free for cdparanoia.
                    pipeline.get_state(5 * Gst.SECOND)
            except Exception:
                pass
        was = self._state
        self._state = PlayState.STOPPED
        if was is not PlayState.STOPPED and self.on_state:
            self.on_state(PlayState.STOPPED, self._track)

    def next_track(self) -> bool:
        if not self._tracks:
            return False
        current = self._track or self._tracks[0]
        try:
            idx = self._tracks.index(current)
        except ValueError:
            idx = -1
        # Wrap last → first so Next never dead-ends mid-session.
        if idx + 1 >= len(self._tracks):
            return self.play(self._tracks[0])
        return self.play(self._tracks[idx + 1])

    def previous_track(self) -> bool:
        if not self._tracks:
            return False
        # Restart current track if >3s in; otherwise go to previous.
        position = self._query_position()
        if position is not None and position > 3.0 and self._track is not None:
            return self.play(self._track)
        current = self._track or self._tracks[0]
        try:
            idx = self._tracks.index(current)
        except ValueError:
            idx = 0
        if idx <= 0:
            return self.play(self._tracks[0])
        return self.play(self._tracks[idx - 1])

    def seek(self, position_s: float) -> bool:
        """Seek within the current track to *position_s* seconds."""
        if self._pipeline is None or self._state is PlayState.STOPPED:
            return False
        from gi.repository import Gst

        # Wait briefly for PLAYING/PAUSED — seeks before preroll often fail.
        try:
            _ret, state, _pending = self._pipeline.get_state(200 * Gst.MSECOND)
        except Exception:
            state = None
        if state not in (Gst.State.PLAYING, Gst.State.PAUSED):
            return False

        duration = self._query_duration()
        target = max(0.0, float(position_s))
        if duration is not None and duration > 0:
            target = min(target, max(0.0, duration - 0.05))
        ns = int(target * Gst.SECOND)
        # CDDA is raw PCM: KEY_UNIT often makes seek_simple return False.
        flag_sets = (
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            Gst.SeekFlags.FLUSH,
        )
        for flags in flag_sets:
            try:
                if self._pipeline.seek_simple(Gst.Format.TIME, flags, ns):
                    return True
            except Exception:
                pass
            try:
                if self._pipeline.seek(
                    1.0,
                    Gst.Format.TIME,
                    flags,
                    Gst.SeekType.SET,
                    ns,
                    Gst.SeekType.NONE,
                    -1,
                ):
                    return True
            except Exception:
                pass
        return False

    def position(self) -> float | None:
        return self._query_position()

    def duration(self) -> float | None:
        return self._query_duration()

    def _build_pipeline(self, track: int) -> bool:
        import gi

        gi.require_version('Gst', '1.0')
        from gi.repository import Gst

        Gst.init(None)
        factory = _pick_cdda_factory()
        if factory is None:
            if self.on_error:
                self.on_error('No GStreamer CDDA source (cdio/cdparanoia) found')
            return False

        pipeline = Gst.Pipeline.new('ready2rip-cdda')
        src = Gst.ElementFactory.make(factory, 'cdda')
        # Short queue absorbs drive jitter without long start delay.
        queue = Gst.ElementFactory.make('queue', 'queue')
        convert = Gst.ElementFactory.make('audioconvert', 'convert')
        resample = Gst.ElementFactory.make('audioresample', 'resample')
        sink = Gst.ElementFactory.make('autoaudiosink', 'sink')
        if not all((src, queue, convert, resample, sink)):
            if self.on_error:
                self.on_error('Could not create GStreamer playback elements')
            return False

        src.set_property('device', self._device)
        try:
            src.set_property('track', int(track))
        except Exception:
            src.set_property('track', track)

        # Listening mode: skip archival paranoia / prefer higher read speed.
        if factory == 'cdparanoiasrc':
            for key, value in (
                ('paranoia-mode', 0),
                ('read-speed', -1),
            ):
                try:
                    src.set_property(key, value)
                except Exception:
                    pass
        elif factory == 'cdiocddasrc':
            for key, value in (('read-speed', -1),):
                try:
                    src.set_property(key, value)
                except Exception:
                    pass

        # ~0.5s of audio — enough to avoid underruns, quick to fill.
        try:
            queue.set_property('max-size-time', 500 * Gst.MSECOND)
            queue.set_property('max-size-buffers', 0)
            queue.set_property('max-size-bytes', 0)
        except Exception:
            pass

        for el in (src, queue, convert, resample, sink):
            pipeline.add(el)
        if not (
            src.link(queue)
            and queue.link(convert)
            and convert.link(resample)
            and resample.link(sink)
        ):
            if self.on_error:
                self.on_error('Could not link CD playback pipeline')
            return False

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_handler = bus.connect('message', self._on_bus_message)
        self._bus = bus
        self._pipeline = pipeline
        return True

    def _disconnect_bus(self) -> None:
        if self._bus is None:
            return
        try:
            if self._bus_handler is not None:
                self._bus.disconnect(self._bus_handler)
        except Exception:
            pass
        try:
            self._bus.remove_signal_watch()
        except Exception:
            pass
        self._bus_handler = None
        self._bus = None

    def _set_pipeline_state(self, state: PlayState) -> bool:
        from gi.repository import Gst

        if self._pipeline is None:
            return False
        gst_state = {
            PlayState.PLAYING: Gst.State.PLAYING,
            PlayState.PAUSED: Gst.State.PAUSED,
            PlayState.STOPPED: Gst.State.NULL,
        }[state]
        result = self._pipeline.set_state(gst_state)
        if result == Gst.StateChangeReturn.FAILURE:
            if self.on_error:
                self.on_error('Playback failed to start')
            self.stop()
            return False
        self._state = state
        if state is PlayState.PLAYING:
            self._start_tick()
        else:
            self._stop_tick()
        if self.on_state:
            self.on_state(state, self._track)
        return True

    def _on_bus_message(self, _bus, message) -> None:
        from gi.repository import Gst

        t = message.type
        if t == Gst.MessageType.EOS:
            finished = self._track
            if finished is not None and self.on_eos:
                self.on_eos(finished)
            elif not self.next_track():
                self.stop()
            return
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            log.debug('Playback error: %s (%s)', err, debug)
            msg = err.message if err is not None else 'Playback error'
            self.stop()
            if self.on_error:
                self.on_error(msg)

    def _start_tick(self) -> None:
        if self._tick_id is not None:
            return
        from gi.repository import GLib

        self._tick_id = GLib.timeout_add(250, self._on_tick)

    def _stop_tick(self) -> None:
        if self._tick_id is None:
            return
        from gi.repository import GLib

        GLib.source_remove(self._tick_id)
        self._tick_id = None

    def _on_tick(self) -> bool:
        if self._state is not PlayState.PLAYING or self._track is None:
            return True
        pos = self._query_position()
        dur = self._query_duration()
        if pos is not None and self.on_progress:
            self.on_progress(self._track, pos, dur if dur is not None else 0.0)
        return True

    def _toc_duration(self, track: int | None = None) -> float | None:
        t = self._track if track is None else track
        if t is None:
            return None
        value = self._durations.get(int(t))
        return float(value) if value is not None and value > 0 else None

    def _query_position(self) -> float | None:
        return self._query_time('position')

    def _query_duration(self) -> float | None:
        gst = self._query_time('duration')
        if gst is not None and gst > 0:
            return gst
        return self._toc_duration()

    def _query_time(self, which: str) -> float | None:
        if self._pipeline is None:
            return None
        from gi.repository import Gst

        try:
            if which == 'position':
                ok, value = self._pipeline.query_position(Gst.Format.TIME)
            else:
                ok, value = self._pipeline.query_duration(Gst.Format.TIME)
            if not ok or value < 0:
                return None
            return float(value) / Gst.SECOND
        except Exception:
            return None
