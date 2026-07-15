# PulseBox 🎵

**Stream. Play. Enjoy.**

A TouchTunes-inspired Python jukebox application for Raspberry Pi kiosk use.  
Built with **Tkinter** (UI), **python-vlc** (audio), **Mutagen** (metadata),
**SQLite** (library + cover art BLOB storage), and **mpv** (video playback).

---

## Installation — Raspberry Pi OS Trixie (Debian 13)

### Core install (required for all features)

```bash
# 1. System packages
sudo apt install vlc mpv python3-pil python3-pil.imagetk ffmpeg

# 2. Remove PulseAudio — it conflicts with PipeWire on Trixie
sudo apt remove --purge pulseaudio pulseaudio-module-bluetooth 2>/dev/null
sudo apt autoremove

# 3. Python packages
pip install python-vlc mutagen requests --break-system-packages
```

> **Why remove PulseAudio?**  
> `sudo apt install vlc` can pull in `pulseaudio` as a dependency. PipeWire
> (the default audio server on Trixie) includes a full PulseAudio
> compatibility layer (`pipewire-pulse`) — having both installed causes them
> to fight over the audio device, resulting in silence.
>
> Verify PipeWire is running after removal:
> ```bash
> pactl list sinks short   # should show your audio device
> ```

> **Why mpv in the core install?**  
> mpv is used in two places: the **Video Library** (plays movies inside the
> app window) and the **Playlist animation** (record placement/spinning
> videos during jukebox playback). Both features stop working without it.

> **Why ffmpeg in the core install?**  
> ffmpeg extracts poster frames from video files for the Video Library, and
> is also used during DVD ripping. Installing it once covers both.

---

### CD Ripping (optional)

```bash
# Ripping engine + CD tools
sudo apt install abcde cdparanoia cd-discid eject

# Audio codecs — install the formats you want to rip to
sudo apt install flac          # FLAC (lossless — recommended)
sudo apt install lame          # MP3
sudo apt install fdkaac        # M4A / AAC
```

Configure in **Modules → CD Ripper**. Supports auto-rip on disc insertion.

---

### DVD Ripping + Video Library (optional)

PulseBox uses **MakeMKV** to decrypt and rip the disc, then
**HandBrakeCLI** to transcode to MP4. Copy protection is bypassed
automatically by **libdvdcss2** which MakeMKV loads when present.

#### Step 1 — libdvdcss2 (copy protection bypass)

```bash
sudo apt install libdvd-pkg
sudo dpkg-reconfigure libdvd-pkg
```

The `dpkg-reconfigure` step is mandatory — `libdvd-pkg` is a build wrapper
that downloads and compiles libdvdcss2 from VideoLAN. Without it the package
is installed but the library is absent and DVD decryption will fail silently.

> **Legal note:** libdvdcss2 is legal in most jurisdictions for personal
> backup of discs you own. Laws vary — check yours.

#### Step 2 — MakeMKV

MakeMKV is not in the standard Debian/Raspberry Pi OS repositories.

```bash
# Option A: deb-multimedia repository (easiest)
sudo apt install dirmngr ca-certificates
sudo apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 \
    --recv-keys 58D95F4B1B5A6FDC
echo "deb https://www.deb-multimedia.org bookworm main non-free" | \
    sudo tee /etc/apt/sources.list.d/deb-multimedia.list
sudo apt update
sudo apt install makemkv-bin makemkv-oss

# Option B: Build from source (always gets the latest version)
sudo apt install build-essential pkg-config libc6-dev libssl-dev \
    libexpat1-dev libavcodec-dev libgl1-mesa-dev qtbase5-dev zlib1g-dev
# Download both tarballs from: https://www.makemkv.com/download/
# Then for each: tar xzf makemkv-*.tar.gz && cd makemkv-* && \
#                ./configure && make && sudo make install
```

**MakeMKV beta key** — the free beta requires a registration key:

1. Visit https://www.makemkv.com/forum/viewtopic.php?t=1053
2. Copy the current beta key
3. Create `~/.MakeMKV/settings.conf` containing:
   ```
   app_Key = "paste-key-here"
   ```
   Or run `makemkvcon` once — it will prompt for the key interactively.

Without a valid key MakeMKV will still rip but with a 30-day trial
watermark on the output.

#### Step 3 — HandBrakeCLI

```bash
sudo apt install handbrake-cli
```

Verify:

```bash
HandBrakeCLI --version
```

#### Step 4 — TMDb API key (online poster art)

When no `poster.jpg` is found alongside the video, PulseBox fetches the
official movie poster from The Movie Database.

1. Create a free account at https://www.themoviedb.org
2. Go to **Settings → API → Create → Developer**
3. Copy the **API Read Access Token** (v3 auth key)
4. Enter it in **Modules → DVD Ripper → TMDb key**

#### Complete DVD rip workflow

```
Insert DVD  →  Modules → DVD Ripper
    ↓
"Read Title"  (auto-detects movie name from disc)
    ↓
Correct title if needed, set output directory, press ▶ Rip DVD
    ↓
Stage 1: MakeMKV reads & decrypts disc  →  .mkv      (5 – 45%)
Stage 2: HandBrakeCLI transcodes        →  .mp4      (45 – 92%)
Stage 3: Poster extracted or fetched    →  poster.jpg (92 – 97%)
Stage 4: Temp .mkv deleted, library refreshed        (97 – 100%)
    ↓
Video tab refreshes — poster card appears
```

Output:

```
~/Videos/
└── Movie Title/
    ├── Movie Title.mp4
    └── poster.jpg
```

---

### Video Library — supported formats

The **Video** tab scans your video directory recursively and picks up:

| Extension | Container |
|---|---|
| `.mp4`  | MPEG-4 (HandBrake output, most common) |
| `.mkv`  | Matroska (MakeMKV raw output) |
| `.mov`  | QuickTime |
| `.m4v`  | iTunes Video |
| `.avi`  | AVI |
| `.ts`   | MPEG Transport Stream |
| `.wmv`  | Windows Media Video |

Files can live anywhere in the directory tree — all layouts are detected:

```bash
~/Videos/film.mkv                        # flat file in root
~/Videos/The Matrix (1999)/film.mp4      # one-level folder
~/Videos/The Matrix/disc1/title_t00.mkv  # makemkv nested output
```

Poster art is resolved per video in this order:
1. `poster.jpg` or `poster.png` in the same folder as the video
2. `cover.jpg` or `cover.png` in the same folder
3. `poster.jpg` or `poster.png` in the parent folder
4. Any `.jpg` or `.png` in the same folder
5. Placeholder artwork

To add your own poster: drop a `poster.jpg` next to the video file and
press **⟳ Rescan** in the Video tab.

---

### Playlist animation (optional)

The jukebox playlist screen plays a record-drop animation between tracks.
This requires two video files placed in `assets/video/`:

```
assets/
└── video/
    ├── record_placement.mp4   # plays once when a track is about to start
    └── record_spinning.mp4    # loops until the track finishes
```

These files are not included — supply your own. Any `.mp4` works.
mpv is required (installed in the core step above).

---

### Bluetooth (optional)

```bash
sudo apt install bluetooth bluez
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
```

Configure in **Modules → Bluetooth Manager**.

---

### Web Remote (no extra dependencies)

The web remote runs on Python's built-in `http.server` — nothing to install.

Enable by loading the module. It starts automatically on port 8080.
Access from any device on the same network: `http://<pi-ip>:8080`

Change the port in **Modules → Web Remote**.

---

### Spotify Connect (personal use only)

> ⚠  **Personal use only.** librespot reverse-engineers Spotify's internal
> protocol, which is against Spotify's Developer Terms of Service.
> Requires Spotify Premium for audio streaming.

#### Install Raspotify

```bash
sudo apt-get install curl
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
```

The Pi will appear as a Spotify Connect speaker in your Spotify app.

#### Configure via PulseBox

**Modules → Spotify (librespot) → Config tab:**

| Setting | Value |
|---|---|
| Device Name | `PulseBox` (or any name) |
| Bitrate | `320` kbps (requires Premium) |
| Audio Backend | `alsa` |
| ALSA Device | `hw:1,0` (HiFiBerry on card 1) |

Tap **Save & Restart**.

#### Optional: Now-Playing Metadata

```bash
pip install spotipy --break-system-packages
```

1. Go to https://developer.spotify.com/dashboard
2. Create App → Web API
3. Add redirect URI: `http://localhost:8765/callback`
4. Copy the **Client ID**
5. **Modules → Spotify → Connect tab** → paste ID → **Start Polling**
6. One-time browser login — metadata updates every 5 seconds

---

## HiFiBerry Digi / Optical (S/PDIF) Setup

Edit `/boot/firmware/config.txt`:

```ini
dtparam=audio=off
dtoverlay=hifiberry-digi
```

In **PulseBox → Settings → Playback**:
- VLC Output: `pipewire`
- ALSA Device: blank (PipeWire manages routing)

For direct ALSA output: set ALSA Device to `hw:1,0`.

---

## Features

| Feature | Detail |
|---|---|
| **Splash screen** | Neon logo startup with progress bar |
| **VLC Playback** | PipeWire output — HiFiBerry, HDMI, USB audio |
| **SQLite Library** | Fast startup — art stored as BLOB, no filesystem reads |
| **A–Z Browser** | Starts on configured letter; compilations indexed by album |
| **Cover Grid** | Configurable column album art grid (2–10 columns) |
| **Tree View** | Collapsible Artist → Album tree with thumbnails |
| **Album Detail** | Track list, play all, shuffle, double-click to play |
| **Art Overlay** | Full-screen blurred art when thumbnail tapped |
| **Queue / Jukebox** | Up to 25 tracks; saved named playlists |
| **Playlist Animation** | Record placement + spinning video during jukebox playback |
| **Equaliser** | 10-band VLC EQ, preamp, 14 presets, persisted to config |
| **Internet Radio** | Radio Browser API, icon grid, search |
| **Video Library** | Poster grid — MP4 MKV MOV M4V AVI TS WMV, embedded mpv |
| **DVD Ripper** | MakeMKV + HandBrakeCLI, libdvdcss2, TMDb poster art |
| **CD Ripper** | abcde — FLAC / M4A / MP3, auto-rip on insertion |
| **Web Remote** | Phone-friendly HTTP remote — browse, queue, transport |
| **Spotify Connect** | librespot — Pi as Spotify speaker (personal use only) |
| **Player Bar** | Always-visible: transport, seek bar, volume |
| **Touch Scroll** | Touchscreen drag-to-scroll on all views |
| **Settings** | Music dir, video dir, WiFi, VLC output, ALSA, columns |
| **Audio Configurator** | I2C HAT overlays, HDMI1/HDMI2, noaudio KMS fix |
| **Bluetooth Manager** | Pair, connect, disconnect via bluetoothctl |
| **Module API** | Drop `.py` files in `/modules/` to extend anything |

---

## Module API

Drop a `.py` file in `/modules/`. Subclass `JukeboxModule`:

```python
from core.config import JukeboxModule
import tkinter as tk

class MyModule(JukeboxModule):
    MODULE_ID          = "my_module"
    MODULE_NAME        = "My Module"
    MODULE_VERSION     = "1.0.0"
    MODULE_DESCRIPTION = "Does something cool."
    MODULE_AUTHOR      = "Your Name"

    def on_load(self, app):
        super().on_load(app)
        # self.player, self.library, self.config, self.app all available

    def on_track_started(self, track: dict):
        print(f"Now playing: {track['title']} — {track['artist']}")

    def on_track_ended(self):
        pass

    def on_queue_empty(self):
        pass

    def get_ui_panel(self, parent) -> tk.Frame:
        f = tk.Frame(parent)
        tk.Label(f, text="Hello from MyModule!").pack()
        return f
```

---

## Configuration

Saved to `~/.pulsebox_config.json`.

| Key | Default | Description |
|---|---|---|
| `music_dir` | `~/Music` | Root directory for audio library |
| `dvd_output_dir` | `~/Videos` | Root directory for ripped video |
| `volume` | `80` | Default volume (0–100) |
| `vlc_aout` | `pipewire` | VLC audio output module |
| `vlc_alsa_device` | `""` | ALSA device e.g. `hw:1,0` |
| `columns_per_row` | `6` | Album/poster grid columns (2–10) |
| `browse_mode` | `covers` | `covers` or `tree` |
| `repeat` | `none` | `none` / `track` / `queue` |
| `tmdb_api_key` | `""` | The Movie Database API key |
| `web_remote_port` | `8080` | Web remote HTTP port |
| `spotify_client_id` | `""` | Spotify app Client ID |
| `librespot_name` | `PulseBox` | Raspotify device name |
| `librespot_bitrate` | `320` | Raspotify bitrate (kbps) |
| `librespot_alsa` | `""` | Raspotify ALSA device |

---

## Autostart (kiosk)

**Option A — LXDE autostart:**

Add to `/etc/xdg/lxsession/LXDE-pi/autostart`:

```
@python3 /home/pi/pulsebox/pulsebox.py
```

**Option B — systemd service:**

Create `/etc/systemd/system/pulsebox.service`:

```ini
[Unit]
Description=PulseBox Jukebox
After=graphical.target

[Service]
User=pi
Environment=DISPLAY=:0
WorkingDirectory=/home/pi/pulsebox
ExecStart=/usr/bin/python3 /home/pi/pulsebox/pulsebox.py
Restart=on-failure

[Install]
WantedBy=graphical.target
```

```bash
sudo systemctl enable pulsebox
sudo systemctl start pulsebox
```

---

## Licence

Copyright © 2025 Andrew J. Morgan.  
Personal edition: GPL v2+.  
Commercial edition (`pulsebox-c`): All rights reserved.
