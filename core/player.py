"""
PulseBox Player Engine - VLC Backend
Wraps python-vlc for media playback with a clean API.
"""

import threading
import time
from pathlib import Path


def _detect_alsa_card() -> tuple[str, str]:
    """
    Find a HiFiBerry / DAC audio card. Returns (device, card_name):
      device    -> ALSA device string for VLC (plughw:CARD=…), or ""
      card_name -> the ALSA card name for amixer, or ""
    Returns ("", "") if no dedicated DAC is found (ALSA "default" is used).
    """
    import subprocess
    import re
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return ("", "")
    for line in out.splitlines():
        m = re.match(r"card (\d+): (\S+)", line)
        if not m:
            continue
        if re.search(r"hifiberry|iqaudio|dacplus|\bdac\b|audioinjector|"
                     r"allo|justboom|wm8804", line, re.I) and \
           not re.search(r"hdmi|vc4|bcm2835", line, re.I):
            name = m.group(2)
            return (f"plughw:CARD={name}", name)
    return ("", "")


def _find_hw_control(card_name: str) -> str:
    """
    Return the DAC's master volume control name (Digital on HiFiBerry, else
    PCM/Master/…), or "" if none. Does not change anything.
    """
    import subprocess
    import re
    card = card_name or "0"
    controls = []
    try:
        out = subprocess.run(["amixer", "-c", card, "scontrols"],
                             capture_output=True, text=True, timeout=5).stdout
        controls = re.findall(r"Simple mixer control '([^']+)'", out)
    except Exception:
        pass
    for name in ("Digital", "PCM", "Master", "Playback", "Speaker"):
        if name in controls:
            return name
    return ""


def _audio_card_present() -> bool:
    """
    True if ALSA sees at least one playback sound card. Used so PulseBox can
    stay up and show a clear "no audio device" state instead of thrashing when
    the DAC is missing/faulty.
    """
    import subprocess
    try:
        r = subprocess.run(["aplay", "-l"], capture_output=True,
                           text=True, timeout=5)
        return "card " in (r.stdout or "")
    except Exception:
        # If aplay isn't available we can't tell — assume present, don't block.
        return True


def _amixer_set_scaled(card: str, ctl: str, vol: int) -> bool:
    """
    Set an ALSA control from a 0-100 slider value where the slider is a
    percentage of maximum **amplitude** (dB = 20·log10(vol/100)). This is what
    people expect from a volume control: 100% = full (0 dB), 80% = -1.9 dB
    (loud), 50% = -6 dB (clearly audible), 25% = -12 dB. It keeps the whole
    slider usable on the pcm512x 'Digital' control, whose raw-percent scale is
    otherwise so wide that most of the slider is inaudible. Falls back to plain
    percent if the control doesn't expose a dB scale.
    """
    import subprocess
    import math
    card = card or "0"
    try:
        if vol <= 0:
            subprocess.run(["amixer", "-c", card, "sset", ctl, "mute"],
                           capture_output=True, timeout=5)
            return True
        db = 20.0 * math.log10(vol / 100.0)      # 100->0dB, 80->-1.9dB, 50->-6dB
        r = subprocess.run(["amixer", "-c", card, "sset", ctl,
                            f"{db:.1f}dB", "unmute"],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            return True
        # Fallback: plain percent if this control has no dB scale
        subprocess.run(["amixer", "-c", card, "sset", ctl, f"{vol}%", "unmute"],
                       capture_output=True, timeout=5)
        return True
    except Exception:
        return False


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
        self._hw_ctl: str = ""            # ALSA mixer control for master volume
        self._hw_card: str = ""           # ALSA card for that control
        self._callbacks: dict[str, list] = {}
        self._position_thread: threading.Thread | None = None
        self._running = False

        # Stays up gracefully when the DAC is missing/faulty: the UI runs, but
        # audio is disabled and this flag lets the app show a clear message.
        self.no_audio_device = not _audio_card_present()
        if self.no_audio_device:
            print("[Player] WARNING: no audio device detected. UI will run "
                  "but audio is disabled until a sound card appears.")

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
                cfg = json.load(open(
                    os.path.expanduser("~/.pulsebox_config.json")))
            except Exception:
                pass
            preferred = cfg.get("vlc_aout", "")

            self._instance = None
            self._player   = None

            # ALSA is the default output. Talking to the sound card directly
            # is the most reliable path on a dedicated Pi with a HiFiBerry and
            # sidesteps PipeWire routing problems. pipewire/pulse remain as
            # fallbacks and can be forced with the "vlc_aout" config key.
            aout_list = []
            if preferred:
                aout_list.append(preferred)
            for a in ("alsa", "pipewire", "pulse", ""):
                if a not in aout_list:
                    aout_list.append(a)

            # ALSA device: explicit config wins, otherwise auto-detect a
            # HiFiBerry/DAC card, otherwise fall back to ALSA "default".
            alsa_device = cfg.get("vlc_alsa_device", "")
            alsa_card = ""
            detected_dev, alsa_card = _detect_alsa_card()
            if not alsa_device:
                alsa_device = detected_dev
                if alsa_device:
                    print(f"[Player] Auto-detected ALSA device: {alsa_device}")

            # Use the DAC's hardware mixer as the master volume with a dB taper.
            # This controls the real DAC output — so the slider works for local
            # playback *and* AirPlay/other sources, not just VLC — and the whole
            # slider range is usable. Falls back to VLC software volume if no
            # hardware control is found.
            if cfg.get("set_hw_volume", True):
                ctl = _find_hw_control(alsa_card)
                if ctl:
                    self._hw_ctl  = ctl
                    self._hw_card = alsa_card
                    self._volume  = int(cfg.get("initial_volume", 80))
                    _amixer_set_scaled(alsa_card, ctl, self._volume)
                    print(f"[Player] Master volume via hardware mixer '{ctl}' "
                          f"on card '{alsa_card or '0'}' at {self._volume}% "
                          f"(amplitude-mapped)")

            for aout in aout_list:
                try:
                    args = ["--no-xlib", "--quiet"]
                    if aout:
                        args.append(f"--aout={aout}")
                    # If ALSA selected and a device is known, target it
                    if aout == "alsa" and alsa_device:
                        args.append(f"--alsa-audio-device={alsa_device}")
                    inst = vlc.Instance(*args)
                    plyr = inst.media_player_new()
                    if plyr is not None:
                        self._instance = inst
                        self._player   = plyr
                        print(f"[Player] VLC initialised "
                              f"(aout={aout or 'default'}"
                              f"{', ' + alsa_device if (aout=='alsa' and alsa_device) else ''})")
                        break
                except Exception as e:
                    print(f"[Player] aout={aout or 'default'} failed: {e}")
            if self._player is None:
                raise RuntimeError("No working VLC audio output found")
            self._vlc_available = True
            self._attach_vlc_events()
        except (ImportError, Exception) as e:
            print(f"[Player] VLC not available: {e}. Running in demo mode.")
            self._vlc_available = False

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
        player instance — e.g. "cdda:///dev/sr0" for an audio CD. Unlike
        _play_track(), this does NOT require a real file on disk, so it works
        for optical-disc and network MRLs. Per-media options (e.g.
        ":cdda-track=3") are applied before playback. Emits track_started with
        `meta` so the player bar updates. Audio follows the same hardware/VLC
        volume path as normal playback.
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
        if self._hw_ctl:
            # Hardware master (dB-tapered): controls the DAC for every source
            _amixer_set_scaled(self._hw_card, self._hw_ctl, self._volume)
        elif self._vlc_available and self._player:
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
        if self._vlc_available and Path(path).exists():
            self._player.stop()          # ensure clean start at position 0
            media = self._instance.media_new(path)
            self._player.set_media(media)
            self._player.play()
            # With a hardware master volume, leave VLC at full and let the DAC
            # mixer set the level; otherwise apply VLC software volume.
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
