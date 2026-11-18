# Hi-Fi Lite: security on a home network

Lane `hifi-lite-security` (aeb53). Source: hifi-receiver `h2/lite-bringup`
(435d6cc) with squeezelite-esp32 ee1720c and patches 0001-0008. Fixes land on
branch `h2/security-surface`.

## Threat model

The Lite is a network speaker in someone's home. The model:

- **The home LAN is trusted to *play* and *adjust*.** That matches every
  speaker people own (AirPlay, Spotify Connect, Squeezebox, Chromecast): a
  guest on the Wi-Fi can pick songs and turn the volume up.
- **The LAN is *not* trusted to make things stick or to leak things.** Nobody on
  the Wi-Fi (a guest's phone, a compromised smart plug) should be able to:
  1. install firmware that isn't ours (that lasts across reboots, and
     a hostile image could pivot from the board into the rest of the LAN);
  2. read secrets (the relay's access token, the Wi-Fi password);
  3. point the board's outbound connections somewhere else (the relay, the
     update server), which would leak secrets or install code;
  4. turn on extra services (telnet).
- **Browsers are hostile.** Any web page the person opens can send
  requests to `http://192.168.x.y/...` (CSRF) and can rebind its own
  hostname to the board's IP (DNS rebinding). Nothing on the board may act on
  a request a foreign page made.
- **The board must never open the network from outside.** No port mapping
  (UPnP/NAT-PMP/PCP), no routing between the setup AP and the LAN, and only
  outbound connections to the relay and update server.
- **Within radio range** (Bluetooth, the setup AP), someone can do what a
  LAN guest can do. They get nothing more.
- **Out of scope:** someone holding the board (UART, JTAG, flash readout).
  Secure boot and flash encryption cover that; see the plan at the end.

Ratings: **High** = a LAN device or a web page gets persistent code, secrets or
an open network. **Medium** = needs a nearby attacker or a person's mistake, or
leaks something minor. **Low** = nuisance within the trusted-LAN model.

## Inventory and ratings

### Listening sockets (station mode, normal use)

| Port | What | Who can reach it | Rating | Fix |
|---|---|---|---|---|
| TCP 80 | esp_http_server: our web app `/`, our test API `/api/v1/*`, squeezelite's UI `/squeezelite/`, `/config.json`, `/commands.json`, `/status.json`, `/messages.json`, `/recovery.json`, `/flash.json`, `/reboot*.json`, cspot zeroconf `/spotify_info` | LAN, and **any web page** via CSRF / DNS rebinding | **High** (see the next table) | Host/Origin gate, signed firmware, closed keys |
| TCP 5000 | AirPlay 1 (RAOP) RTSP, plus UDP audio/control/timing ports per session | LAN | Low | Protocol is play-only; no password is the norm for home speakers. Covered by Bluedroid/lwIP patches only |
| UDP 5353 | mDNS: hostname, `_hifi._tcp`, `_raop._tcp`, `_spotify-connect._tcp` | LAN (link-local multicast) | Low | Announces name and model only, never secrets (checked `txt` in hifi_main.c:1256) |
| UDP 3483 (out) / TCP 3483 (out) | slimproto: discovery broadcast and client connection to LMS | Outbound only; no listener | Low | A rogue LMS on the LAN can play audio (same as any Squeezebox) |
| UDP 67/53 | DHCP server and captive DNS | **Setup AP only** | Low | AP only, see below |
| TCP 23 | telnet console (squeezelite) | LAN, **when `telnet_enable` is Y/D/X** (off by default, but any LAN client could switch it on through `/config.json`) | **Medium** (full console, no password) | Compile telnet out (`HIFI_TELNET` undefined) |
| none | UPnP IGD / NAT-PMP / PCP | — | none found | grep of the whole tree: no client; `build.sh` fails if either link map names one (upnp, natpmp, ssdp, pcp_client) |

Outbound only: the relay (wss, TLS with the certificate bundle), the update
manifest and image (https or http, from NVS `hifi_update_url`), Spotify,
SNTP and LMS.

### The HTTP server on port 80, path by path

| Path | Before | Rating | After |
|---|---|---|---|
| `/api/v1/ota/*` (chunked install) | Anyone on the LAN, and any web page (`Access-Control-Allow-Origin: *`) can flash firmware; checks only a SHA-256 the sender provides | **High** | From the LAN: the image must carry the Hive signature (trailer, below). Through the relay: as before (the command is already signed) |
| `/api/v1/update`, `/api/v1/update/upload` (the app's Updates page) | Installs any image from a URL or a phone upload; only the image's own checksum | **High** | Signed image required from the LAN. Decided below |
| `/api/v1/remote` POST (set relay URL, token, enable) | LAN repoints the relay: the board then sends its signed-command channel and the Access token to any server | **High** | 403 from the LAN; relay/console only |
| `/api/v1/wifi`, `wifi/forget`, `reset`, `reboot`, `name`, `capture`, `tone`, `amp`, … | Open to LAN and web pages | Medium (web pages), Low (LAN) | Web pages refused by the gate; LAN keeps them (trusted to adjust), except that reboots, the BT test, Wi-Fi changes, forget and reset get 409 while new firmware is on trial (so the LAN can't force a rollback; squeezelite's own `/connect.json`, `/reboot*.json`, `/commands.json` and `/config.json` POSTs too). A player name may not hold `"` or `\` (it goes into squeezelite's command line) |
| `/config.json` GET | Returns every NVS key, including **`hifi_relay_pass` (the Cloudflare Access token) and `hifi_relay_key`** | **High** (secret leak) | Those keys are redacted |
| `/config.json` POST | Writes any NVS key: `fwurl` (triggers a recovery OTA from any URL), `telnet_enable`, `hifi_relay_url`, `hifi_update_url`, `bt_*`… | **High** | Refused keys: `fwurl`, `telnet*` and every `hifi_*` key (relay, update URL, `hifi_bt_save`, …). `autoexecN` values must be `squeezelite` with known single-letter options, or one of the listed `cfg-*` commands; `cfg-hw-preset` is refused (it writes any NVS key). The rest stays (the squeezelite UI writes them) |
| `/commands.json` POST | Runs **any** console command (`nvs_set`, `update`, `recovery`, `api`, `hifi`, `i2cset` …) | **High** | Only the `cfg-*` commands the squeezelite UI's forms use, plus read-only ones |
| `/recovery.json`, `/flash.json` | Reboot to recovery, then flash any uploaded image | **High** | Both 403 from the LAN, in the main app and in recovery itself (recovery flashes unsigned images, so its upload page is USB only; Decided). If the board ends up in recovery some other way (a boot loop), the LAN can't flash it: the worst left is a board that needs USB (DoS) |
| `/spotify_info` GET/POST | Spotify zeroconf, by design open to the LAN (patch 0006 lets anyone take over). cspot registered it straight on the server, past the gate | Low (LAN) / Medium (web pages could add a user) | Now registered through the gate too (0010, `Shim.cpp`): foreign origins and rebound names refused; the Spotify app sends neither |
| `/status.json`, `/messages.json` | Read state | Low | Gate only |
| `/api/v1/crash`, `/api/v1/diag/*` (from `main`, #185) | Read-only: the console log kept in RTC memory, tasks, network, audio and OTA counters. No secrets in them (the relay secret and bench key are never logged). `diag/echo` runs a throughput test of up to 1 MB | Low (LAN) | Gate: web pages and rebound names refused; a device on the LAN can read them. `diag/echo` is refused from the LAN (403): it is a bench test over USB or the relay |
| Every response | `Access-Control-Allow-Origin: *` on our API and squeezelite's root handler | **High** (enables the rest from any page) | Header removed |

`is_user_authenticated()` in squeezelite is a stub that always returns true.
The `// todo` bodies behind it are why every handler is open.

**The gate** (one function in front of every handler, ours and squeezelite's):
- `Host` must be an IP literal, the board's hostname, or the hostname with
  `.local`, `.home.arpa` or `.internal`. That kills DNS rebinding, because a
  rebound page carries its own `Host:`. Router suffixes (`.lan`, `.home`,
  `.box`, `.fritz.box`, `.localdomain` …) are refused: they are, or could
  become, public domains, and whoever owns `hifi-lite-4.<that>` could rebind
  it to the board. Decided: open the board by its IP or `hifi-lite-4.local`.
- If an `Origin` header is present, it must be `http://<Host>`. That kills
  CSRF: a browser always sends `Origin` on cross-site POSTs and fetches.
  The Spotify app, the LMS server and curl send no `Origin` and pass.
- The setup AP's captive-portal redirects are exempt (phones probe with
  foreign Host names there).

### The setup access point

- SSID `hifi-lite` (the board name). Before this branch the password was
  **`squeezelite`**, the public upstream default. WPA2, 192.168.4.1, at most 4 clients.
- It comes up on first boot (no Wi-Fi saved) **and again as AP+STA whenever the
  saved Wi-Fi is lost for 2 retries** (a router reboot is enough), and stays up
  until the station reconnects.
- **Does it route?** No. There is no `ip_forward`, NAPT or PPP in the build
  (`CONFIG_LWIP_IP_FORWARD` and PPP are unset; NAPT is not in this lwIP). An AP client
  reaches only the board: 192.168.4.1:80 and DNS.
- Rating: **Medium.** Anyone in radio range who knows the public password
  can join while the router is down and use the same port-80 surface as a LAN
  device. After the fixes that is "adjust", not "own". It can still change the
  Wi-Fi the board joins, which is a nuisance and a way to watch its traffic.
  The traffic is TLS-protected for the relay and signed for firmware.
- Fix (done in patch 0010 + `hifi_gate.c`; Decided):
  - **First setup** (no Wi-Fi saved) keeps `squeezelite`. There is nothing on
    the board to protect yet, and the setup guides use that password.
  - **Once a Wi-Fi network is saved**, an AP still on the public password gets
    a random 12-character one of its own (`esp_random`, no look-alike
    characters), stored in NVS as `ap_pwd`. Read it with `nvs_get ap_pwd`
    over USB or on squeezelite's settings page from the LAN. The log no longer
    prints it (the log reaches the Hive's crash reports).
  - With Wi-Fi saved, the AP comes up only after **10 minutes** without it,
    not after 2 failed retries, so a router reboot no longer opens it.
  - Known gap: if the saved network is missing at boot
    (`connect_active_ssid failed`), the AP comes up at once, but with the
    random password.

### Bluetooth (A2DP sink)

- Classic BR/EDR only, no BLE (no GATT surface). Profiles: A2DP sink,
  AVRCP. SSP with IO capability NoInputNoOutput (patch 0007, "just works":
  no number to confirm). A phone that stays idle is released after 120 s so
  the next one can connect.
- What an attacker in range gets: they can pair and play audio, or
  disconnect the current phone. There is no path from A2DP to the network
  or to NVS.
- Rating: **Low.** That is the norm for a speaker without a screen or
  buttons for a passkey. Numeric comparison has nothing to show the number on.
  Just-works pairing gives no MITM protection, but the link only carries
  audio. Keep it as is. The real risk is stack bugs (BlueBorne-style, BrakTooth
  in the ESP32 controller), and those are handled by the patch list below.
- Optional hardening for later (not done; it would change pairing): accept new
  pairings only for 3 minutes after power-up or a button press.

### The relay (Hive dial-in)

- Outbound `wss://` to the Hive relay behind Cloudflare Access (TLS checked
  against the certificate bundle; Access token in NVS `hifi_relay_pass`).
- Every command is a JSON line signed with Ed25519 by a key in the build
  (`HIFI_HIVE_PUBKEY`). The board checks the signature, `dev` = its own id,
  `id` > the last accepted id, and `ts` within 600 s of its clock
  (hifi_remote.c).
- Rating: **Low.** One residue: `last_id` lives in RAM, and before SNTP sets the
  clock the `ts` check is skipped, so a captured command could replay right
  after a reboot. Capturing one needs the relay or TLS broken. Fix (small):
  persist `last_id` in NVS every 64 commands, plus a margin, and refuse
  `update/ota` commands until the clock is set. Recorded; not urgent.
- Leak paths into the relay were the High items above (`/api/v1/remote`,
  `hifi_relay_url` via config.json). Both are closed.

### OTA paths (all of them)

| Path | Authentication before | After |
|---|---|---|
| Relay `ota/begin|chunk|finish` | Signed command, SHA-256 in the command | unchanged |
| Relay `update` (URL) | Signed command; image checked only by its own checksum | unchanged (command is signed; image also signed from now on) |
| LAN `/api/v1/ota/*` | none | **image signature required, no older build** |
| LAN `/api/v1/update` (manifest URL or explicit URL) | none; manifest over http allowed | **image signature required, no older build** |
| LAN `/api/v1/update/upload` (app's file upload) | none | **image signature required, no older build** |
| squeezelite `fwurl` via `/config.json` → recovery → OTA | none | **key refused from the LAN** |
| squeezelite `/flash.json` (recovery upload) | none | **403 from the LAN, in recovery too** |
| console `update` / `recovery` via `/commands.json` | none | **command not allowed from the LAN** |
| Serial console (USB, bench) | physical access | unchanged |

**Signed images.** `build.sh` gives every build a number (`HIFI_FW_BUILD`,
seconds since 1970 UTC, compiled in as the text `HIFI-BUILD:<n>;`) and
`tools/sign_fw.py` appends an 80-byte trailer to `squeezelite.bin`:
`HIFISIG2`, the build number (8 bytes, little-endian) and an Ed25519
signature over `hifi-fw-v2:<build>:<sha256 hex of the image without the
trailer>`, made with the Hive bench key, the same key whose public half is
compiled in as `HIFI_HIVE_PUBKEY`. The ESP-IDF image format ignores bytes
after the image, so older bootloaders and the recovery flasher still accept
a signed file. The board checks the trailer in `hifi_ota_finish` before it
switches slots. An image built without the key, or without a build number,
has no trailer. It can still go in through the relay or serial, never
through the LAN. A first-version (`HIFISIG1`) trailer is refused.

**No going back from the LAN (L9, fixed 2026-11-17).** A signature alone
would let anyone replay an old signed image with a known hole. So, once the
signature checks out (only then are the bytes the Hive's), `hifi_ota_finish`
refuses (403) an image for another project (`esp_app_desc_t`) or with a lower
signed build number than the running app's. The same build again is allowed.
It fails closed: a running app with no build number takes nothing from the
LAN. The relay is exempt: the bench swaps builds both ways, and its commands
are signed and fresh. Decided: a LAN upload of an older firmware is refused;
install it over USB or from the Hive. Decided: images signed before this
change (v1 trailer) no longer install from the LAN; the first update to this
firmware goes over USB or through the relay. Host tests: `test_remote.c`
(same, newer, older, build 0, other project, running build 0, a raised build
number breaking the signature, v1 trailer, descriptor and trailer split over
7-byte chunks, relay path) and `test_sign_fw.py` (an image the tool signs,
checked by the board's own code).

TLS note: the build has `ESP_TLS_INSECURE` + `SKIP_SERVER_CERT_VERIFY` set
globally (upstream default). Our relay and update clients attach the
certificate bundle themselves, so they are verified. The flag only affects
squeezelite's own https fetches without a CA. With image signatures, an
unverified download can no longer install anything. Tracked as Low.

## Fixes in branch `h2/security-surface`

- [x] patch 0010 `lan-hardening` (0009 on `main` is the diag output stats): the gate on every registered URI, no
      `Access-Control-Allow-Origin: *`, `/recovery.json` and `/flash.json` 403,
      the config.json key filter and redaction, the commands.json allow list,
      telnet compiled out
- [x] core: API source (LAN vs relay/console). LAN: `remote` POST 403, and
      `ota/*` and `update*` need a signed image
- [x] `hifi_ota`: trailer parsing and Ed25519 check; host tests (good,
      bad signature, no trailer, truncated trailer, trailer split across chunks)
- [x] `tools/sign_fw.py` + `build.sh` signs when the key is there
- [x] AP: per-board password and AP only when no Wi-Fi is saved (Decided)
- [x] rung 4 "Cannot open the home network": self-test lists every lwIP TCP
      listener and UDP pcb and fails on anything outside the allow list;
      host test with a planted listener
- [x] review fixes (independent Opus review, 2026-11-17):
  - H1: `/commands.json` and `autoexecN` let a second command ride behind a
    newline (`version\nrecovery`): any control character now refuses the line
  - H2: recovery's own `/flash.json` was still open from the LAN: refused
  - H3: router suffixes in the gate (above): only `.local`, `.home.arpa`,
    `.internal`
  - M1: cspot's `/spotify_info` went straight to the server: now gated
  - M2: signed but older images (above): refused from the LAN
  - L1: the gate's hooks are weak references; the hifi component now names
    them with `-Wl,-u` so the linker can't drop them silently
  - L2: the setup AP password is no longer printed to the log
  - L3: a hostname set to a domain (`evil.example`) made that domain count
    as the board's own name: any name with a dot is now skipped by the gate
- [x] second review fixes (another independent Opus review, 2026-11-17):
  - High: `config.json` could set `hifi_bt_save`, and the Bluetooth test boot
    copies it back into `autoexec1`, so a LAN write ran any console command
    (`recovery`, `nvs_set …`) at the next boot. Now the LAN can't write any
    `hifi_*` key, and the copy-back checks the line against the boot rule
    (`hifi_autoexec_ok`: `squeezelite …` or `cfg-*` only); a bad one is
    logged and dropped
  - Medium: `autoexecN = restart` from the LAN would reboot-loop the board:
    LAN autoexec values now pass the same boot rule
  - Medium: during an OTA trial, the LAN could change or forget the Wi-Fi
    (or reset) so the self-test fails and the new firmware rolls back:
    those calls get 409 while `ota` is `pending_verify`
  - Low (fixed): a TCP listener counted only once seen in two scans, so one
    that opened and closed between scans slipped past; TCP now counts from
    a single scan (only UDP needs two, to rule out DNS queries)
- [x] re-check fixes (a third independent Opus pass over the fixes, 2026-11-17):
  - High: `cfg-hw-preset` is a `cfg-*` command, so it passed the autoexec
    rule, but it stores any NVS key it is given (`hifi_relay_url`, `fwurl`,
    `autoexec1`): the LAN could get round the key filter at the next boot.
    `cfg-*` is now an explicit list (`cfg-audio-bt_source`, `-general`,
    `-squeezelite`, `cfg-hw-dac`, `-display`, `-i2c`, `-ledvu`, `-rotary`,
    `-spdif`, `-spi`, `cfg-syst-cspot`, `-name`, `-services`) without it
  - Medium (M1): our word splitter didn't match ESP-IDF's
    (`esp_console_split_argv`), and `squeezelite` took any words: an
    autoexec like `squeezelite -t` or `squeezelite junk` makes squeezelite
    exit, and the board reboots 60 s later, every boot. The splitter now
    copies IDF's rules exactly (only spaces split; a quote groups only at a
    word's start; `\` escapes only `\`, `"` and space), and squeezelite takes
    only its known single-letter options, each value option with a value
  - Medium (M2): a player name with `"` (set through the API, the console or
    squeezelite's form) ended `-n "<name>"` in `autoexec1` and added
    options of its own. The API refuses `"` and `\`; squeezelite's
    `set_name` and the console's `set_squeezelite_player_name` replace them
    with `_` (patch 0010)
  - Medium (M3, was L13): during an OTA trial the LAN could still reboot,
    run the BT test (it reboots), or use squeezelite's `/connect.json`,
    `/reboot*.json`, `/commands.json` and `/config.json` to force a
    rollback. One list (`hifi_lan_trial_refused`) now guards both our API
    and squeezelite's pages (the HTTP gate) with a 409
  - Low: `diag/echo` (up to 1 MB through the Hive's relay) is refused from
    the LAN
- [x] fourth pass (an Opus review of the re-check fixes, 2026-11-17):
  - High: a bare `squeezelite` passed; the console then adds `-?`, which
    prints help and exits, so the board rebooted every boot. At least one
    option is now needed (without `-o` the output is I2S, which runs)
  - High: the console keeps 599 bytes and 27 words of a line
    (`max_cmdline_length` 600, `max_cmdline_args` 28 in
    `platform_console.c`; IDF's `esp_console_split_argv` keeps one slot for
    the NULL) and drops the rest without a word, so the line that ran was
    not the one checked. Longer lines are now refused for `/commands.json`
    and `autoexecN` alike, tested at 27/28 words and 599/600 bytes
  - High: the trial 409 matched the request target as sent, but the server
    routes on the parsed path: `POST http://<ip>/reboot.json` or
    `/reboot.json#x` reached the handler. During a trial, any non-GET whose
    target doesn't start with `/` or holds `#` is now refused (our API
    already 404s them)
  - Medium: two option exits were left: `-n` with `-N`, and `-d` without
    `name=level`. `-N` is refused (no file system for it here) and `-d` must
    give what squeezelite's `strtok` needs
- [x] fifth pass (an Opus re-check of 69be606, 2026-11-17):
  - Medium: the 3000 KB cap missed the crossfade growth of a default
    1450 KB output to 1740 KB; the cap is now 2600 KB with that counted
  - Low/Medium: `-r 0-999999` (a range top above 768000) wrote one past
    squeezelite's `rates[]`; `-r` is now digits and separators only, each
    rate at most 768000, at most 18 values
  - Low: resampling with no `-b` output was refused outright; it is now
    sized as squeezelite sizes it (above)
  - Low, accepted: some odd forms squeezelite would take are refused
    (spaces or signs in numbers, `-r -`, mixed `,` and `-`, 5+ digit `-b`
    parts)
- [x] sixth pass (an Opus re-check of 33d297e, 2026-11-17): no High or
  Medium; nothing accepted writes past an array or allocates more than
  2600 KB. Two Lows fixed in cf63de4: an 18-rate `-r` list left no 0 to end
  the list (reads past it), and a rate under 8000 set I2S to it

Lows (L4-L16; the open ones are known and accepted for now, each with a
suggested fix). Round of 2026-11-17: all fixed except the L4 UDP rule
(deferred) and L5 (the person's decision):

- L4 (scan fixed, UDP rule deferred): the listener allow list lets any UDP
  port at 49152 or above through (the ephemeral range; `hifi_listen.c`), which
  would also hide a UDP service bound there, and the OTA trial passes after
  two clean scans, so a listener that opens later (Bluetooth, AirPlay) is
  never seen by the trial. Fixed 2026-11-17 (6863a91): the 10 s scan, which
  already ran in normal use but only logged, now counts each new or changed
  stray set (`hifi_listen_report`), and `/api/diag`'s net page shows it under
  `listeners` (`strays`, `events`, `open`), so a late listener is visible
  remotely. `test_listen`, `test_diag`. Deferred: the tighter UDP rule waits
  for soak data on which high ports are really in use (cspot, AirPlay
  timing, mDNS answers).
- L5 (the person's decision): the firmware signing key and the relay
  command key are the same Ed25519 key. The signed messages can't be swapped
  (different context prefix, the `v:1` check), but one leaked key does both
  jobs. Fix: a second key, built in as `HIFI_FW_PUBKEY`, that only signs
  firmware and is kept offline; the Hive key then only signs commands. The
  code change is small (a second key list for the trailer check in
  `hifi_ota.c`, and `sign_fw.py --key`), but it moves who can release firmware: whoever holds the offline
  key has to sign each release, so agents could no longer ship an update on
  their own. Not done until the person says who holds it.
- ~~L6: a factory reset from the LAN (`reset` in `/commands.json`) wipes the
  saved Wi-Fi and the AP password, so the board comes back as a setup AP on
  the public default password. Someone on the LAN could do this, then join the
  AP from radio range and pick the Wi-Fi the board joins.~~ Fixed 2026-11-17
  (c3866ea): from the LAN a reset only arms (`hifi_reset_armed`); it happens
  if the board next starts from power-on within 10 minutes, which takes a
  hand on the plug. Any other start disarms it. USB and the relay reset at
  once. `hifi_reset_confirmed`, tested in `test_gate`.
  Decided: a factory reset from the board's web page finishes only when the
  receiver is unplugged and plugged back in within 10 minutes.
- L7: cross-site GETs pass the gate (a browser sends no `Origin` on a
  plain GET or `<img>` load), so a web page can fire read-only GETs at the
  board, without seeing the answer. The written fix doesn't work: browsers
  send `Sec-Fetch-Site` only to https and localhost, never to the board's
  http:// address. Narrowed 2026-11-17 (553466b): the GETs that start work
  (a Wi-Fi scan, which can stutter audio, and the update-manifest fetch) run
  at most once per 5 s from the LAN, else 503 (`hifi_lan_throttled`).
  Read-only GETs stay open; the page can't read their answers.
- ~~L8: the listed `cfg-hw-*` commands are allowed whole from the LAN (they
  set GPIOs, the DAC and the amp). A wrong value can make the board misbehave until it
  is fixed over USB; it can't run code.~~ Fixed 2026-11-17 (e4d6fd7). The
  written fix ("only the keys the UI's forms use") would change nothing:
  squeezelite's UI draws a form for every option of every command from
  `/commands.json`. Instead, from the LAN and in `autoexecN`, a `cfg-hw-*`
  line takes only its command's own options, by exact long name (no short
  options, no abbreviations getopt would widen), and no pin option may name
  the flash (6-11), the PSRAM (16, 17) or a pin the board's firmware holds
  (Lite: PD PG 4, PVDD_EN 18, AMP_SDZ 21, FAULTZ 23, sense 34 and 39; Core:
  the Ethernet, the expander INT, the current sense). Pins are plain decimal
  only. The codec's I2S and I2C pins stay allowed. Tested in `test_gate`.
  Decided: the board's settings page refuses a hardware setting that names
  one of the board's own power, amp, flash or PSRAM pins.
- ~~L9: anti-rollback compares local-time build stamps; a stamp that doesn't
  parse skips the check, equal stamps pass, and the trailer names no board.~~
  Fixed 2026-11-17: a signed build number in the trailer, failing closed
  ("No going back from the LAN" above).
- ~~L10: the OTA session is shared between the HTTP and relay paths with no
  lock; two uploads at once can interleave.~~ Fixed 2026-11-17 (304b522):
  one holder at a time (`hifi_ota_claim`, else 409 "an update is already
  running"), each step under a lock. Tested in `test_remote`.
- ~~L11: `/reboot_ota.json` in recovery boots a fixed OTA_0 slot.~~ Fixed
  2026-11-17 (e737d09, `firmware/patches/0013`): recovery boots the slot that
  last ran well. A board check only (no host test reaches the patch).
- ~~L12: the gate's `Host` port parsing is loose (`host:abc` passes as
  `host`).~~ Fixed 2026-11-17 (553466b): the port is 1-5 digits. `test_gate`.
- L13: fixed (M3 above).
- L14: fixed in 69be606, 33d297e and cf63de4, rated High by the Opus review of d9266b3. An
  autoexec line with `-b 100000:2000`, or with `-u`/`-R` and no output size
  (squeezelite's 1450 KB default times up to 8), made a buffer malloc fail.
  squeezelite then exits and the player stays dead until a reboot. The gate now
  works out the sizes squeezelite ends with (its `next_param` rules, the last
  `-b` wins, 0 or empty keeps the default). It takes a stream of at least
  64 KB, an output of at least 256 KB and at most 2600 KB together. A 1450 KB
  output counts as the 1740 KB a crossfade grows it to. The stock 500:2000
  leaves 1.2 MB of PSRAM free. With resampling and no output size, the gate
  scales 1450 KB by the top `-r` rate / 44100 (1 to 8), as squeezelite does.
  `-r` takes only digits, `,`, `-` and one `:`, each rate 0 or 8000 to
  768000, at most 17 in a list: squeezelite's range form above 768000 writes
  one past `rates[]` and zeroes part of the MAC (fifth pass), and a list of 18
  leaves no 0 to end `supported_rates` (sixth pass). A tab is refused (the console splits on
  spaces only), and the command name is taken from the split words, as the
  console sees it.
- L15 (fixed): `cfg-audio-bt_source` rewrites `autoexec1` itself
  (`cmd_config.c:338-344`) without our check. No way to inject an option
  through it was found: it only cuts a Bluetooth name out of the `-o`
  value of the line already stored (`strip_bt_name`), which was checked when
  it was saved, though the cut could in theory change how the rest splits.
  It also checked the wrong `err` after the write. Fixed 2026-11-17
  (f4eaf63, `firmware/patches/0012`): the line it builds goes through
  `hifi_autoexec_ok` and is not saved if it fails; the right `err` is checked.
- L16 (fixed): during an OTA trial the LAN can still start another update
  (`/api/v1/update/upload`, `/api/v1/ota/finish`, which reboots by
  default). That second image replaces the rollback copy. It has to be
  signed, so only the Hive or the key holder can do it. Fixed 2026-11-17
  (553466b): every update route is in `hifi_lan_trial_refused` (409 during a
  trial). `test_gate`.
- Accepted: the host-less pass (a request with no `Host` header is let
  through, since browsers always send one; this is how `curl` and the dash
  reach the board).

Checked on Lite 4 (lite-4), 2026-11-17 03:00 EST, with both reviews'
fixes in and `main` (56d504a) merged: the signed image went over Wi-Fi and
marked itself good (`ota_valid`). The self-test passes with `listeners: 8
sockets, all allowed`. Internal heap is 15.2 KB free (17.5 KB before this
branch; 15.7 KB before the merge, so `main`'s diag takes about 0.5 KB). The tower
is not on the board's LAN, so the gate is proved by the host tests
(`test_gate`, `test_api`), not by a live probe from another LAN device.
(Since 2026-11-17 a live probe from another Lite exists; see "The network
side and the radio" below.)
Again at 03:18 with the re-check fixes and `h2/lite-bringup` (#192, a
1 KB larger relay stack) merged: `ota_valid`, self-test pass, `8 sockets,
all allowed`, 14.0 KB internal heap free (the 1.2 KB drop is #192's stack;
`hifi_relay` kept 1716 B of its stack free after 8 min, against 716 B before).
And at 03:29 with the fourth-pass fixes (d9266b3): `ota_valid`, self-test
pass, `8 sockets, all allowed`, 14.2 KB free. And at 03:51 with the
fifth-pass fixes (33d297e) plus #192 and #193: `ota_valid`, self-test pass,
`8 sockets, all allowed`, 14.1 KB free (cf63de4 changes only the `-r`
check and is proved by the host tests).

## The network side and the radio (2026-11-17, aeb53)

### Scanning a board from the LAN

The tower (the build machine) is on another network from the boards
(192.168.2.x) and can't reach them, so a scan from the tower can't happen.
The scan runs from a board instead: the lab image's `lanscan` command
(`esp/lab_lanscan.c`, plain-C part `core/hifi_scan.c`, `test_scan`) makes
one Lite probe one other Lite over the shared Wi-Fi LAN. That is the view
any device on the home network has.

- TCP: a raw SYN to every port 1-65535. A SYN-ACK means open, an RST means
  closed, and no answer means silent.
- UDP: an empty datagram to each common port (about 1100 of them: 1-1024
  plus the well-known higher ones such as 1900 SSDP, 3483 Slim, 3702
  WS-Discovery, 5353 mDNS, 5355 LLMNR and 49152-49170). An
  ICMP port-unreachable means closed, a reply means answered, and no answer
  means silent.
- Listening: for the whole scan it keeps every UDP packet from the target,
  multicast included. An mDNS, SSDP (1900) or LLMNR (5355) announcement
  the board sends by itself is heard and logged with its source port.
- lwIP has no firewall: a port nobody holds always answers with an RST or an
  ICMP. So a silent port is either a socket that ignores empty datagrams (68
  and 5353 do) or a lost packet. Wi-Fi drops probes in bursts, so the board
  retries the silent ports, up to 4 passes in all, until a pass gets no new
  answer. The tower tool then rechecks whatever is still silent. A port
  silent in both, and not on the allow-list, fails the scan. When more TCP
  ports stay silent than the board can list (24), the unlisted ones are
  unchecked and the scan is incomplete, never a pass.

What it may scan:
- The board refuses any target that isn't on its own /24 or narrower, and also
  itself, the gateway, and the network or broadcast address.
- The tower tool (`tools/scan_board.py`) takes targets only from the bench's
  diag files of hi-fi Lites, where the file name and MAC agree.
- The result records the target's ARP MAC. A different MAC makes the
  scan incomplete, never a pass.
- The default rate is 300 packets/s.

The verdict compares the result with the allow-list in `core/hifi_listen.c`,
which is the same in the product and lab images:
- TCP may be open only on 80 and 5000.
- UDP may answer only on 5353 and 68, plus 67 and 53 while the setup AP is on.
- No SSDP or LLMNR announcement is allowed.

`scan_board.py judge` exits 0 for a pass, 1 for a fail and 2 for an
incomplete scan. The host tests are `tools/test_scan_board.py`
(26 tests) and `firmware/test` `test_scan` (104 checks).

```
cd projects/hifi-lite-lab/tools
python3 scan_board.py run --from lite-1 --target lite-4 --save
```

First runs, 2026-11-17, all from Lite 1 (lab image `aeb53-lanscan3`, except
run 1). Lite 3, 4 and 5 all run the product image (`lite-main`).

| Target | Verdict | TCP open (of 65535) | UDP answered (of 1093) | Heard from the board | Time |
|---|---|---|---|---|---|
| Lite 4, run 1 (`lanscan2`, 2 passes) | incomplete | 80, 5000; 65440 closed, 93 silent | 5353 | Slim discovery broadcast to :3483 (51×), mDNS replies | 257 s |
| Lite 4, run 2 | **pass** | 80, 5000; 65533 closed | 5353 | the same | 261 s |
| Lite 5 | **pass** | 80, 5000; 65533 closed | 5353 | the same | 262 s |
| Lite 3 | **pass** | 80, 5000; 65533 closed | 5353 | the same | 263 s |

Notes on these runs:
- In run 1, the 8 silent ports the board listed all answered on the tower's
  recheck (closed). The silence was burst loss on the Wi-Fi.
  That run is why the board now retries silent ports itself.
  Re-judged with the current tool, run 1 comes out "incomplete", with
  nothing bad.
- The broadcast to 255.255.255.255:3483 comes from an ephemeral port. It is
  squeezelite looking for an LMS server, a client query and not a listener.
- No SSDP or LLMNR announcement was heard from any board, and nothing
  outside the allow-list was open.
- Not scanned:
  - Lite 1 itself, because it was the scanner. It runs the lab image, whose
    allow-list is the same.
  - Lite 2, which is offline and has no diag file.
- Results: `lab/data/lanscan-*-2026-11-17T07*.json`.

### The radio: what a stranger in range can do to a lab board

The lab image has two radio experiments: ESP-NOW (`esp/lab_espnow.c`) and BLE
(`esp/lab_ble.c`). The stranger here is anyone within radio range, tens
of metres or more with a good antenna. They don't have the Hive key or a
LAN login. Both experiments are off until a run starts:
- ESP-NOW is initialised only for a run.
- BLE advertises (the echo) or scans (the client) only during a run.
- Every run ends by itself at `max_s` (default 120, at most 3600).

| What the stranger wants | Can they? | Why |
|---|---|---|
| Start, stop or change a run | No | Runs start and stop only through the signed `lab` API op. No radio frame is ever read as a command. |
| Read memory | No | The echo sends back only the bytes it received. No radio path returns anything else. |
| Use up memory | No | ESP-NOW frames go into one item made at start and a 32-deep queue of fixed-size items. A full queue drops the frame and counts it (`queue_full`). BLE frames are capped at 244 B (`PAYLOAD_MAX`) into fixed items. Prepared (long) writes are counted and dropped. |
| Crash it through the lab code | No | Every copy is length-checked first: ESP-NOW `n <= HIFI_LINK_MAX`, BLE `<= 244`. a4b54 fuzzes the frame decoders (11170143212c). #199 closed a PONG time overflow, and #201 closes the last one, the `rtt_sum_us` sum. |
| Crash it through the radio stack | Only through a Bluedroid or controller-blob bug | This is reachable only during a BLE run, when the board is connectable. The same code is in `cve-survey.md`. The product image has no BLE at all (below). |
| Skew a run's numbers | Yes | ESP-NOW frames are unencrypted, and the only check is the source MAC, which can be forged. Forged frames from the peer's MAC count as the peer's. Forged PONGs can bend loss and round-trip time. Since #199/#201, a round trip over 10 s counts as bad, and a forged PONG can't use up the real one's sequence number. Flooding the channel shows up as loss and `queue_full`. |
| Stall or fail a BLE run | Yes | The echo turns away every connection whose address isn't the peer's, but the address can be forged. A stranger who connects first as the peer holds the only link. Repeated connects from other addresses each stop advertising until it restarts, which slows the real peer. The client used to connect to anything that copied the peer's public `hl` mark. It now also needs the advertiser's public address to be the peer's (hifi-receiver #203). A forged address still gets through. |
| Keep it off its Wi-Fi | Yes, like any Wi-Fi device | Jamming always works. Forged deauth frames work only if the router lacks Protected Management Frames: IDF 4.4 always uses PMF when the AP offers it (`pmf_cfg.capable` is deprecated and always on). This is the same for the product image and isn't lab-specific. The board reconnects when the air clears. |

Verdict: everything a stranger can do wastes lab time or skews a lab
run's numbers. Nothing reaches the board's controls, its keys, its update
path or its image. Nothing went past that, so the only change is the BLE
client's address check.

Not done, on purpose:
- ESP-NOW encryption (an LMK per pair) would stop forged frames. But it changes
  what the lab measures (the raw link), and the key would sit in the lab
  image's flash.
- BLE pairing would add a bonding step to every run, for a lab-only link.

Both are worth doing only if a lab radio ever carries something that matters.

### The lab radio is not in the product image

The product image (`lite-main` from main's tip, 2026-11-17) was checked
through its map and symbol table. It has:
- no symbol from `lab_*.c`, `hifi_lab`, `hifi_scan` or `hifi_link`;
- no `esp_now_*` call;
- no GATT server or client (`gatts`/`gattc`).

Its sdkconfig has `CONFIG_BTDM_CTRL_MODE_BR_EDR_ONLY=y`, with
`CONFIG_BT_BLE_ENABLED` unset. The four BLE names in the symbol table
(`esp_ble_adv_aa_setting` and three like it) are in the closed controller
library, which links whole, and they are idle in BR/EDR-only mode. The lab
sources compile only under `CONFIG_HIFI_LAB`, and only the `lite-lab`
board sets that.

### Boot heap of the lab image

The lab image before hifi-receiver #203 booted with 11535 B of internal heap free.
That is under the self-test's 12000 B floor (`HIFI_HEAP_MIN`). #203
moves the listener check's tables, the lab state (`hifi_lab_t`) and the
scan's buffers to PSRAM (`EXT_RAM_ATTR`). Lite 1 now boots with 12555 B
free and passes the self-test. The heap's low point during a full scan
was 10759 B in the first run and 11179 B in the second.

Heap headroom (hifi-receiver #206, merged 2026-11-17 as 3cc11f6): every
image now has to boot with at least 16000 B free. #206 trims four system
task stacks (esp_timer and the event loop to 2816 + IDF's 512, the FreeRTOS
timer to 3328, mDNS to 3584) and the Wi-Fi static TX buffers from 8 to 6,
in `lite-main`'s defaults, so the lab image inherits them. The audio path is
untouched. The self-test's heap line now prints the boot heap
(`state.sys.heap_boot`) and its margin over the floor.

| Image | Board | Boot heap | Over the floor | Underruns (tone) | Fleet |
|---|---|---|---|---|---|
| product | Lite 5 | 20515 B (20987 B after a reboot) | 8515 B | 0 in 240 s | a3dbb2fc |
| lab | Lite 1 | 18363 B | 6363 B | 0 in 180 s | 7337732c |
| lab | Lite 4 | 18119 B | 6119 B | 0 in 180 s | 954619f4 |

Before: product about 12.1-13.8 KB, lab 12555 B. The smallest stack margin
left is about 1150 B (the event loop). Lite 3 was in its soak and wasn't
flashed.

#### Shrunk stacks under stress (2026-11-17)

#206 left the four stacks it trimmed with about 1150 B to spare on the
bench, so these runs load the paths a bench session doesn't. The boards
were Lite 1 on the product image (#206) and Lite 4 on the lab image (#206
plus the lab, with PR #208's `wifidrop` experiment). After each step
`api diag/tasks` gave each task's high-water mark, which is the least
stack it has ever had free since boot. The bar is 768 B free.

| Board, image | Step | sys_evt | Tmr Svc | mdns | esp_timer | Heap free / min (B) |
|---|---|---|---|---|---|---|
| Lite 1, product | after an OTA (#206 to #206, trial boot) | 1176 | 1276 | 1708 | 2588 | 19799 / 18615 |
| Lite 1, product | name change, no reboot | 1176 | **1164** | 1708 | 2588 | 19803 / 18615 |
| Lite 1, product | reboot under Lite 4's name (mDNS clash) | 1176 | 1548 | 1552 | 2588 | 19667 / 16823 |
| Lite 1, product | name changed back | 1176 | 1164 | 1552 | 2588 | 19851 / 16823 |
| Lite 1, product | Lite 4 pairs as an A2DP source and connects | 1176 | 1548 | 1824 | 2508 | 18547 / 12051 |
| Lite 1, product | after the A2DP disconnect | 1176 | 1532 | 1824 | **2508** | 18739 / 12035 |
| Lite 4, lab | after an OTA (trial boot) | 1172 | 1548 | **1516** | 2588 | 16971 / 13887 |
| Lite 4, lab | 10 Wi-Fi drops and reconnects (`wifidrop`) | **1156** | 1548 | 1516 | 2572 | 6867 / 5287 |
| Lite 4, lab | fresh boot, 6 drops 60 s apart | 1156 | 1260 | 1564 | 2508 | 11907 / 11375 |
| Lite 4, lab | 4 more drops 10 s apart (10 this boot, 20 in all) | 1156 | 1260 | 1564 | 2508 | 8951 / 8451 |
| Lite 3, product before #206 (soak, read only) | end of the soak, 7.1 h up, 1 Wi-Fi drop, 9 dial-in drops | 1744 | 2012 | 1792 | 3276 | 15155 / 13179 |

The lowest figures are in bold. All four tasks stay above 768 B in every
step, so no stack goes back up. The tightest is the event loop (sys_evt)
at 1152-1176 B. Drops and reconnects take it down only 16 B, from 1172 to
1156. Notes:

- An OTA can't be read while it runs. The serial tab doesn't take
  commands during a fleet flash, and the image chunks go through the relay
  and command tasks, not the four trimmed ones. So the reading comes after
  the trial boot, which runs the new image's start-up and self-test.
- A live name change costs the FreeRTOS timer about 384 B (1548 to 1164)
  and stays the deepest use of that stack.
- mDNS clashes on every boot, because all the Lites share the hostname
  `hifi-lite`. The reboot under Lite 4's name added an instance-name
  clash on top. mdns bottomed at 1516 B. Its 3584 B stack is also why
  CVE-2026-64860 (the recursion fix, below) matters.
- `api wifi/scan` is USB-only, so there were no scans on these remote
  boards.
- Lite 1's product image can't drop its own Wi-Fi remotely: the only way
  is `api wifi`, which rejoins and needs the network password sent again.
  So the drop cycles ran on the lab image. It has the same #206 stacks and
  the same network manager.
- Lite 3 soaked on the product image from before #206, so its stacks are
  the old, larger ones. Its row shows the soak's heap through one real
  Wi-Fi drop and nine dial-in drops, not the #206 margins.

**Fixed: each Wi-Fi drop leaked about 730 B of internal heap.** On the
lab image every drop and reconnect cost Lite 4 708-760 B of free internal
heap. The loss was linear and none of it came back: from one fresh boot,
`api diag` read 16203 B free, then 15443, 14699, 13971, 13223, 12463 and
11755 B after drops 1 to 6. Ten drops 10 s apart took a boot heap of
17363 B down to 6867 B, where the lab's low-RAM guard stopped the run. On
a product board, with about 20 KB at boot, about 11 drops in one boot
would have taken it under the self-test's 12000 B floor, and past about 25
the Wi-Fi driver and TLS would run short: a denial of service that a router
reboot, a flaky AP or anyone able to deauth the board could cause.

Root cause: squeezelite-esp32 starts its Spotify and AirPlay receivers
from a network-manager callback that runs on every connection, not once.
`cspot_sink_start` made a whole new Spotify player each time (a task, its
mutexes and semaphores: about 730 B of internal RAM, plus a 32 KB PSRAM
stack) beside the old one, which never stops. `raop_sink_start` called
`raop_create` again, which couldn't bind port 5000 (the first listener
still had it), returned NULL and lost the running instance's pointer.
Lite 3's soak showed the same step, 736 B for its one real Wi-Fi drop,
on the product image. Its nine dial-in drops over Wi-Fi that stayed up
left the heap alone, which pointed at the Wi-Fi connection, not the relay.

The fix is hifi-receiver patch 0020 (PR #214): both receivers start once
per boot. The first Spotify player stays, and its session reconnects by
itself. AirPlay's listener is bound to the board's address, so
`cb_connection_got_ip` now remembers that address, and the IP-change
reboot it already had can fire. Upstream never stored the address, so
that reboot never ran.

Proof: Lite 5, a lab image with the same network stack and patch 0020,
30 `wifidrop` drops on one boot. Free internal heap started at 29863 B
and ended 100 B under that (heap_ok). Across the 30 cycles the readings
stayed between 29539 and 29987 B, with no step. Before the fix, the
same run would have lost about 22 KB. A heap trace over the run found
988 B in 13 internal allocations still held at the end, which is normal
churn, not a leak. Coming back took 9.5-9.8 s, or 24.5-28.5 s on every
third drop, when the network manager waits for its polling timer.
A second 30-drop run, on the image after the review fixes and through the
`fw hil` plan below, passed 2/2: heap 28095 B at the start, 27983 B at the
end (112 B under, heap_ok), readings 27739-28199 B, no restart
(`reports/hil/lite-5-20261117-wifidrop-result.json`).
`tools/hil-wifidrop.json` runs the 30 drops as a `fw hil` plan and fails
when the heap ends more than 200 B under where it started, so a leak like
this fails a check from now on.

esp-idf issue #8446[^idf8446] was a lead and isn't the cause. It is about
the PHY calibration memory that `esp_wifi_deinit()` doesn't free, a
deinit and reinit path the firmware never takes on a drop. So the fix
needs no IDF change.

[^idf8446]: [esp-idf issue #8446](https://github.com/espressif/esp-idf/issues/8446)

## Patches and CVEs

The full survey, with each source and verdict, is in `cve-survey.md`, dated
2026-11-17. It covers ESP-IDF v4.4.8, mbedTLS 2.28.8, lwIP, Bluedroid and the
BT controller, mdns, cspot/bell, cJSON, nanopb, the codec libraries and
squeezelite-esp32 ee1720c. IDF v4.4 went end of life in July 2024 (no 4.4.9)
and mbedTLS 2.28 at the end of 2024. Any fix after those dates has to be
backported by hand or needs an IDF 5.x bump.

**Backported** (firmware `idf-patches/`, applied by `fetch.sh` to copies of the
`bt`, `lwip` and `mdns` components; the shared IDF is not touched):

| CVE | where | who can reach it | backport |
|---|---|---|---|
| CVE-2026-81507 | Bluedroid L2CAP config options OOB read | anyone in BT range (connectable) | esp-idf c8cd8f4 |
| CVE-2026-46532, CVE-2025-68474 | Bluedroid AVRCP vendor command OOB | a paired phone (just works) | 60f9362f, 565fa98d |
| CVE-2025-66409 | AVRCP/SDP/RFCOMM/SMP bounds | BT range | 075ed218, one hunk by hand |
| CVE-2025-68473 | `bta_dm_sdp_result` OOB write | BT range | 6ca6f422 |
| CVE-2026-45160 | lwIP DHCP server `parse_options` OOB read | setup-AP clients | 8b4b5d53, hand port |
| CVE-2026-64860 | mdns name-decompression recursion (stack overflow on a 4 KB task) | any LAN host, multicast | esp-protocols 95c882c7, hand port |

**Round 2 (2026-11-17, branch `h2/security-lows`).** A second sweep of
everything since 4.4.8 (`cve-survey.md`, "Round 2"). It adds 26 IDF patch
files and 3 firmware patches, all listed in `firmware/idf-patches/README.md`:

- Bluetooth: 30 more Bluedroid fixes in five `bt-r2-*` files (HCI and L2CAP
  from anyone in range; SDP; AVDTP, A2DP and the SBC bitpool check, which
  4.4.8 compiles only in debug builds; AVCTP and AVRCP; GAP, `btm_sec`, SMP).
  One has no upstream fix for 4.4: `btc_avrc.c` puts an array sized by the
  phone (up to about 3 KB) on a 3 KB task stack; it is on the heap now.
- lwIP: 9 fixes (`lwip-r2-*`): TCP out-of-order FIN and option index, IPv6
  reassembly, ND6 address length and Packet Too Big MTU, DHCP client options
  and MTU, DHCP server length and NULL.
- mbedTLS: CVE-2026-49300 (a forged CA bit accepted in a chain), ported to
  2.28 after all. The certificate bundle drops three emSign roots that
  Mozilla removed. It also takes the upstream callback fix
  that accepts a cross-signed root at any depth and bounds the issuer
  compare. Checked first: the relay's chain ends at ISRG Root X1, which stays.
- Wi-Fi supplicant: the WPA3 SAE token (freed but not cleared, length wrap,
  NULL write from a fake WPA3 access point), a constant-time `memcmp`, two
  hardening fixes, and esp_phy's log shim bounds. `esp_netif` NULL checks.
- Firmware patch 0014, AirPlay: RTSP parsing bounds (method and header
  buffers, `Content-Length`, the 1-byte-short AES key mallocs, the DACP
  `strcpy`s), fmtp and DMAP bounds. No upstream fix exists.
- Firmware patch 0015, squeezelite: Ogg Vorbis comment lengths and the header
  buffer, an Opus stream with 0 channels (it rebooted the board), the `setd`
  name. No upstream fix exists.
- Firmware patch 0016: cJSON nesting capped at 16 (CVE-2023-53154 class;
  the default 1000 could run past a task stack) and nanopb nesting at 10
  (GHSA-9w99).

Checked on Lite 4 (lite-4), 2026-11-17 05:55-06:00 EST, build
1794912505 (Hive key set, signed; app at 94% of its slot): the Wi-Fi OTA
marked itself good, the board dialled back in over wss (the relay's TLS
works with the patched mbedTLS and bundle), the self-test passed, and
`diag/net` shows the new `listeners` block (`strays` 0). Internal heap at
idle: 13.4 KB free, minimum 12.1 KB, inside the 12.7-15.5 KB the other Lite
images show. Not checked, because they need someone at the board or gear the
lab doesn't have: the L6 power cycle, the L11 recovery boot, a WPA3 router,
an A2DP/AVRCP soak with a phone, and L9 (an update from the LAN). The board
is back on `esp32-lab-ble7`.

Three independent Opus reviews of the round-2 patches (Bluetooth;
lwIP, mbedTLS and Wi-Fi; the whole PR) found no High. The fixes for what
they found are in `bt-r2-6-review.patch` (an SMP NULL device record, the
AVDT multiplexing copy length, the AVRCP company-ID length) and
`wifi-r2-review.patch` (the SoftAP passphrase NUL at 64 characters, the RRM
and extended-capability IE lengths). Left as upstream has them: the L2CAP
info-response and silent CONFIG_RSP handling, the ND6 half-set address
state. Fixes from the whole-PR review: `cfg-hw-*` pins are held to GPIO
0-39; the L6 arm is a FreeRTOS one-shot timer and is committed to NVS before
the reply; recovery (0013) tries each app slot in turn, the last good one
first; an AirPlay ANNOUNCE with a bad `rsaaeskey` or `aesiv` gets a 503
instead of playing in the clear (0014); an Ogg Opus packet under 8 bytes is
read as audio, and an out-of-memory ends the track instead of spinning
(0015). A fourth Opus pass checked those fixes and found no High. Its
Medium was fixed: an RSA key that fails to decrypt, or a key without an IV
(or an IV without a key), now also gets the 503. So were its Lows: the L6
timer starts before the flag is set and its result is checked, the timer is
created only once when two tasks race, the arm reads the flag back from
flash and undoes itself if it isn't there, the ANNOUNCE mallocs are checked,
and the Opus overflow `memmove` counts bytes, not frames. A fifth pass
checked those. Rebuilt with them (build 1794914922, signed, app at 94%,
recovery at 92%).

- Decided: an AirPlay request whose `Content-Length` is over 1 MiB closes
  the RTSP session (fail-safe; no real sender sends that much). From the
  whole-PR review.

Held or skipped in round 2 (Decided, reversible):

- Decided: keep the Comodo AAA root (upstream 614fb7a6df drops it). A
  server that sends AAA as the top of its chain would fail to connect, and
  AAA expires on 2028-12-31 anyway. Raised by the round-2 review.
- Decided: hold lwIP 6a3a944207 (SYN_RCVD sequence check). Upstream
  committed it on 2026-11-16, and it changes the TCP handshake; take it
  after it has had a soak.
- Decided: hold esp-idf 9669403338 (PTK with no PMK). The caller is in the
  closed Wi-Fi library and can't be checked; if it ever passes an empty PMK
  on a good path, connects would fail.
- Decided: skip nvs 79b93d1db7. It changes how inconsistent entries on
  existing flash are read, and only someone with the board in hand can
  write the flash.
- Decided: skip httpd 370cbcaff9 (only on OOM at startup). The firmware's
  own `components/esp_http_server` would be replaced by an IDF override.

**Already fixed in 4.4.8:** BrakTooth, BIAS, FragAttacks (both advisories),
the Wi-Fi beacon auth bypass, the older zero-PMK advisory (not 9669403338,
held above) and EAP bugs, and the lwIP icmp6
over-read.

**Do not apply here:** lwIP SNMP, MQTT and SMTP apps, its own mdns, 6LoWPAN and
zepif (none are built); BLURtooth (no BLE pairing); the undocumented HCI
commands (CVE-2025-27840: needs code already on the board, fixed in 5.x only);
Kr00k (Broadcom/Cypress); nanopb (0.4.6-dev already has the fixes); the
mbedTLS session-ticket, PSK, ChaCha20 and DTLS issues (all off).

**Open, with reasons** (residual risk):

| item | rating | why it's not fixed in this branch | fix |
|---|---|---|---|
| mbedTLS 2.28 EOL: CVE-2026-54441 (documentation only), CVE-2026-50581 (EMS; unreachable here) | Low | CVE-2026-49300 is backported (round 2). The other two don't reach this firmware | IDF 5.x bump (mbedTLS 3.6.7+), a separate project |
| CVE-2025-27810, CVE-2025-27809 (mbedTLS 2.28.10) | Low | Needs an allocation failure mid-handshake; the hostname check is already set by esp-tls | Ride along with the bump |
| BLUFFS / KNOB (minimum key size 7 octets) | Low | No API in 4.4.8; patching `g_min_enc_key_size_required` in the blob is an untested hack. The link carries only audio | IDF bump |
| squeezelite's own https fetches (`ESP_TLS_SKIP_SERVER_CERT_VERIFY`) | Low | Streams and the recovery OTA. Images are signed now, so a MITM can feed audio but can't install code | Turn off the global flag once every client attaches the bundle |
| Small NULL checks: cspot `TrackQueue.cpp:302`, `AccessKeyFetcher.cpp:84`; `bt_app_sink.c:270` malloc | Low | A crash on a bad Spotify reply or on OOM | Next round |
| WPA3 `sae_pwe_h2e` unset (Espressif AR2026-003) | Low | Interop, not an attack: some WPA3-only routers need H2E | One app line; next round, with a WPA3 router to test |
| 8 large Bluedroid \"AI review\" bundles (60-2270 lines each) | Low | Written against 5.x; the security fixes in them are covered by the single-purpose commits taken | The person's call: port line by line, or leave to the IDF 5.x move |
| Closed Wi-Fi library fixes (9 upstream, incl. ESP-NOW) | unknown | Whether 4.4.8's library has these bugs can't be checked; only a newer library fixes them | IDF 5.x move (the person's decision) |
| helix-aac, tremor, opus | unknown | Prebuilt, versions unknown | Fuzz the decode path, or rebuild from tagged sources |
| cJSON 1.7.17 (CVE-2023-53154, the 1-byte over-read) | Low | Nesting is capped (patch 0016); the over-read itself stays. Only cspot's `ParseWithLength` callers reach it; Bell's own 1.7.15 is not built | Swap in 1.7.19 |
| AirPlay 1 has no password | Low (by design) | Play-only, the norm for home speakers | Optional AirPlay password |
| Recovery `/flash.json` takes an unsigned image | Low | 403 from the LAN now; only serial reaches recovery | Check the trailer in recovery too |
| cspot `/spotify_info` | Low | Spotify zeroconf is meant to be open on the LAN; the gate stops web pages | none |
| Saved Wi-Fi fails at boot (`connect_active_ssid failed`) | Low | The AP comes up at once instead of after 10 min, but with the random per-board password | Route that path through the hold too |
| Spotify TLS: no certificate expiry check | Low | `MBEDTLS_HAVE_TIME_DATE` is off, so an expired or not-yet-valid certificate from a trusted CA is still taken. Needs a stolen, expired Spotify key | Turn it on once SNTP time is known to be set before cspot connects |
| Spotify TLS: the bundle is set up on first use | Low | Two cspot sockets attaching the IDF bundle for the first time at once race on setting it up. cspot opens them one after another today | Attach the bundle once at boot |
| cspot `ApResolve.cpp:35` NULL dereference; no retry backoff | Low | A bad apresolve reply crashes the cspot task; a refused connection (now including a failed certificate check) is retried at once, in a loop | Next round, with the other NULL checks |
| cspot: a second phone's login waits | Low (existing behaviour) | A zeroconf login that arrives while another is still connecting is taken only when that session ends. Since 0028 it is whole, not torn | Hand over after login when the user differs |
| CI skips `test_up_tls` (and `test_up_aphello`) | Low (process) | They need ESP-IDF 4.4.8, which the CI runner doesn't have, so they show as SKIPPED; the job doesn't set `UPSTREAM_REQUIRED=1`. The ALAC, LoginBlob, libmad and layout tests do run in CI. The TLS test was run locally on #223 | Install IDF 4.4.8 in CI (cached), then set `UPSTREAM_REQUIRED=1` |

**Fixed: the four Medium LAN risks** (2026-11-17, aeb53, a3f17's rung in
`goals.md`; each PR had an independent Opus review, and each test fails on
the code before the fix and passes after):

- ~~libalac `numSamples` heap overflow~~ (patch 0021, #225, 5976a65).
  libalac is built from source; `ALACDecoder::Decode` refuses `numSamples`
  above `frameLength` and checks short packets, the skip stride and
  `chanBits`. AirPlay `rtp.c` takes audio, control and timing packets only
  from the RTSP session's sender. Tests: `test_up_alac` (ASan WRITE overflow
  before), `test_up_rtp` (625 checks), a `fuzz_alac` seed. On Lite 5: the
  crafted 300-byte packet panicked the board before; after, it was refused
  64 times out of 64 with the heap intact and no restart.
- ~~Spotify TLS unverified~~ (patch 0023, #223, a0ec9c8). Not through bell's
  `X509Bundle`: on the board `bell::TLSSocket` attaches the IDF certificate
  bundle (`esp_crt_bundle_attach`, the one the relay and updater use) and
  requires verification; if attaching fails it throws rather than connect
  unverified. `test_up_tls` builds the board's `TLSSocket.cpp` on the host
  against IDF 4.4.8's mbedTLS: stock takes a self-signed and a wrong-name
  certificate (1/3), patched refuses both (3/3), and reaches
  apresolve.spotify.com live. Still to do: play Spotify on the product
  image (a phone with the app is needed). squeezelite's own
  `ESP_TLS_SKIP_SERVER_CERT_VERIFY` stays Low (row above).
- ~~cspot LoginBlob race~~ (patch 0028, #228, a73ebfa). `blobMutex` around
  the zeroconf GET and POST handlers; the cspot task logs in from its own
  copy (`loginSnapshot()`); `loadZeroconf` builds the account in locals and
  sets it only once the whole blob has parsed, so a refused or half-copied
  login leaves the old one. `test_up_blob` (ThreadSanitizer): a POST during
  a login, and a POST while the snapshot is taken, are clean patched; the
  unpatched build, and one with only the POST lock removed, hit a data race
  10 times out of 10. On boards (lab image `aeb53-blob-6028509`): Lite 2 and
  Lite 5 took the update over the air, were marked good and passed the
  self-test. Their lowest free heap since boot was 13871 B and 13515 B, under
  the 16384 B warning line though above the 12000 B floor. That low point
  includes the OTA download itself; it wasn't compared with the image before
  0028.
- ~~libmad 0.15.1b (CVE-2017-8372/8373/8374)~~ (patch 0029, #229, ea683a9). libmad is
  built from the stock 0.15.1b source with Debian's `md_size.diff` and
  `length-check.patch` (added in 0.15.1b-9), ported to the board's
  inline-buffer `mad.h`; recipe in `firmware/third_party/libmad/README.md`.
  Correction to the row this replaces: the `main_data` overflow needs a
  frame bigger than the 2048 bytes squeezelite's `mad.c` reads at a time, so
  normal playback can't reach it; the lab's whole-buffer decode can. The
  playback risk was the reads past the end of the input, which
  `length-check` fixes. `make mad` (ASan): crafted frames overflow stock and
  are refused by the fix; `sine.mp3` decodes to the same PCM in both;
  `fuzz_mad` finds a heap over-read in stock and nothing in the fix;
  `mad_layout` checks the struct sizes, offsets and types against `mad.h`.
  On Lite 2 the lab image built from source (`aeb53-mad-996aa04`) took the
  update over the air, was marked good and passed the self-test. The crafted
  MP3 on a board (#233, c255b60, 2026-11-17): the `crafted` op now takes data in
  pieces (`more`, `offset`), since a bench command is 512 characters and the
  frame is 2871 bytes. On Lite 5, frame511.mp3 in 11 pieces with `repeat`
  64: the image without the fix (patch 0029 taken out, stock `libmad.a`)
  restarted in the decode (`crashed_in` "crafted"); the fixed image refused
  65 of 65 calls with the heap intact and no restart.

**Fixed: an IP change during the trial rolled updates back** (2026-11-17,
aeb53, a3f17's follow-up; patch 0030, #230 into `main`, f37c566). Patch
0020's reboot on a new address ran before the image was marked good, so a
router handing out a new address in the first two minutes rolled every
update back. The reboot is now held while the image is on trial (AirPlay
stays on the old address meanwhile) and runs once the image is marked good,
after the settings are committed. The trial state is kept in RAM, because
otadata is blank while mark-valid rewrites it. `test_remote`
`test_restart_hold`, and `test_up_source`: 6 of its 19 checks fail on the
old code, 19/19 pass. Opus-reviewed. h2/lite-bringup doesn't have it yet; it
comes over when the branches merge (one of the two patch 0021s needs a new
number then).

## Moving to ESP-IDF 5.x (the person's call (b))

Written 2026-11-17 as a plan. Nothing is ported. The numbers come from the
firmware tree at build 1794914922 and from test builds with IDF 4.4.8 and
5.5.6 on the tower.

**Why it comes up.** IDF 4.4 went end of life in July 2024. Each IDF minor
release is supported for 30 months
(https://github.com/espressif/esp-idf/blob/master/SUPPORT_POLICY.md). So
every fix we take now is a hand backport, and the closed Wi-Fi and Bluetooth
libraries can't be fixed at all.

**Which version.** The target is 5.5 (5.5.6, released 2026-10-16, is
installed at `~/.cache/fw/esp-idf-v5.5.6`). Version 5.5 came out on
2025-07-21, so its support ends around January 2028. It is the last line
that still has the legacy I2S, I2C, RMT, ADC and timer drivers (under
`driver/deprecated`). 6.0 and 6.1 remove that directory, which would add a
full driver rewrite on top (about 60 files, listed below). Going to 5.5
first and to 6.x later splits the work into two steps that can each be
tested.

**Upstream has no port to follow.** sle118/squeezelite-esp32 has no IDF 5
branch: master-v4.3 (our base, ee1720c) builds on 4.3/4.4, and the
`refactoring` branch is still on 4.4.6. In issue #468
(https://github.com/sle118/squeezelite-esp32/issues/468), co-maintainer
philippe44 calls v5 "HIGHY incompatible", with "a boatload of changes on
network and BT". He tried the network part, called it "a nightmare" and
gave up. sle118 would take v5 only on top of `refactoring`. The one
community attempt (emlynmac, branch `esp-idf-5.4`) stopped on 2025-03-14,
23 commits in. The breaks he lists are mbedTLS 3 needing an RNG, the
reworked I2S driver, networking, Ethernet left off, and the stricter `%d`
formats.

**What breaks.** Counts are files / lines in our components, leaving out
the IDF component copies that `fetch.sh` patches:

| area | files / lines | what has to change |
|---|---|---|
| components removed from the IDF | — | `mdns`, `libsodium`, `esp_websocket_client` move to the component registry. `tcpip_adapter`, `openssl`, `nghttp`, `expat`, `esp_adc_cal` are gone |
| OpenSSL shim (`openssl/ssl.h`) | 5 / 23 | civetweb, squeezelite `stream.c`, AirPlay `raop.c` and `rtp.c` must be rewritten on mbedTLS or esp-tls. This is the biggest single item, and it sits on the network paths patch 0014 hardened |
| mbedTLS 2 → 3.6.7 API | 5 / 19 | private struct fields, an RNG on every `pk_parse_key` and `pk_sign` (`raop.c`, bell `TLSSocket.cpp`) |
| `tcpip_adapter` → `esp_netif` | 6 / 23 | wifi-manager, Ethernet, the relay |
| mdns (registry component) | 11 / 23 | mostly the include and the dependency, and CVE-2026-64860 is fixed there |
| esp_eth 4.x constructors | 3 / 7 | the MAC and PHY config structs changed (patch 0002 sits here) |
| legacy I2S, I2C, RMT, ADC | 27, 25, 7, 4 files | still build on 5.5 with deprecation warnings. They must move to the new drivers before 6.x |
| FreeRTOS v8 names (`xTaskHandle`, `portTICK_RATE_MS`) | 33 / 73 | the compatibility option covers them on 5.5 |
| OTA and image API (`esp_ota_*`, `esp_image_*`) | 11 / 53 | recovery, L9/L11, the signed-trailer check |
| SPI flash API | 5 / 7 | `spi_flash_*` → `esp_flash_*` |
| Bluetooth API | 2 / 11 | small renames in the sink and source |
| `%d` with 32-bit types | 67 / 296 | GCC 14 with `-Werror=format`, mechanical |

That is about 100 files plus the formats. On top of it, all 16 firmware
patches (0001-0016) touch code that moves, so each has to be re-ported and
its host test re-run.

**What it retires.** All 29 files in `firmware/idf-patches/`
(`fetch.sh` refuses any IDF but 4.4.8). 5.5.6 already has every upstream
fix in them. Our own four fixes are in 5.5.6 too: the AVRCP parameter
array is off the stack, the AVDT copy length, the 3-byte company-ID length,
and the SMP NULL device record (checked in the 5.5.6 sources). It also
closes these open rows above: mbedTLS 2.28 end of life (3.6.7 has
CVE-2026-54441, CVE-2026-50581, CVE-2025-27809 and CVE-2025-27810 fixed),
BLUFFS/KNOB (`esp_bt_gap_set_min_enc_key_size` exists in 5.5.6), the closed
Wi-Fi library fixes, the 8 Bluedroid review bundles, and CVE-2025-27840. It
does not fix the firmware's own code (libmad, libalac, cspot, the Spotify
TLS row): those stay open either way.

**Size against the slots.** The test builds were Espressif's examples built
with `-Os`, log level Info, and the same config on both versions:

| example | 4.4.8 | 5.5.6 | change | static IRAM |
|---|---|---|---|---|
| `https_request` (Wi-Fi, lwIP, mbedTLS, esp-tls) | 736,112 B | 813,936 B | +77.8 KB (+10.6%) | +8.7 KB |
| `a2dp_sink` (Classic BT; 5.5's version is `a2dp_sink_stream`) | 857,776 B | 904,400 B | +46.6 KB (+5.4%) | +8.7 KB |

Where it comes from, per archive, in the Wi-Fi build: the closed Wi-Fi
libraries grow about 70 KB (`net80211` +33 KB, `wpa_supplicant` +19,
`pp` +11.5, `phy` +6). newlib shrinks about 23 KB. lwIP and mbedTLS are
within 5 KB each. In the BT build the controller grows 8 KB.

Our image splits into about 1.68 MB that comes from the IDF and the
toolchain (Wi-Fi and BT libraries, Bluedroid, lwIP, mbedTLS, newlib,
libstdc++) and about 1.25 MB of our own code (squeezelite, codecs, cspot,
the receiver). Applying the measured changes to that split gives:

- **App slot (ota_0/ota_1, 3,211,264 B):** 3,014,320 B now (94%), 196 KB free.
  The estimate is +60 to +170 KB, most likely about +100 KB. That puts it at
  about 97% (96% to 99%). It fits, but with almost no room left for features.
- **Recovery slot (1,835,008 B):** 1,692,096 B now (92%), 140 KB free. It
  carries the same Wi-Fi and TLS stack, so expect about +70 to +100 KB,
  landing at about 96-98%.
- **IRAM is tighter than flash.** The app uses 115.8 KB of 131 KB static
  IRAM (15.3 KB free), and both test builds added 8.7 KB. Most likely
  that leaves about 6 KB. Any further growth would need the Wi-Fi or lwIP
  IRAM options turned off, which costs throughput.
- **Heap is the real unknown.** Internal heap at idle is 13.4 KB free now
  (12.1 KB lowest). Static DRAM grew 2-3 KB in the test builds, and mbedTLS 3
  handshakes need about as much. Only a board run can tell whether the TLS
  dial-in still has room.

If it doesn't fit, the slots can't grow. The 8 MB of flash is fully
allocated, and an OTA can't change the partition table, so new slots would
mean a serial reflash of every board. What could be trimmed instead
(sizes are from the current map): MQTT (16 KB, if nothing uses it), the
display driver (62 KB, if the Lite has no display), the console (39 KB),
log level Warning, Ethernet (42 KB, if the Lite never uses wired).

**Boards in the field.** An OTA replaces only the app slot. Existing boards
keep their 4.4.8 bootloader, and their 4.4.8 recovery stays in the factory
slot until someone reflashes it over serial. Three things have to be tested
on a board before shipping: the 4.4.8 bootloader boots a 5.5 app; recovery
(4.4.8) still reads the NVS and otadata that the 5.5 app writes; and our
signed-trailer check (L9) works the same in both directions. A rollback from
5.5 to a 4.4.8 image has to be tested too.

**Effort.** This is a port nobody upstream has finished, so it's a guess.
About 1-2 weeks of agent work gets to a clean build. Then come the parts
upstream gave up on, which take board time: Wi-Fi and Ethernet bring-up,
A2DP and AVRCP with a phone, AirPlay and squeezelite streaming on the new
TLS layer, the relay dial-in, OTA and recovery across the version line, and
a heap soak. Realistically that is 4-6 weeks from start to a build that can
be shipped. After that we carry the fork ourselves, with no upstream to
merge from.

**The alternative: stay on 4.4.8.** Keep backporting as in rounds 1 and 2,
at about a day of agent work plus reviews per sweep. What can't be
backported stays open: the closed Wi-Fi and BT library fixes, BLUFFS, and
mbedTLS 2.28 after the next CVE that doesn't port. The gap grows with each
upstream release.

**Recommendation, for the person to decide:** stay on 4.4.8 with backports
for this year's boards, and start the 5.5 port as a separate project only
if the Lite is going to ship to people outside the house. Run it as a lab
with a board on the bench, since the risk is in behaviour, not in the
build. If it starts, the first step is cheap and answers the size and heap
questions: build the image on 5.5 with the legacy drivers, the
compatibility options and the OpenSSL users stubbed out, and measure
flash, IRAM and heap on Lite 4 before porting anything else.

## Secure boot and flash encryption (the person's decision)

Both burn eFuses, which can't be undone. Nothing here is done; this is the plan.

- **What they add:** secure boot v2 (RSA-3072, ESP32 rev3 / ECO3 needed;
  check the chip revision first) makes the ROM refuse a bootloader or app
  not signed with our key, which stops a flashed-over-serial image too.
  Flash encryption (release mode) makes a desoldered or dumped flash
  unreadable: the Wi-Fi password and the relay token are in NVS in the clear.
- **What it costs:** after release mode, serial flashing of plain images
  stops for good. Every image must be signed with a key that must never be
  lost (losing it bricks updates). The bench's "flash over USB to recover"
  path becomes "OTA only". Development mode keeps serial reflashing but
  protects little.
- **Recommendation:** not for the bench boards (Lite 4/5). For boards that
  leave the house, burn secure boot v2 + flash encryption in release mode at
  the factory step, with the signing key held offline (not on the tower).
  The LAN-side fixes above cover the realistic home threat without any burn.
