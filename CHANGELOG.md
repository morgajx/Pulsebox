# PulseBox — Changelog

Versioning: 0.25 onward, increment by 0.01 per release.
`APP_VERSION` lives in `core/config.py`.

## 0.37
**Main app — album art overlay: title drawn twice (regression)**
- In the full-screen album art overlay, the album title was painted twice, slightly offset. The resize handler (`_on_overlay_resize`) drew the name/artist text with no canvas tag, while `_refresh_overlay_labels` drew the same text tagged `"overlaytext"` and cleared only tagged items — so the resize copy was never removed. The resize handler now tags its text `"overlaytext"` and uses the same y position, so a redraw cleanly replaces the old text instead of stacking a second copy. (Note: the 0.36 web-remote font-weight tweak was a wrong guess at this same report; this is the actual fix.)

## 0.36
**Web remote**
- Fixed the album/track title in the big now-playing and album views appearing faintly doubled (a 1-2px echo above the text). The titles used font-weight 800; on phone fonts without a true 800 face the browser synthesises bold by drawing the glyphs twice, offset. Dropped those titles to a real 700 weight and added `-moz-osx-font-smoothing:grayscale`.

## 0.35
**Web remote**
- Music library rows now show the **artist on top and the album underneath** (previously album over artist).

## 0.34
**Web remote radio + now-playing sync**
- Internet radio now plays from the web remote. `/api/radio/play` called `player.play_url()`, which never existed — the handler threw before it could play or navigate, so radio did nothing *and* the app didn't follow. Added a real `JukeboxPlayer.play_url(url, title)` that plays the stream and sets it as the current track, and routed the app's Radio page through the same method so a station played on the box now shows on the remote (and vice-versa).

**Bluetooth manager**
- Scanning now finds unpaired/advertising devices, not just OS-paired ones. A one-shot `bluetoothctl scan on` exits immediately and BlueZ stops discovery with it; scanning now uses `--timeout` to keep the discovery client alive for the whole window.
- Pairing and connecting now run in a single scripted `bluetoothctl` session with an agent (`agent on; default-agent; pair; trust`, and `trust; connect` with one retry), which is what lets a freshly discovered device actually pair, and a paired device actually connect (A2DP sometimes needs the second attempt).

## 0.33
**Rotary Encoder — self-repair of stale pin config**
- The module now auto-corrects the old default wiring (DT=18, BTN=27) to the tested CLK=17 / DT=27 / SW=22 (mute) on start. That old combo put the button on the encoder's DT line, so knob turns registered as button presses. The repair updates the in-memory config so the running session works even if the config file can't be written (e.g. a root-owned `~/.pulsebox_config.json` from an earlier sudo run), and persists when it can.
- `Config.save()` now logs write failures instead of failing silently, so an unwritable config file is visible.

## 0.32
**Rotary Encoder — pin collision guard**
- If the configured button (SW) pin equals the CLK or DT pin, the button is now auto-disabled (with a log note) instead of misreading knob rotation as button presses. This was the root cause of the "volume jumps to 80 / doesn't step" reports: a saved config from an older build had DT=18 and the button on GPIO 27 — which was the encoder's actual DT line — so every detent registered as a button (reset) press. Correct wiring is CLK 17 / DT 27 / SW 22.

## 0.31
**Rotary Encoder — diagnosis for "works standalone, not in app"**
- `wpctl` calls now use an explicit binary path (`/usr/bin/wpctl` if not on PATH) and inject `XDG_RUNTIME_DIR=/run/user/<uid>` when it's missing. When PulseBox is launched outside a full login session (autostart/service), the app's environment can lack these even though an interactive terminal has them — which is exactly why the standalone script worked and the module didn't. Same hardening applied to the player's volume path (slider / web remote).
- The encoder now writes a diagnostic log to `~/pulsebox_encoder.log` (and stdout): the pins in use, whether `wpctl` is found and can read the current volume at startup, and a line per rotation/button event with the `wpctl` result. This makes the remaining failure mode visible even without a terminal.

## 0.30
**Audio Output Configurator — config.txt fix**
- Switching to HDMI now fully disables a HiFiBerry Digi. `_remove_managed_lines` stopped at the `dtparam=i2c_arm=...` baudrate line (only the wm8804 Digi/Digi+ Pro cards get it), leaving `dtoverlay=hifiberry-digi` active. The removal now covers `dtparam=`/`hdmi_` lines, and HDMI mode additionally comments out any still-active audio-HAT overlay (managed or hand-added).

**Rotary Encoder / volume**
- The encoder no longer force-sets `LGPIOFactory(chip=4)`; it uses gpiozero's default pin factory first (as the working standalone script does) and only forces an explicit lgpio chip if that fails.
- Volume now uses the same `wpctl` calls as the known-good script (relative set-volume on `@DEFAULT_AUDIO_SINK@`, `set-mute toggle`), reading the level back for the UI, with a VLC fallback.
- Added console logging for every rotation/button event and every `wpctl` result (no more silent failures), plus `scripts/encoder_test.py` — a standalone diagnostic to confirm wiring + wpctl independently of the app.

## 0.29
**Volume backend + Rotary Encoder**
- Volume is now applied to the **PipeWire default sink** via `wpctl` (with VLC kept at 100% so they don't compound), falling back to VLC's stream volume only when `wpctl` isn't available. This is what actually controls the HiFiBerry output, and it means the rotary encoder, the on-screen slider, and the web remote all use the working backend.
- Rotary encoder default **DT pin corrected to GPIO 27** (was 18) to match the tested wiring (CLK 17 / DT 27). The push button stays disabled by default — set the SW pin to 22 in the panel if you have one wired.

## 0.28
**Rotary Encoder Volume plugin**
- Fixed volume flipping between 0 and 80 instead of stepping. Those are the mute values: the push-button defaulted to enabled on GPIO 27 with the "mute" action, so an unwired/floating or over-sensitive SW pin was toggling mute on every event. The button now defaults to **disabled** (pin 0) — enable it only when one is actually wired — and when enabled it's hardened with `pull_up=True` and a longer `bounce_time` (0.3s). Rotation now steps by the configured amount.
- Fixed the on-screen player bar not tracking encoder changes: `_update_vol_ui` called a non-existent `update_volume()`; it now updates the bar's volume slider and % label directly.

## 0.27
**DVD Ripper plugin**
- Added a **Save Key** button next to the TMDb API key field. The key now persists to `~/.pulsebox_config.json` on its own (`config.set("tmdb_api_key", …)`) instead of only being saved as a side-effect of starting a rip — mirroring the existing MakeMKV key save.

## 0.26
**Web remote**
- New **Playlist** tab (5th tab): manage the live queue (remove items, Play, Clear, **Save** as a named playlist) and your **Saved Playlists** (Load / Delete) — the `~/.pulsebox_playlists.json` system is now reachable from the phone.
- New endpoints: `GET /api/playlists`, `POST /api/playlist/save|load|delete`, `POST /api/queue/remove`.
- Source-aware navigation: playing **radio** now brings the on-screen app to the **Radio** page (music already goes to the Playlist page).
- Refreshed, cohesive icon set for all five tabs (Music / Radio / Video / Playlist / Power).

## 0.25
**Web remote (`modules/web_remote.py`)** — full Volumio-style rebuild, wired to the
existing JSON API:
- Frosted bottom tab bar, tall rounded rows, blurred now-playing backdrop, persistent mini-player.
- Library ordered by **Artist → Album**; A-Z grouped into collapsible letter sections that **start collapsed**; the old right-hand A-Z rail was removed.
- Album cover pinned in view while the track list scrolls.
- Per-tab coloured bottom buttons; headers renamed **Music Library / Radio Stations / Video Library**.
- Currently-playing track highlighted live in album + library (animated equaliser); on load the library jumps to the playing album, otherwise starts normally.
- Album detail **Play** + **Shuffle**; each track has a **Play** button (plays from that track to the end of the album) plus **Add to queue**.
- The main on-screen app now follows the remote: play / queue-add / radio actions bring the Tkinter UI to the playlist page (`_nav_main()`).
- Page footer shows the version from `APP_VERSION`.

**Audio (`core/audio_spdif.py`, `core/player.py`)**
- New `core/audio_spdif.py`: card-agnostic helper that forces a HiFiBerry Digi's `Tx Source` to `AIF`. Safe no-op on any card without that control.
- `JukeboxPlayer` now calls it at startup and before every track (`_play_track`). This complements the existing boot-time `hifiberry-tx-source.service`: it re-asserts AIF after a projector/HDMI re-probe resets the card mid-session — the cause of the intermittent silence.
- `scripts/spdif_fix.sh`: optional standalone helper (not wired into `install.sh`, which already manages the boot service via the Audio Output Configurator).

**Docs**
- `docs/`: web remote technical documentation (Markdown + Word) — architecture & entity diagrams, full API reference, module guide.
