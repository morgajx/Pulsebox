# PulseBox — Development Notes (Oct–Nov 2026)

A record of the work done in this period: the hardware volume knob, the website,
the documentation refresh, and the rotation / installer fixes. Kept as a running
reference so nothing has to be re-derived next time.

---

## 1. ESP32-S3 Wi-Fi volume knob (+ spectrum analyser)

**Board:** Waveshare ESP32-S3-Knob-Touch-LCD-1.8 — a 360×360 round display
(ST77916/SH8601 QSPI), CST816 capacitive touch, a quadrature knob, a PDM
microphone, and a DRV2605 haptic driver. Dual-chip (ESP32-S3 + a companion
ESP32 sharing the one USB-C port).

**What it does:** turns the knob → sets PulseBox volume over Wi-Fi via the Web
Remote HTTP API. Round screen shows a volume dial; **tap the screen** to switch
to a live microphone **spectrum analyser**; tap again to go back. Volume syncs
both ways (knob ↔ PulseBox) every few seconds. No change to PulseBox itself is
needed — it only uses the existing Web Remote API:
- `GET  /api/status`  → JSON incl. `"volume":NN`
- `POST /api/volume`  → body `{"volume":0-100}` (plain HTTP :8080, no auth)

**How it was built (hard-won lessons):**
- Built on top of Waveshare's **`08_LVGL_Test`** example (its display/touch/LVGL
  init is reused; don't re-create it). The firmware is appended as one block.
- **Flashing:** this board has a **BOOT button but no RESET**. Download mode =
  hold BOOT, power-cycle (power button or re-plug USB-C), release BOOT. The
  **USB-C orientation picks which chip you talk to** — flip the plug 180° if it
  won't connect ("No serial data received").
- **Fonts:** `~/Documents/Arduino/libraries/lv_conf.h` controls which Montserrat
  sizes compile in (set the `LV_FONT_MONTSERRAT_XX` line to `1`). Only 14/20/40
  were enabled; 28 was not.
- **Serial** over USB needs **Tools → USB CDC On Boot = Enabled**, else the
  Serial Monitor is blank even though the sketch runs.
- **The knob only reads correctly through Waveshare's `bidi_switch_knob`
  (`iot_knob`) driver** on GPIO **8 / 7**, with the event-bit + FreeRTOS-task
  pattern — a raw `attachInterrupt` read does nothing. (The user's working
  Volumio sketch was the reference that settled this.)
- **Spectrum:** PDM mic on GPIO **45 (clk) / 46 (data)** via the newer
  `i2s_pdm` driver, 16 kHz / 16-bit mono, FFT with the **ArduinoFFT** library
  (v2 API), auto-gain so it self-calibrates. All LVGL drawing happens inside an
  `lv_timer` callback (the board runs LVGL in its own task behind a mutex, so
  touching LVGL from other threads crashes it). Tap toggle uses LVGL's own
  click event, not a second touch reader.
- WiFi is 2.4 GHz only. Diagnose connect failures by the `WiFi.status()` code
  and the disconnect `reason` (shown on-screen when serial was unavailable).

Files live in the user's Arduino `08_LVGL_Test` sketch folder, with backups of
earlier merge attempts alongside (`.ino.bak`, `.ino.vol.bak`).

---

## 2. Marketing website

An **Apple-style landing page** for PulseBox (published as a Claude artifact).
Minimalist: system/SF typography, big hero, generous whitespace, light/dark
aware. Hero uses the in-room photo of the cabinet; a dramatic black band shows
the glowing spectrum knob. Sections: hero, the app, every source
(AirPlay/Spotify/Roon/DLNA/CD/DVD/Bluetooth/Last.fm), the knob, the web remote,
an "under the hood" strip. Only one real photo existed, so two clean crops were
pulled from it (full cabinet + knob close-up).

---

## 3. Documentation

- **Reference doc** (`PulseBox_Documentation.docx`) — rebuilt from the old June
  README to the v0.300 state: ALSA-default audio, all 16 modules, hardware
  volume control, compiled build / launcher / kiosk, config, module API.
- **User manual** (`PulseBox_User_Manual.docx`) — edited in place (formatting
  preserved) to add the six newer plugins (AirPlay 2, Cast/UPnP-DLNA, CD Player,
  DLNA Media Server, DVD Player, Roon), correct the audio section to ALSA, note
  the Wi-Fi knob, and fix the "Last.fm Scrobbler" heading. The developer/Web
  Remote API section was checked against the code and left unchanged.
- Remember to **Update Field** on the Word table of contents after editing.

---

## 4. Rotation & installer fixes — PR #1 (merged to `main`, squash `e83f0cd`)

### Screen & touch rotation (`ui/settings_page.py`, `scripts/set_touch_rotation.sh`)
- **Root cause:** rotation wrote `display_rotate=` to `/boot/firmware/config.txt`.
  That's the **legacy firmware** rotation, which the Pi 5 KMS driver
  (`vc4-kms-v3d`) + **labwc/Wayland** desktop **ignores** — so neither screen
  nor touch rotated.
- **Fix (screen):** rotate through the **compositor**. Set the transform in the
  **kanshi** profile (`~/.config/kanshi/config`) and reload kanshi
  (`kanshictl reload`, else restart it); a best-effort live `wlr-randr` nudge
  too. Applies instantly, **no reboot**. kanshi is the authority on this Pi and
  will revert a bare `wlr-randr` change, which is why reloading kanshi matters.
  Needs `wlr-randr` and `kanshi` installed.
- **Fix (touch):** a compositor transform rotates the display but **not touch**
  (touch is a libinput concern). `scripts/set_touch_rotation.sh` writes a
  `LIBINPUT_CALIBRATION_MATRIX` **udev rule** for the detected touchscreen.
  Settings now calls it so screen + touch are set together. **Touch applies
  after a reboot** (libinput reads the matrix at device init).
- **Confirmed mapping — touch matrix is the SAME angle as the display** (not the
  inverse), verified on the Goodix DSI panel:

  | Display rotation | `LIBINPUT_CALIBRATION_MATRIX` |
  |---|---|
  | 0° | `1 0 0 0 1 0` |
  | 90° | `0 -1 1 1 0 0` |
  | 180° | `-1 0 1 0 -1 1` |
  | 270° | `0 1 0 -1 0 1` |

  Touch device on this unit: **`11-005d Goodix Capacitive TouchScreen`** (the
  `11-005d` is the I²C bus/addr; the udev rule matches `*Goodix Capacitive
  TouchScreen` so it survives kernel changes).
- A scoped `NOPASSWD` sudoers entry lets the app run the touch helper.

### Installer compile step (`install.sh`)
- **Root cause:** `set -euo pipefail` stays on through the whole tail of the
  script, and the optional Nuitka **compile prompt is the last step**. A
  non-zero exit in the reporting tail — most likely `pactl` failing on an
  ALSA-only setup, under `pipefail` — aborted the installer **before** the
  compile prompt. (There is **no `reboot`** in `install.sh`.)
- **Fix:** `set +euo pipefail` before the reporting/optional tail so the compile
  step is always reached. Still intentionally skipped on `--all` / `--core-only`.

---

## 5. Key technical reference (gotchas worth keeping)

- **Audio:** PulseBox drives **ALSA directly** (not PipeWire). It auto-detects
  the card (HiFiBerry AMP4 Pro = card 0 `sndrpihifiberry`, `pcm512x`), points VLC
  at the matching `plughw:CARD=…`, and **auto-raises the hardware "Digital"
  mixer** (volume is amplitude-mapped, `dB = 20·log10(vol/100)`). Runs headless
  with no audio device (`no_audio_device` flag).
- **The launcher prefers the COMPILED build** whenever one exists
  (`~/pulsebox/dist` or `~/pulsebox_compiled`). Editing the `.py` source does
  **nothing** until you rebuild (`./build.sh`) or force source with
  `PULSEBOX_FORCE_SOURCE=1`. This is why rotation "didn't take" after editing
  the source — the old compiled binary was still running.
- **Kiosk performance:** V3D GPU idles at 500 MHz → set `v3d_freq_min=960` in
  config.txt; a Wayland present-throttle under labwc is worked around by the
  launcher's `grim` "framekeeper" loop.
- **Pi has password-protected sudo** → `sudo -n` auto-commands fail silently
  unless covered by the scoped sudoers file. Run Nuitka builds **as the pi
  user**, not root.
- **Web Remote has no authentication** (plain HTTP on :8080) — keep it on a
  trusted LAN.

---

## 6. Repo / GitHub state

- Repo: **`github.com/morgajx/pulsebox`** (public). Default branch `main`.
- PR #1 (rotation + compile fixes) **merged** → `main` at `e83f0cd`.
- The Pi has **no git**; updates are done by `curl`-ing raw files from
  `raw.githubusercontent.com/morgajx/pulsebox/main/…`. Installing git on the Pi
  (`sudo apt install -y git`) would make this a single `git pull`.
- To update the Pi after a change: pull the file(s), then `./build.sh` and
  `sudo systemctl restart pulsebox`.
