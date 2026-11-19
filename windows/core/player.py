"""
PulseBox Player Engine - VLC Backend
Wraps python-vlc for media playback with a clean API.
"""

import threading
import time
from pathlib import Path


# Windows audio outputs VLC can be forced to with the "vlc_aout" config key.
# "" lets VLC choose (WASAPI on any current Windows), which is what you want
# unless a particular device misbehaves. The config stores "auto" for that.
WINDOWS_AOUTS = ("", "mmdevice", "directsound", "waveout")


class PlayerState:
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class JukeboxPlayer:
    """
    VLC-backed audio player.

    Events fired (callbacks registered via on_event):
        track_started(track_info)
        track_ended()
        state_changed(state)
        position_changed(seconds, duration)
        queue_empty()
    """

    def __init__(self):
        self._vlc_available = False
        self._instance = None
        self._player = None
        self._media_list_player = None
        self._media_list = None

        self._queue: list[dict] = []       # list of track dicts
        self._current_index: int = -1
        self._state: str = PlayerState.STOPPED
        self._volume: int = 80            # 0-100
        # The Pi build drove the HiFiBerry's hardware mixer here. On Windows
        # the volume is VLC's own (per-app) level, so this stays empty; it is
        # kept because the rest of the player still branches on it.
        self._hw_ctl: str = ""
        self._callbacks: dict[str, list] = {}
        self._position_thread: threading.Thread | None = None
        self._running = False

        # The Pi build set this when ALSA saw no sound card. Windows always
        # routes to its default device, so it is never raised here; the
        # attribute stays because the UI reads it.
        self.no_audio_device = False

        self._init_vlc()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_vlc(self):
        try:
            import vlc  # noqa: F401
            self._vlc = vlc
            # Try audio outputs in order until one works
            # Read preferred output from ~/.pulsebox_config.json if set
            import json, os
            cfg = {}
            try:
                with open(os.path.expanduser("~/.pulsebox_config.json"),
                          encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
            preferred = cfg.get("vlc_aout", "")

            self._instance = None
            self._player   = None

            # Let VLC pick its Windows output (WASAPI) unless one is forced
            # with the "vlc_aout" config key; fall back through the others.
            aout_list = []
            if preferred in WINDOWS_AOUTS and preferred:
                aout_list.append(preferred)
            for a in WINDOWS_AOUTS:
                if a not in aout_list:
                    aout_list.append(a)

            try:
                self._volume = max(0, min(100, int(cfg.get("volume", 80))))
            except (TypeError, ValueError):
                pass

            for aout in aout_list:
                try:
                    args = ["--quiet"]
                    if aout:
                        args.append(f"--aout={aout}")
                    inst = vlc.Instance(*args)
                    plyr = inst.media_player_new() if inst else None
                    if plyr is not None:
                        self._instance = inst
                        self._player   = plyr
                        print(f"[Player] VLC initialised "
                              f"(aout={aout or 'default'})")
                        break
                except Exception as e:
                    print(f"[Player] aout={aout or 'default'} failed: {e}")
            if self._player is None:
                raise RuntimeError("No working VLC audio output found")
            self._vlc_available = True
            self._attach_vlc_events()

            # Send audio to a specific Windows playback device if one was
            # chosen in Settings; otherwise the Windows default device is used.
            # Deferred a moment: VLC fills its device list asynchronously.
            wanted = cfg.get("vlc_audio_device", "")
            if wanted:
                threading.Timer(1.5, self._apply_saved_device,
                                args=(wanted,)).start()
        except (ImportError, Exception) as e:
            print(f"[Player] VLC not available: {e}. Running in demo mode.")
            self._vlc_available = False
            self.vlc_error = str(e)

    def _attach_vlc_events(self):
        em = self._player.event_manager()
        # IMPORTANT: VLC event callbacks must NEVER call VLC methods directly
        # (deadlock). Dispatch all handling to a separate thread.
        em.event_attach(self._vlc.EventType.MediaPlayerEndReached,
                        lambda e: threading.Thread(
                            target=self._on_track_end, daemon=True).start())
        em.event_attach(self._vlc.EventType.MediaPlayerPlaying,
                        lambda e: threading.Thread(
                            target=self._on_playing, daemon=True).start())
        em.event_attach(self._vlc.EventType.MediaPlayerPaused,
                        lambda e: threading.Thread(
                            target=lambda: self._emit(
                                "state_changed", PlayerState.PAUSED),
                            daemon=True).start())
        em.event_attach(self._vlc.EventType.MediaPlayerStopped,
                        lambda e: threading.Thread(
                            target=lambda: self._emit(
                                "state_changed", PlayerState.STOPPED),
                            daemon=True).start())

    # ------------------------------------------------------------------
    # Output devices (Windows playback devices, as VLC sees them)
    # ------------------------------------------------------------------

    def list_output_devices(self) -> list[tuple[str, str]]:
        """[(device_id, description)] for the active VLC audio output."""
        if not self._vlc_available or self._player is None:
            return []
        devices = []
        try:
            head = self._player.audio_output_device_enum()
            if head:
                node = head
                while node:
                    d = node.contents
                    dev_id = (d.device or b"").decode("utf-8", "replace")
                    desc   = (d.description or b"").decode("utf-8", "replace")
                    if dev_id:
                        devices.append((dev_id, desc or dev_id))
                    node = d.next
                self._vlc.libvlc_audio_output_device_list_release(head)
        except Exception as e:
            print(f"[Player] Could not list audio devices: {e}")
        return devices

    def _apply_saved_device(self, wanted: str):
        if any(dev_id == wanted for dev_id, _ in self.list_output_devices()):
            self.set_output_device(wanted)
        else:
            print("[Player] Saved audio device not present — "
                  "using the Windows default.")

    def set_output_device(self, device_id: str) -> bool:
        """Switch playback device live. '' = Windows default device."""
        if not self._vlc_available or self._player is None:
            return False
        try:
            self._player.audio_output_device_set(None, device_id or None)
            print(f"[Player] Audio device: {device_id or 'Windows default'}")
            return True
        except Exception as e:
            print(f"[Player] Could not set audio device: {e}")
            return False

    # ------------------------------------------------------------------
    # Public Playback API
    # ------------------------------------------------------------------

    def play(self, track: dict | None = None):
        """Play a specific track dict, or resume current."""
        if track:
            self._play_track(track)
        elif self._state == PlayerState.PAUSED and self._vlc_available:
            self._player.play()
        elif self._queue and self._current_index >= 0:
            self._play_track(self._queue[self._current_index])

    def play_mrl(self, mrl: str, options: list | None = None,
                 meta: dict | None = None):
        """
        Play an arbitrary VLC media resource locator (MRL) through the shared
        player instance — e.g. "cdda:///D:/" for an audio CD. Unlike
        _play_track(), this does NOT require a real file on disk, so it works
        for optical-disc and network MRLs. Per-media options (e.g.
        ":cdda-track=3") are applied before playback. Emits track_started with
        `meta` so the player bar updates. Audio follows the same volume path
        as normal playback.
        """
        if not self._vlc_available or not self._player:
            return False
        try:
            self._player.stop()
            media = self._instance.media_new(mrl)
            for opt in (options or []):
                media.add_option(opt)
            self._player.set_media(media)
            self._player.play()
            if not self._hw_ctl:
                self._player.audio_set_volume(self._volume)
            self._state = PlayerState.PLAYING
            self._emit("track_started", meta or {"title": mrl})
            self._start_position_thread()
            return True
        except Exception as e:
            print(f"[Player] play_mrl failed for {mrl}: {e}")
            return False

    def pause(self):
        if self._vlc_available:
            self._player.pause()
        self._state = PlayerState.PAUSED
        self._emit("state_changed", PlayerState.PAUSED)

    def stop(self):
        if self._vlc_available:
            self._player.stop()
        self._state = PlayerState.STOPPED
        self._emit("state_changed", PlayerState.STOPPED)

    def next_track(self):
        if self._current_index < len(self._queue) - 1:
            self._current_index += 1
            self._play_track(self._queue[self._current_index])

    def previous_track(self):
        if self._current_index > 0:
            self._current_index -= 1
            self._play_track(self._queue[self._current_index])

    def seek(self, seconds: float):
        if self._vlc_available:
            duration = self.get_duration()
            if duration > 0:
                self._player.set_position(seconds / duration)

    def set_volume(self, vol: int):
        self._volume = max(0, min(100, vol))
        if self._vlc_available and self._player:
            self._player.audio_set_volume(self._volume)

    def get_volume(self) -> int:
        return self._volume

    def get_position(self) -> float:
        """Returns current position in seconds."""
        if self._vlc_available and self._state == PlayerState.PLAYING:
            ms = self._player.get_time()
            return ms / 1000 if ms >= 0 else 0.0
        return 0.0

    def get_duration(self) -> float:
        """Returns duration in seconds."""
        if self._vlc_available:
            ms = self._player.get_length()
            return ms / 1000 if ms > 0 else 0.0
        return 0.0

    def get_state(self) -> str:
        return self._state

    # ------------------------------------------------------------------
    # Queue Management
    # ------------------------------------------------------------------

    def set_queue(self, tracks: list[dict], start_index: int = 0):
        """Replace queue and start playing from start_index."""
        self._queue = list(tracks)
        self._current_index = start_index
        if self._queue:
            self._play_track(self._queue[self._current_index])

    def add_to_queue(self, track: dict):
        self._queue.append(track)

    def clear_queue(self):
        self.stop()
        self._queue = []
        self._current_index = -1

    def get_queue(self) -> list[dict]:
        return list(self._queue)

    def get_current_track(self) -> dict | None:
        if 0 <= self._current_index < len(self._queue):
            return self._queue[self._current_index]
        return None

    def get_current_index(self) -> int:
        return self._current_index

    # ------------------------------------------------------------------
    # Event System
    # ------------------------------------------------------------------

    def on_event(self, event: str, callback):
        """Register a callback for a named event."""
        self._callbacks.setdefault(event, []).append(callback)

    def _emit(self, event: str, *args):
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception as e:
                print(f"[Player] Callback error ({event}): {e}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _play_track(self, track: dict):
        path = track.get("path", "")
        # Previously, if VLC hadn't loaded or the file was missing, this
        # skipped playback but still reported PLAYING: the pause icon showed,
        # the time stayed at 0:00 and there was no sound and no error. Now
        # it stops and says why.
        if not self._vlc_available:
            reason = getattr(self, "vlc_error", "") or "unknown error"
            msg = f"No sound: VLC did not load ({reason})"
        elif not Path(path).exists():
            msg = f"File not found: {path}"
        else:
            msg = ""
        if msg:
            print(f"[Player] {msg}")
            self._state = PlayerState.STOPPED
            self._emit("playback_error", msg)
            self._emit("state_changed", PlayerState.STOPPED)
            return
        self._player.stop()              # ensure clean start at position 0
        media = self._instance.media_new(path)
        self._player.set_media(media)
        self._player.play()
        if not self._hw_ctl:
            self._player.audio_set_volume(self._volume)
        self._state = PlayerState.PLAYING
        self._emit("track_started", track)
        self._start_position_thread()

    def _on_track_end(self):
        # Small delay to let VLC fully release the previous media
        time.sleep(0.3)
        self._emit("track_ended")
        if self._current_index < len(self._queue) - 1:
            self._current_index += 1
            self._play_track(self._queue[self._current_index])
        else:
            self._state = PlayerState.STOPPED
            self._emit("queue_empty")

    def _on_playing(self):
        self._state = PlayerState.PLAYING
        self._emit("state_changed", PlayerState.PLAYING)

    def _start_position_thread(self):
        if self._position_thread and self._position_thread.is_alive():
            return
        self._running = True
        self._position_thread = threading.Thread(
            target=self._position_loop, daemon=True)
        self._position_thread.start()

    def _position_loop(self):
        while self._running and self._state == PlayerState.PLAYING:
            pos = self.get_position()
            dur = self.get_duration()
            self._emit("position_changed", pos, dur)
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # Graphic Equaliser (VLC libvlc_equalizer)
    # ------------------------------------------------------------------

    # 10-band frequencies matching VLC's built-in EQ
    EQ_BANDS = [31.25, 62.5, 125.0, 250.0, 500.0,
                1000.0, 2000.0, 4000.0, 8000.0, 16000.0]

    # VLC built-in presets (name -> (preamp_db, [band0..band9]))
    EQ_PRESETS = {
        "Flat":        (0.0, [0]*10),
        "Classical":   (0.0, [-1,-1, 0, 0, 0, 0,-2,-2,-2,-3]),
        "Club":        (0.0, [0, 0, 5, 3, 3, 3, 1, 0, 0, 0]),
        "Dance":       (0.0, [8, 6, 1, 0, 0,-3,-4,-4, 0, 0]),
        "Full Bass":   (0.0, [7, 7, 7, 3, 1,-3,-5,-5,-5,-5]),
        "Full Treble": (0.0, [-8,-8,-8,-2, 2, 8,10,10,10,10]),
        "Live":        (0.0, [-4, 0, 3, 3, 3, 3, 2, 1, 1, 1]),
        "Party":       (0.0, [ 6, 4, 0, 0, 0, 0, 0, 0, 4, 6]),
        "Pop":         (0.0, [-1, 3, 5, 5, 3,-1,-2,-2,-1,-1]),
        "Reggae":      (0.0, [ 0, 0, 0,-3, 0, 4, 4, 0, 0, 0]),
        "Rock":        (0.0, [ 5, 3,-3,-4, 1, 5, 6, 5, 3, 1]),
        "Ska":         (0.0, [-1,-3,-3, 0, 2, 3, 5, 5, 6, 6]),
        "Soft":        (0.0, [ 3, 2, 1, 0,-1, 0, 2, 3, 4, 5]),
        "Techno":      (0.0, [ 5, 4, 0,-3, 0, 5, 6, 5, 1, 0]),
    }

    def eq_set_band(self, band: int, value_db: float):
        """Set a single EQ band (-20 to +20 dB). band 0-9."""
        if not self._vlc_available or self._player is None:
            return
        try:
            eq = self._vlc.libvlc_audio_equalizer_new()
            if eq is None:
                return
            # Copy current bands first
            for i in range(10):
                cur = self._vlc.libvlc_audio_equalizer_get_amp_at_index(eq, i)
                self._vlc.libvlc_audio_equalizer_set_amp_at_index(eq, cur, i)
            self._vlc.libvlc_audio_equalizer_set_amp_at_index(
                eq, value_db, band)
            self._player.set_equalizer(eq)
            self._vlc.libvlc_audio_equalizer_release(eq)
        except Exception as e:
            print(f"[Player] EQ band error: {e}")

    def eq_apply(self, preamp_db: float, bands: list[float]):
        """Apply a full EQ setting — preamp + all 10 bands."""
        if not self._vlc_available or self._player is None:
            return
        try:
            eq = self._vlc.libvlc_audio_equalizer_new()
            if eq is None:
                return
            self._vlc.libvlc_audio_equalizer_set_preamp(eq, preamp_db)
            for i, val in enumerate(bands[:10]):
                self._vlc.libvlc_audio_equalizer_set_amp_at_index(eq, val, i)
            self._player.set_equalizer(eq)
            self._vlc.libvlc_audio_equalizer_release(eq)
            print(f"[Player] EQ applied: preamp={preamp_db:.1f} "
                  f"bands={[round(b,1) for b in bands]}")
        except Exception as e:
            print(f"[Player] EQ apply error: {e}")

    def eq_disable(self):
        """Disable the equaliser."""
        if not self._vlc_available or self._player is None:
            return
        try:
            self._player.set_equalizer(None)
        except Exception as e:
            print(f"[Player] EQ disable error: {e}")

    def shutdown(self):
        self._running = False
        self.stop()
