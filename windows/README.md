# PulseBox for Windows

A Windows build of PulseBox, ported from the Raspberry Pi 5 project
(`morgajx/pulsebox`, v0.300, commit `92501b7`). Same app, same UI, same
plugin API — the Linux/Pi-only plumbing has been swapped for Windows
equivalents or removed.

## Install

1. **Python 3.10+ (64-bit)** from python.org — tick *Add python.exe to PATH*.
2. **VLC media player (64-bit)** from videolan.org. It must match Python's
   bitness; `python-vlc` finds it through the registry.
3. Run **`install.bat`** (installs `python-vlc`, `mutagen`, `Pillow`, `requests`).
4. Run **`run_pulsebox.bat`**. To see log output, run `python pulsebox.py`
   from a terminal instead.

Optional, per plugin:

| Plugin | Needs |
|---|---|
| Spotify Connect | `pip install spotipy` |
| DVD Ripper | MakeMKV for Windows, `HandBrakeCLI.exe` (the separate command-line download), optionally `ffmpeg.exe` |
| CD Player / DVD Player | an optical drive (DVD Player also uses `vlc.exe`) |

Helper programs are looked for on `PATH`, in a `tools\` folder next to
`pulsebox.py`, and in the usual `Program Files` locations.

Settings, library database and playlists live in your user folder
(`%USERPROFILE%\.pulsebox_*`), the same file names as on the Pi. The config
file is compatible apart from the audio keys; the library database is not
portable because it stores absolute paths — let it rescan.

To start with Windows: put a shortcut to `run_pulsebox.bat` in the folder
that `Win+R` → `shell:startup` opens, and turn on Kiosk Mode in Settings.

## What changed from the Pi version

**Carried over unchanged** — library scanner and SQLite schema, playlist
manager, module/plugin API, albums / playlist / radio / EQ / about / plugins
pages, widgets, splash, and the Last.fm, Cover Art Extractor and Spotify
Connect plugins.

**Changed**

| Area | Pi | Windows |
|---|---|---|
| Audio output (`core/player.py`) | ALSA → HiFiBerry, `aplay` detection | VLC's Windows output (WASAPI by default); playback device chosen in Settings and switched live |
| Volume | HiFiBerry hardware mixer via `amixer`, dB taper | VLC's own volume (per-app, 0–100 linear) |
| Settings → Playback | PipeWire/ALSA/Pulse + ALSA device | Automatic/WASAPI/DirectSound/WaveOut + device dropdown |
| Settings → Network Drives | `sudo mount_smb.sh`, `/proc/mounts` | lists mapped drives; maps a share with `net use` (Windows keeps the credentials) |
| Shutdown / Reboot (nav bar and web remote) | `sudo shutdown` / `sudo reboot` | `shutdown /s` / `shutdown /r` |
| Video page | `set_xwindow` | `set_hwnd`; hardware decoding left on |
| CD Player | `cdparanoia`, `/dev/sr0`, `eject` | drive letter, track count and eject through Windows MCI |
| DVD Player | `cvlc` + ALSA device | `vlc.exe` fullscreen on the default device |
| DVD Ripper | `/dev/sr*`, key in `~/.MakeMKV/settings.conf` | drive letters, tool lookup in Program Files, key in the registry |
| Display scaling (`ui/theme.py`) | — | process made DPI-aware, Tk pinned to 96 dpi so the resolution-based scaling behaves as on the Pi |
| Visualiser | also spawned `cvlc … alsa://hw:0,0` | canvas animation only (the `cvlc` process never drew anything) |

All the Windows-specific calls are in one new file, `core/winplat.py`.

**Removed** (no Windows counterpart, or Windows already does it)

- Settings → Wi-Fi and Screen Rotation — use Windows Settings.
- Rotary Encoder Volume (GPIO). The ESP32 Wi-Fi knob still works: it only
  talks to the Web Remote API, which is unchanged.
- Audio Output Configurator (`config.txt` overlays) — replaced by the device
  dropdown.
- Bluetooth Manager (`bluetoothctl`) — pair in Windows; the device then
  appears in the playback device dropdown.
- AirPlay receiver (Shairport Sync), DLNA server (minidlna), DLNA renderer
  (gmediarender), Roon Bridge, Spotify librespot (Raspotify), CD Ripper
  (abcde) — all wrap Linux daemons/tools. On Windows the Spotify desktop app
  is itself a Connect device that the Spotify Connect plugin can control, and
  Roon has its own Windows app.
- `install.sh`, `scripts/*.sh`, `build.sh`, the compiled `dist/` tree.

**One bug fixed on the way through:** the web remote's `/api/radio/play`
called `player.play_url()`, which doesn't exist on `JukeboxPlayer` (same in
the Pi repo). It now calls `play_mrl()`.

## Test status

Checked on Linux with a headless display and real libVLC, because that is
what was available: library scan, every page, all Settings cards, every
plugin panel, audio playback / next / stop / volume / EQ, the web remote API,
and embedded video playback all ran with no errors.

**Not yet run on Windows.** These paths only execute there and are written
from the Windows API documentation rather than tested:

- `core/winplat.py` — drive detection, MCI CD track count and eject,
  `net use` mapping, shutdown, DPI awareness
- playback-device enumeration against real Windows devices
- `set_hwnd` video embedding
- MakeMKV key in the registry (`HKCU\Software\MakeMKV`, `app_Key`)
- `build.bat` (Nuitka)
- how Tk on Windows renders the emoji in the nav buttons
