# Party mode on the Hi-Fi Lite: design study

Status: design reviewed (Opus, section 7) and corrected. The prototype is in PR 221 of the hifi repo, and two
Lites have played the same stream for 10 min with a measured offset of ±23 µs (5.1). Bluetooth as a party source
fits in RAM: with the real worker (#241) the leader kept 35.8 KB at the lowest over 16 min of A2DP plus party,
and a second follower costs about 50 B (3.7, 5.1). But A2DP on the leader hurts the send: the follower concealed
about 4% of frames, against none without Bluetooth (5.1). Still open: an independent offset check (RCA cable or microphone).
Thread: lite-party-mode.

**Goal.** Several Lites play the same music in sync with no server. One board leads, the rest follow.
The aim is an offset under 1 ms between any two boards. Joining must not open the home network.

**Short answer.**
- The leader taps what its own squeezelite is about to play, after the volume and fades, and sends it as PCM over
  Wi-Fi (UDP unicast through the AP) with stream frame numbers.
- It delays its own playback by 300 ms so the followers get the same lead.
- The common clock is the Wi-Fi TSF, the AP's 1 MHz beacon timer, which every board on the same AP already
  shares to a few µs. A board that loses it (another AP, a TSF step) re-locks; a mesh is out of scope for v1.
- Each board times its own DAC against that clock from its I2S writes. Followers correct by dropping or
  repeating one frame per 46 ms block, as needed.
- Any source the leader can play works, because the tap sits after the decoder: Spotify, AirPlay, LMS, Bluetooth
  and the line in.
- Cost per follower: about 10% of the channel's airtime. The leader needs about 230 KB of PSRAM, each follower
  180 KB, and one extra task of about 4 KB stack (PSRAM stack where the recipe allows).

## 1. What the board has to work with

| resource | number | source |
|---|---|---|
| PCM 44.1 kHz stereo 16 bit | 1411 kbit/s = 176.4 KB/s; one frame = 22.7 µs | arithmetic |
| PSRAM free with a stream up | about 1.2 MB, largest block 1.18 MB (OUTPUTBUF 1450 KB already in PSRAM) | heap report, esp32-lab lane |
| internal heap free | about 13 KB in the product image; about 24.8 KB with a4b0e's PSRAM-stack recipe (patch 0019) | esp32-lab lane |
| I2S DMA ring | 12 × 256 frames = 70 ms (32-bit slots, so the hifi patch halves `dma_buf_len`; the driver's free queue holds 11), plus an output block of up to 2048 frames (46 ms) | `output_i2s.c:353`, IDF `i2s.c:864` |
| I2S clock | APLL (`use_apll = true`), from the 40 MHz crystal | `output_i2s.c:306` |
| crystal drift between two Lites | −2.9 to +23.6 ppm, but from 20 s runs; to be measured again over ≥ 10 min | `lab/espnow.md` |
| UDP throughput, lab air | about 30 Mbit/s | esp32-lab lane |
| ESP-NOW with BT off and ps none | 0% loss at 3.6 to 4.1 Mbit/s; with BT on, the station is held in modem sleep and loses frames | `lab/espnow.md` |
| RTT board to board through the AP | p50 5.7 ms, p95 25 ms, p99 42 ms | HIL report 2026-11-17 |

Two facts shape the design:
1. **A drift of 26 ppm is 1 ms of offset every 38 s.** Starting the boards together is not enough. Each follower
   must keep correcting, by about one frame a second.
2. **Each board already has 70 to 116 ms of audio queued ahead of its DAC** (the DMA ring plus the block being
   written). A follower needs a frame that early too, so the leader has to delay its own output by the network
   budget it wants to give its followers.

## 2. Options compared

| | A. squeezelite slimproto sync | B. leader sends PCM over UDP, TSF clock (chosen) | C. leader sends over ESP-NOW | D. BLE or ESP-NOW control, Wi-Fi audio | E. snapcast-style, board as server |
|---|---|---|---|---|---|
| needs a server | yes, LMS (sync lives in the server) | no | no | no | no server box, but the leader runs a snapserver role |
| sync accuracy | LMS reports ±6 ms tuning by hand; no published figure for ESP32 players | target < 100 µs (TSF to a few µs, DAC timing filtered, ±1 frame steps) | the same as B; the clock is the ESP-NOW receive time or TSF | the same as B, since audio and timing are still on Wi-Fi | snapclient corrects by one sample per chunk; it reports ms-level sync, needs IDF 5.x |
| airtime per follower | — (each player pulls the stream from LMS) | 9.6% at MCS7 to 12.7% at MCS4 (two hops through the AP) | unicast 14.7% per follower at 54 Mbit/s; broadcast 11.6% for all followers together | B plus a little | like B |
| loss handling | TCP | NACK and resend within a 300 ms budget, then concealment | unicast: MAC retries; broadcast: no ACK, needs FEC | as B | TCP |
| leader RAM | — | 53 KB PSRAM delay line + 176 KB resend history | the same | as B | a full server: too big |
| follower RAM | its own decoder | 180 KB PSRAM jitter buffer, no decoder running | the same | as B | as B |
| sources | only what LMS plays | anything the leader plays | anything the leader plays | anything the leader plays | anything |
| what it costs us | nothing to build; keeps the server | a new component and a short squeezelite patch | B plus FEC; followers must turn BT off and stay at ps none | B plus a second radio protocol | a port to IDF 5 |
| network opened | LMS's ports | nothing (LAN unicast only, signed) | nothing (802.11 action frames, signed) | nothing | snapserver ports |

**A** is not party mode: the server does the sync. It stays the answer for a home that already runs LMS.
**E** needs a port to a newer IDF and a server process on the leader. **D** adds a radio without solving
anything B doesn't: control rides the same UDP socket with no extra cost.

**C** is the real alternative to B. ESP-NOW broadcast is the only option where airtime does not grow with the
number of followers. But it is limited:
- frames hold at most 250 bytes on IDF 4.4, so about 706 frames a second for one stream;
- broadcast has no ACK, so it needs FEC;
- the followers must give up Bluetooth and power save to receive cleanly (`lab/espnow.md`).

C is the v2 candidate for groups of four or more boards. B is v1.

**Multicast through the AP** looks right but is not. APs send group traffic at the lowest basic rate (often
1 or 6 Mbit/s) and with no ACK. When any station is in power save, they hold it until the DTIM beacon
(RFC 9119 §3). A 1.4 Mbit/s stream at 6 Mbit/s would take a quarter of the channel. ESP32 forum reports of
multicast loss match this.

### Airtime arithmetic (option B)

Per packet: 352 frames = 1408 B of audio, plus a 38 B party header, an 8 B tag, 28 B UDP/IP, 8 B LLC, 26 B MAC
and 4 B FCS, which comes to about 1520 B. That is 125 packets a second.

| rate | data time | + preamble 36 µs, DIFS 34, mean backoff 68, SIFS 16, ACK 28 | per hop | per follower (leader→AP→follower) |
|---|---|---|---|---|
| MCS7 HT20, 65 Mbit/s | 187 µs | 369 µs | 4.6% | **9.2%**, with headroom about 9.6% |
| MCS4, 39 Mbit/s | 312 µs | 494 µs | 6.2% | **12.4%**, about 12.7% |
| 802.11g 24 Mbit/s | 507 µs | 689 µs | 8.6% | 17% |

Three followers at MCS4 use about 38% of the channel. That is the practical limit for v1
(Bluetooth is out for v1, 3.7). A home network shared with video calls should stay at two or
three followers, or use the v2 compression below.

Two v2 savings:
- A lossless fixed predictor with Rice codes comes to about 60% of PCM on music. That costs about 2% of one core
  at 44.1 kHz, with no tables.
- SBC at 328 kbit/s cuts the stream to a quarter, but it is lossy and costs about 10% of a core.

Neither is needed for two or three boards.

## 3. The chosen design (option B)

### 3.1 Clock: the Wi-Fi TSF

Every station on a BSS keeps a 64-bit µs counter, the TSF, set from the AP's beacons (802.11 §11.1). Inside one
BSS, stations agree to within a few µs plus their own drift between beacons; the AVB study of 802.11n timestamps
measured jitter in that range. `esp_wifi_get_tsf_time()` reads it; one call takes about 48 µs on the ESP32, so
each read is bracketed between two `esp_timer_get_time()` reads and the bracket's width is the sample's quality.

- Each board keeps a linear map from its local µs timer to TSF, fitted over the last 30 s through the narrowest
  bracket of each second (offset and rate).
- **TSF steps.** The TSF is not a steady clock in every case:
  - in modem sleep (forced while Bluetooth is up) the station corrects it only at the beacons it wakes for, so it
    can step when beacons are missed;
  - it restarts when the AP reboots or changes channel;
  - each AP has its own, so roaming changes it.

  A step shows as a sample far off the fitted line. Three windows in a row off the line, or a new BSSID, reset the
  fit: the follower mutes, relocks and jumps back in. Every CLOCK message carries the leader's BSSID, and a
  follower whose BSSID differs does not play (it reports why). So the group must sit on one AP; v1 tells the
  user to pin it. The signed UDP ping clock that `hifi_link` has is the v2 answer for a mesh, at a few ms.

### 3.2 When does a frame reach the DAC?

The squeezelite output thread writes up to 2048 frames per `i2s_write_expand`. The driver copies into its
current DMA buffer of `dma_buf_len` frames (256 on the Lite), and at each multiple of that it takes the next
free buffer from a queue that the DMA's end-of-buffer interrupt fills, waiting when the queue is empty.

The free time of the buffer that frame b (a multiple of the buffer length) goes into depends only on b: that
buffer frees when the DMA finishes what it held a ring earlier. So if the output never runs dry, a write of
frames [w0, w1) that returns at t1 gives the sample (b, t1), where b is the last boundary in [w0, w1). t1 is
never earlier than b's buffer freed, only later (task wake-up, the copy after the boundary, a pre-empting Wi-Fi
or Bluetooth task). From this, each board:
- takes one such sample per write (`hifi_party_dac_sample`), converting t1 to TSF through 3.1's map;
- fits frame index against TSF through the **lower envelope**: the earliest point in each 1 s window, then a
  least-squares line through 30 windows;
- uses the line's slope as its I2S rate against TSF, and its intercept as the TSF at which any frame plays.

The constant between "buffer frees" and "frame plays" (a ring of buffers) is the same on every Lite running the
same code, so it cancels between boards. This logic lives in a pure C file and is tested on the host with
synthetic latency, including late writes and busy seconds.

**When the map breaks, start again.** Four things break the link between the written count and the DAC
(review finding 2): the START_AT discard (frames that never reach `i2s_write`), `i2s_stop`/`i2s_start` on OFF,
a sample-rate change (the driver resets its queue), and the DMA running dry (it drops a whole buffer). So:
- the write count W counts only frames handed to `i2s_write`, after the discard;
- `i2s_start`, a rate change, and a fit whose best samples sit off the line for three windows (the sign of a
  dry DMA) each reset W's buffer alignment and the fit, and the board re-learns its line (a follower mutes
  meanwhile);
- v1 runs at a fixed 44.1 kHz; the leader resamples nothing, and a stream at another rate stops the party.

The DAC's own pipeline (ES8388 digital filter) adds the same delay on identical boards. The EQ runs after the
hook (3.3, 3.4), so boards with different EQ settings differ by the phase of their EQ filters (well under 1 ms
at audio frequencies, except near a sharp bass cut). The power governor (`hifi_dsp_process`) also runs after it,
per board; it changes level, not timing.

### 3.3 Leader

One hook goes in the output thread of `output_i2s.c`, after the START_AT discard and before the equalizer, where
`obuf` holds exactly the frames about to go to `i2s_write`:
- **Tap.** The block is numbered by a running count s and handed to the party sender. It is taken after
  `_apply_gain`, so it carries the replay gain, fades, crossfades and the leader's volume: the group follows the
  leader's volume, and each follower plays at unity (`hifi_take_output` with unity). A per-board trim can stay.
- **Delay.** The same block goes into a PSRAM delay line of D = 300 ms (53 KB), and the block from 300 ms earlier
  comes out into `obuf`. So stream frame k plays on the leader when its write count reaches k + D: k = W − D,
  give or take the anchor (3.2).

Because the tap and the delay sit at the same point, a discarded frame is neither sent nor delayed (review
finding 5).

The sender, a task at a priority below the output thread:
- packs 352 frames per packet with a 38 B header `{magic, version, type, length, group, seq, k, t, rate, n,
  flags}` and an 8 B HMAC tag;
- sends each packet once to each follower, by UDP unicast from a fixed LAN port;
- keeps the last 1 s of packets (176 KB of PSRAM) to answer NACKs;
- sends a **clock message** every 200 ms: the TSF at which stream frame k_ref plays on the leader's DAC (from
  3.2), the rate of the stream against TSF, and its BSSID. This is the RTCP sender report of RTP, in TSF units.

Pause, track change and volume need no extra messages:
- silence is part of the stream, so a pause is silence;
- a track change is a jump in the stream, and it plays on the followers at the same TSF;
- the volume is in the samples.

### 3.4 Follower

A follower runs no decoder. It takes the output the way the line in does (`hifi_take_output` from patch 0001,
with a new `DECODE_HIFI_PARTY` kind that `hifi_owns_output` also counts, so squeezelite doesn't take the output
back), at unity gain. Its own sources stop, and the amp and LEDs behave as usual. With nothing in `outputbuf`,
squeezelite writes 2048-frame blocks of silence, paced by the DMA, and the same hook as the leader's fills each
block from the jitter buffer before the EQ.

- **Receive** into a PSRAM jitter buffer of 1 s (176 KB), indexed by stream frame. Missing packets are NACKed
  20 ms after a gap appears, and again after 60 ms. A gap still empty when it reaches the output is filled by
  repeating the last 5 ms with a fade (packet-loss concealment).
- **Output hook:** for the block about to be written at W0, it takes the TSF at which W0 plays (its own fit, 3.2)
  and the leader's last CLOCK. The target is `k_ref + (TSF(W0) − T_ref) · rate`, and `err` is the target minus
  the index the follower was about to play.
  - |err| ≥ 1 frame and < 5 ms: drop one frame (behind) or repeat one (ahead) in this block, at the quietest point
    of the block, at most once per block. 1 frame per 2048 is 488 ppm, 20 times the worst drift seen.
  - |err| ≥ 5 ms (start, a lost clock, a TSF re-lock): jump straight to the target index with a 5 ms crossfade.
- At a 26 ppm difference, the follower slips one frame (22.7 µs) about every 0.9 s. Snapcast's ESP32 client works
  the same way, and single-frame slips at that rate are inaudible on music.

The review proposed feeding `outputbuf` through `sink_data_handler` instead (finding 6). That reuses more of
squeezelite, but it puts squeezelite's own buffering (its start threshold, its fill level) between the jitter
buffer and the DAC, so the frame that reaches the DAC at a given write would no longer be known. The hook keeps
that known, and the volume, EQ and governor still run after it.

- **v2: APLL trim.** The ESP32's APLL fractional divider steps by about 1 to 5 ppm. Pulling it to the leader's
  rate would remove the slips. Whether it can be changed while I2S runs, without a glitch, is not yet verified,
  and the errata limit the APLL's range, so v1 does not depend on it.
- **v2: the I2S event queue.** Installing the driver's event queue (squeezelite passes none) would give a
  TX_DONE event per buffer. Its time is still read by a task after a wake-up, so it would not beat the write
  returns by much; an ISR-side stamp would.

### 3.5 Joining and security

- **The network stays as it is.** Nothing uses UPnP, NAT-PMP or any router setting. Party traffic is UDP
  unicast between LAN addresses, on a socket bound to the station interface only (never the provisioning AP).
  A board opens no port while party mode is off.
- **Only boards that hold the key.** Every party packet (audio, clock and NACK) carries an 8 B HMAC-SHA256 tag
  over the header and payload, compared in constant time. The tag's key is a fresh random 16 B group key,
  drawn each time a group starts. The Hive key a board holds is a public signing key, so it can't key an HMAC
  itself; instead the group key reaches each member inside a command signed by the Hive key, over the board's
  own dial-in (a TLS WebSocket), as the lab's link runs already get their keys. A board takes a group key only
  from such a command, the key never crosses the LAN in the clear, and a board without one cannot join, forge
  the group's control traffic, or inject audio into it.
- **Replay:** within a group, a follower drops anything behind its play position or already held. Each start
  has a new group key, so a capture from an earlier group fails its tag.
- **NACKs** are signed like everything else, list at most 16 packets, and the leader resends at most 1 s of
  history per peer per second, only to a follower it was told to serve. So a NACK can't make the leader
  flood anyone.
- **The source address** is checked against the leader (or the followers) the board was told of. That is a
  filter for stray traffic, not a defence: the tag is the defence.
- **Pairing without the Hive key** (a guest board, a v2 question): the leader shows a 6-digit code, the guest
  enters it, and a SPAKE2-style exchange gives both the group key. Not needed for the lab.
- **Audio confidentiality:** the PCM is signed but not encrypted in v1. Anyone on the same Wi-Fi can already hear
  the room, so the threat is injection, not eavesdropping. AES-CTR costs about 3% of a core, and v2 can add it.
- **Discovery:** a follower is told its leader's address and the group key by a signed command (`api party
  join` in v1, `api lab` in the prototype), which only the dash or the Hive can send. There is no mDNS browse in
  v1, so nothing extra is announced on the LAN.

### 3.6 Sources

The tap sits after every decoder, so the leader can lead from:
- LMS;
- Spotify (cspot);
- AirPlay (RAOP);
- Bluetooth A2DP;
- the line in;
- the test signals.

Two caveats:
- **Bluetooth on the leader** shares the radio with the Wi-Fi stream. The ESP-NOW study saw Wi-Fi loss with BT on.
  A2DP on the leader plus a 1.4 Mbit/s send is the riskiest case. On the lab image it ran out of internal RAM;
  on the product image with #232 and the real worker it keeps 35.8 KB at the lowest (3.7, 5.1). A new A2DP
  connection while the leader sends works (6 of 6). But with A2DP up the leader's sends fail in bursts and its
  follower conceals about 4% of frames, rising over the run; without Bluetooth, none (5.1). Not solved yet.
- **AirPlay already delays** by about 2 s, and its own sync to other AirPlay speakers is lost. That is fine for a
  Lite-only group.

### 3.7 RAM budget

| | leader | follower |
|---|---|---|
| delay line 300 ms | 53 KB PSRAM | — |
| resend history 1 s | 176 KB PSRAM | — |
| jitter buffer 1 s | — | 176 KB PSRAM |
| task stack | 4 KB (PSRAM with patch 0019, else internal) | 4 KB |
| lwIP buffers | about 3 KB internal while sending (one pbuf per packet, freed after TX) | a bounded RX queue: the socket's receive buffer is capped at 6 packets (9 KB) |
| clock fit, state | < 2 KB | < 2 KB |

Internal RAM is the tight one (review finding 8). A follower in modem sleep (Bluetooth up) gets its unicast in
bursts at each DTIM beacon, about 13 packets per 102 ms, more than the free internal heap could hold at once.
So:
- the socket's receive buffer is capped, and the receive task copies each packet straight into the PSRAM jitter
  buffer;
- Bluetooth is off on the followers. On the leader it fits: on the product image with #232 (decode and stream
  stacks in PSRAM), A2DP plus the real party worker (#241) left 35.8 KB of internal heap at the lowest over a
  16 min run to one follower; a second follower costs about 50 B more (5.1). The first measurement, 6.8 KB on the party-4 lab image before #232,
  is what had ruled it out. (Before that, this read "a leader with Bluetooth up serves at most two followers".)
- the prototype logs the minimum internal heap in every run, and party mode refuses to start under the lab's
  heap floor.

## 4. Measuring the offset

**Without a cable (self-referenced).** Each board reports the TSF at which marker frames (a click every second)
played on its DAC, from 3.2. The difference between boards is the offset. This is weak evidence (review
finding 9): it measures the design against its own clock and its own DAC model, so it shows how well the
controller holds its target, not that the target is right. It cannot see an error shared by both boards, or a
DAC latency that differs between them.

**With one cable (independent).** An RCA cable from Lite A's SUB OUT to Lite B's LINE IN gives a number that
doesn't trust the design. B records left = line in (A's output) and right = its own loopback (B's speaker leg)
on one ADC clock, and cross-correlation gives the offset to a fraction of a frame. Three caveats:
- **The two paths differ.** A's SUB OUT is the ES8388's LOUT2 at line level; B's loopback is LOUT1 through the
  TPA3118, its LC filter and a 100k/10k divider. So first calibrate B alone: loop B's own SUB OUT into its own
  line in and measure that against its speaker tap. That delay is then taken off the A-against-B number.
- **The capture setup is unverified.** Separate LINSEL/RINSEL (ADCCONTROL2 0x0A) is in the header but not yet
  checked on a board, and capture needs ADCPOWER 0x08 (the playback routing leaves it at 0xFC, inputs off)
  without disturbing playback.
- **Levels.** The Lite's sub output is full range but 9 dB below the speakers (SUB_CODE_MAX 24), so the click
  must be loud enough on both.

An independent second check needs no wiring between boards: one microphone placed exactly the same distance from
both speakers, recording a click train.

So: **an honest number needs either the cable or a microphone from the person.** Until then, the prototype
reports the self-referenced number and calls it that.

## 5. Prototype plan

1. `core/hifi_party.c/.h` (pure C, host tests in `test/test_party.c`): packet format and tag, jitter buffer and
   NACKs, lower-envelope fit, the DAC sample rule, the follower's drop/repeat/jump controller, the resend
   history.
2. `esp/lab_party.c`, a lab experiment: `{"exp":"party","role":"leader"|"follower","peer":ip,"nonce":hex,"max_s":...}`.
   The leader plays the test signal: a tone with a 1 ms click every second, on stream frames that are known.
   Its results:
   - packets sent and resent, the loss before and after resend;
   - the clock fits' residuals and re-locks;
   - each marker's DAC TSF, and the slips;
   - the minimum internal heap.
3. A squeezelite patch with the hook and the re-anchor points (3.2 to 3.4), compiled only when `CONFIG_HIFI_LAB`
   is set.
4. Two lab Lites on the same AP:
   - a 10-minute run, giving the self-referenced offset per marker (mean, p95, max) and the drift;
   - a run with the leader's BT A2DP up;
   - with the cable or a microphone, the independent number.

### 5.1 Results (2026-11-17)

Steps 1, 2 and 4 are built; step 3 is the lab experiment's own output path (it plays a test tone or the
leader's current source, then times the DAC as 3.2 says). The host driver is `lab/tools/party_run.py` and the
data is in `lab/data/party-*.json`. Image `a7fcd-party-3` is the first prototype. Lite 3 led and Lite 1 followed,
about 2 m apart. Image `a7fcd-party-4` is the same design moved onto the shared `hifi_party_sync` code (PR 221).
In its runs Lite 1 led and Lite 2 followed. It also logs the supply every 30 s. The tone is 110 Hz at about −30 dBFS.

| image | run | length | markers joined | offset, follower − leader (µs) |
|---|---|---|---|---|
| party-3 | pilot | 60 s | 55 of 55 | cross: mean 11.0, p95 22, max 23, sd 6.8 |
| party-3 | tone10b | 602 s | 565 of 596 | cross: mean 10.8, p50 11, p95 21, max 23, sd 6.7 |
| party-4 | p4pilot | 60 s | 54 of 54 | cross: mean 7.5, p95 21, max 22 |
| party-4 | p4tone10 | 608 s | 604 | follower: mean 8.5, p50 8.5, p95 20.3, max 22.7; cross on the last 8 markers 3 to 6 |

- **All of these numbers are self-referenced** (section 4). Each one compares two boards' own DAC fits on the
  AP's clock. That shows the controller holds its target to about ±20 µs, which is 50× inside the 1 ms aim.
  It does not show that the target is right. An independent number needs the RCA cable or a microphone,
  and that is still open.
- **Drift (review finding 12).** Over the 10 minutes, the follower dropped 148 frames and repeated none. That
  makes its DAC 5.7 ppm fast against the leader's, or about one dropped frame every 4 s. The offset showed no
  trend over the run: each 100 s block had a mean between 10.4 and 11.1 µs.
- **Start-up.** The follower mutes its first 94 blocks (about 3 s) while the fits lock. It also skips about 300
  frames it got before it knew the stream position. After that there are no mutes.
- **Wi-Fi.** The leader sent 77727 packets, with 36 resends and 11 send failures. During one drop of a few
  seconds on the follower (around 410 s), it lost 0.62 s of audio and logged 42 late packets and 30 gaps. It
  relocked on its own, and the next marker was back in range. That is why 31 of the 596 seconds don't join.
  There was one other ~20 s stall on the follower's API, at about 200 s, and no audio was lost then.
- **p4tone10.** The harness stopped the host driver at about 600 s, before it could join and save the full
  cross-board series. Its row therefore shows the follower's own offset against the leader's announced target,
  which it reports itself, and the cross-board offset for the last 8 markers that both boards still held. In the
  pilot the two measures agreed to within about 1 µs. The follower dropped 32 frames and repeated 2. It never
  reset or relocked. One Wi-Fi drop on the follower, at about 150 to 210 s, cost 8632 frames (about 0.2 s of
  audio) and 15 gaps. The offset stayed in range across it.
- **Supply (party-4).** Both boards held 20.2 to 20.4 V on VBUS and PVDD the whole run. Power-good was set
  throughout, on 100 W (Lite 1) and 60 W (Lite 2) contracts, with no resets.
- **Heap.** The minimum free internal heap was 12.8 KB on the follower and 13.4 KB on the leader. That is tight,
  and it agrees with 3.7. On party-4 it was 13.0 KB on the follower and 12.6 KB on the leader.
- **Power.** In a first 10-minute try, both boards reset together at about 185 s. The reset cause was power-on,
  and on this board that comes from the supply supervisor, not a crash. The two boards may share a charger. That
  is a bench supply problem, not a party mode one, and the run was repeated (tone10b).
- **Bluetooth on the leader.** No phone was needed. The lab image's `a2dp` experiment lets Lite 3 stream a tone
  over A2DP into Lite 1, which then leads with `--source any`. The A2DP link itself was fine: it connected in
  2.2 s, ran at 1.4 Mbit/s, and over 176 s had no callback gap over 50 ms and no disconnects. RAM is the
  problem. With A2DP streaming, Lite 1 had 6.8 KB of internal heap free (5.6 KB at its lowest), against 12.4 KB
  idle. So on party-4 the leader was refused at start ("too little free RAM", floor 10 KB). A lab-only image
  (`a7fcd-party-5`, floor lowered to 5 KB, not kept) let it start at 7.0 KB. The leader played 28.7 s, then the
  lab's run guard stopped it when the heap fell under its 7 KB stop line (lowest 6.4 KB). In those 28.7 s, the
  leader sent 3697 packets with 14 send failures, 30 resends and one relock. The follower joined 18 markers
  within ±22 µs (p50 10.6). It also lost 1.7 s of audio in 15 gaps. The sync held, but that lab image was
  about 4 KB short. Data: `lab/data/party-p5bt-2026-11-17T184500.*`.
- **Bluetooth on the leader, product image (a4b0e, esp32-lab rung).** The 4 KB short came from two things: the
  lab image (its tables and experiments) and the decode and stream stacks, which #232 moved to PSRAM. Lite 2's
  internal heap with A2DP from Lite 3 streaming into it, `api diag`:

  | image | idle free (lowest) | A2DP free (lowest) |
  |---|---|---|
  | lite-bringup before #232 (ea683a9) | 19863 (18691) | 13655 (12291) |
  | lite-bringup with #232 (c255b60) | 39127 (35803) | 32519 (31595) |

  A2DP costs about 6.6 KB and #232 gives back about 19 KB. The party worker wasn't on a board yet, so a stand-in
  of its shape (hifi PR #237: 6144 B PSRAM stack, static TCB, core 1, priority MIN+5, 8 × 1452 B to the
  follower every 64 ms plus a clock packet) ran beside A2DP: 13.3 min of A2DP, 11.5 min of it with the
  stand-in sending. Free 31.7 to 32.3 KB, 30975 B at the lowest, 0 underruns, no callback gap over 50 ms, no
  disconnects. The stand-in costs about 0.2 to 0.8 KB of free heap and 0.6 KB of the lowest. It sent 133
  packets/s, not 125 (a tick rounding, since fixed), so a little more than the worker. **Limits:** it sent to one
  follower; the product sends to up to three, and the extra followers' sends are not measured yet.
  **Unconfirmed: an A2DP source failed to connect while the leader sent** ("connect failed, reason 0", 3 of 3,
  twice; with the sender quiet it connected first try in about 1 s). But Lite 3 was both the A2DP source and the
  stand-in's target, answering each packet with ICMP port-unreachable, so this may be Lite 3's radio, not a
  leader limit. A separate source and follower would settle it; until then, connect the source before party
  starts. To repeat with the real worker and three followers. Data: `lab/data/bt-heap-pre232-*.json`,
  `bt-heap-post232-*.json`, `bt-party-standin-2026-11-17T235730.json`.
- **Bluetooth on the leader, real worker (a4b0e, 2026-11-18).** Repeated with #241's worker (main build
  `a7fcd-party3` on Lites 2 and 5; Lite 3 on the #241 lab image as the A2DP source only), `api diag` on the leader:
  - **Run A, clean, 16 min:** Lite 2 leads and plays Lite 3's A2DP stream to Lite 5. Free 36.9 KB at the lowest,
    35,787 B lowest ever (`min`), largest block 34.8 KB; the worker's own heap 42.9 KB at party on, 35.8 KB least.
    Floor 10 KB: **pass with 25 KB to spare.** A2DP connects 6 of 6: two with party off (control), then a first
    connect and reconnects at 301, 480 and 664 s while the leader sent (2.4 to 4.1 s each, a little slower than
    the 1.7 to 2.3 s with party off). So the earlier "reason 0" failure was the stand-in's tangle, not a leader
    limit. 0 source disconnects, about 1410 kbit/s, no callback gap over 29 ms, 0 underruns on the leader.
  - **The cost of a second follower:** run B was meant to make Lite 3 both the source and a second follower, but a
    follower switches its own Bluetooth off (8.6), so the source never connected (0 of 11). It did measure two
    followers without A2DP for 10 min, and run C one follower for 4 min, both with source `any` (silence):
    | run | followers | A2DP | leader packets/s | worker heap least | failed sends | follower missed frames |
    |---|---|---|---|---|---|---|
    | C | 1 | no | 131 | 42,355 | 0 | 0 |
    | B | 2 | no | 260 | 42,307 | 0 | 0 |
    | A | 1 | yes | 142 | 35,839 | 1016 | 1.8 M |
    A second follower costs about 50 B (packets are freed after each send), so A2DP plus three followers should
    still keep about 35 KB. That is an estimate from B and C, not measured with A2DP up.
  - **Open: A2DP hurts the send.** In run A the leader's failed sends rose from 0 to 1016, most after about 560 s,
    and Lite 5 concealed 1.8 M frames in 16 min (about 4%: some 800 a second early, 5000 a second by the end),
    with 47,668 packets skipped and 9375 mutes. In the 30 min tone run (two followers, no Bluetooth) the worse
    follower missed 218 k frames in 30 min, and in B and C, none. So it is Bluetooth beside the leader's Wi-Fi
    send, or the A2DP stream's rate, not RAM; a7fcd's burst logging (#242) can tell which. The A2DP stream carries
    no sync markers (they come with the tone), so the offset between boards wasn't measured in these runs.
  Tool: `lab/tools/bt_party_run.py`. Data: `lab/data/bt-party-real-A-2026-11-17T234151.json`, `bt-party-real-B-tangled-2026-11-17T235927.json`,
  `party-a4b0e-C-1f-any-2026-11-18T001225.json`.

## 6. Sources

- squeezelite-esp32 `output_i2s.c` (ee1720c) and `output.c`: output thread, DMA sizing, `START_AT`, skip and pause frames.
- ESP-IDF 4.4.8 `components/driver/i2s.c`: the free queue, `i2s_start`, `i2s_set_clk`, the end-of-buffer interrupt.
- snapclient for ESP32, https://github.com/CarlosDerSeher/snapclient: sample insert and drop per chunk; IDF 5.x.
- LMS sync tuning by hand, https://bugs-archive.lyrion.org/bug-12909.html and https://github.com/sle118/squeezelite-esp32/issues/327.
- ESP-ADF multiroom, https://github.com/espressif/esp-adf/issues/1310: reported ±1 to 17 ms.
- TSF, https://en.wikipedia.org/wiki/Timing_synchronization_function, and the 802.11n timestamp study for AVB,
  https://grouper.ieee.org/groups/802/1/files/public/docs2008/avb-rhee-802-11n-timestamp-0908.pdf.
- Multicast over Wi-Fi, RFC 9119, https://datatracker.ietf.org/doc/html/rfc9119.
- ESP-IDF 4.4.8 Wi-Fi guide (TSF, ESP-NOW rate, power save), https://docs.espressif.com/projects/esp-idf/en/v4.4.8/esp32/api-guides/wifi.html.
- APLL errata, https://docs.espressif.com/projects/esp-chip-errata/en/latest/esp32/03-errata-description/esp32/clock-limited-audio-pll-frequency-range.html.
- ESP32 forum on TSF reads and multicast loss: https://esp32.com/viewtopic.php?p=94957, p=77828, p=31891, p=27112.
- An RTP pager on ESP32, https://tomeko.net/projects/esp32_rtp_pager/.
- Our own: `lab/espnow.md` (drift, BT and power save), HIL reports in `reports/hil/`.

## 7. Review

An Opus agent that did not write the design reviewed it against the squeezelite and IDF sources (2026-11-17).
It found 3 blockers, 5 major and 4 minor problems. This version fixes them as follows.

| # | finding | what changed |
|---|---|---|
| 1 | the DMA ring is 12 × 256 frames (32-bit slots), not 12 × 512; the "− 11" index formula was not derived | 1 and 3.2: the buffer length comes from `dma_buf_len` at run time, and the fixed ring latency cancels between boards |
| 2 | the write count loses the DAC on START_AT discard, OFF, a rate change and a dry DMA | 3.2: W counts only frames handed to `i2s_write`; each of these resets the fit |
| 3 | the "> 10 ms blocking" filter is unsound | see below: the rule is now "the last boundary the write crossed" |
| 4 | a tap before the gain loses replay gain, fades, crossfades and invert | 3.3: the tap is after the gain; followers run at unity and follow the leader's volume |
| 5 | the delay line saw discarded frames | 3.3: the tap and the delay sit at one point, after the discard |
| 6 | the follower should feed `outputbuf`; a new kind must be added to `hifi_owns_output` | 3.4: `DECODE_HIFI_PARTY` added; the `outputbuf` route was declined, and 3.4 says why |
| 7 | TSF steps in modem sleep, on an AP reboot, when roaming; the 48 µs read | 3.1: bracketed reads, a step forces a re-lock, one BSSID per group |
| 8 | internal RAM and DTIM bursts; the 2 s history is 352 KB | 3.7: 1 s history, a capped receive buffer, at most two followers with Bluetooth, heap logged |
| 9 | the self-referenced number is circular; the cable method compares different analog paths; the capture registers are unverified | 4: called weak; calibrate B first; ADCPOWER 0x08; the microphone alternative |
| 10 | constant-time tag, replay across reboots, NACK amplification, the subnet check, the join command, the AP interface | 3.5 |
| 11 | "sub delay 0" is moot; the sub is −9 dB | 4 |
| 12 | the drift range came from 20 s runs | 1: to be measured again over ≥ 10 min |

**Where this version disagrees: finding 3.** The review said one long write spans 8 buffer acquisitions, so a
long write doesn't show the last one blocked. True, but the fix doesn't need the filter. The free time of the
buffer that each boundary goes into is fixed by the frame index alone (it frees a ring after the frames it held
before). A write that crosses boundary b can't take b's buffer before it frees, so the write returns at or
after that time, whether it waited at b or not. Pre-emption and the copy after b only make the sample later. So
every sample is late or exact, never early, and the lower envelope fits the line. The ≥ 1-boundary rule stays.
A minimum-duration filter is optional: it only drops late samples. The host tests check this with writes that
straddle boundaries and with whole busy seconds.

**Found after the review: the group key.** The first version derived the group key from "the Hive key", but
the board holds only the Hive's public signing key, which anyone can read. 3.5 now has the group key drawn
fresh per group and handed to each member inside a signed command over its TLS dial-in.

Two simpler routes the review offered are recorded in 3.4: the I2S event queue (v2), and feeding `outputbuf`
(declined).

## 8. Party mode you can switch on (v1 product)

The prototype (5) was a lab experiment: one follower, told its leader and its key by a signed `api lab` command.
This section is the version a user turns on from the board's page, where followers find the leader by
themselves. Everything in 3 still holds unless this section says otherwise. An Opus review of the first draft
found 12 problems; 8.10 lists them and where each is answered.

### 8.1 Keys: a household party secret

- The Hive provisions each board once with a 32 B **party secret**, the same for every board in the house:
  `api party {"secret":"<64 hex>"}`. Like the lab, that POST is refused from the LAN (403) and taken only over
  USB or the Hive's signed dial-in (TLS). It is kept in NVS, never shown or logged (GET says only `keyed: true`
  and a 1-byte key id), and cleared by a factory reset.
- Per-group key = HMAC-SHA256(secret, "hifi-party-v1" ‖ group)[0:16], where `group` is a fresh random 32-bit id
  the leader draws each time it starts leading. So each group still has its own key (3.5), and the key itself
  never crosses the LAN.
- Announce key = HMAC-SHA256(secret, "hifi-party-announce-v1")[0:16].
- A board with no secret can't switch party on: the page shows "needs the Hive" and the API says 409.

Decided: one household secret handed out by the Hive, not a key per group pushed to each member. Followers
have to find and join a group with nobody sending them a command, so they must be able to check a group's
traffic before they have been told anything about it. The review asked whether a shared secret is too much to
lose with one board; a flash dump of any board already gives the Wi-Fi password, which opens the same LAN, and
party traffic never leaves it. A new secret (a new key id) is how the Hive retires one.

### 8.2 Finding the leader

- A leader sends an **ANNOUNCE** (type 4) once a second to the subnet's broadcast address, port 4951. It holds
  the group, the term and whether the lead was picked (8.4), the board's name, its IP, its followers' IPs, and
  the leader's TSF when sent, all under the announce key's tag.
- A board takes an announce only if:
  - the tag checks;
  - the source IP is the IP inside it, and is on its own subnet (its own address and netmask, so a /23 or a /16
    works too);
  - its TSF is within 2 s of the board's own. Boards on one AP share the TSF (3.1), so this keeps a group to one
    AP, as 3.1 needs. An announce whose tag checks but whose TSF is off counts as "a leader on another access
    point" (`other_ap`) only when a second one from the same address comes within 3 s with its TSF moved on by
    the time between them (±0.5 s). It is then counted and shown, and the board doesn't lead because of it.
  - So one replayed announce, sent again any number of times, no longer stops a board leading. A replayed
    *sequence* of announces still can: announces go out once a second, and two or more in a row played back
    at their original pace from the leader's address move their TSF on as a live leader's does, so they set
    `other_ap`. Looped, they keep it set, with a gap of about 3 s at each wrap. The TSF can't tell that from a
    real leader on another AP; it costs a recording, and only stops boards leading (they show why).
- The TSF window is not what proves a leader is alive (an AP that reboots restarts its TSF, so an old capture can
  pass it). A follower counts its leader alive only while it gets the leader's **CLOCK** messages, which go to
  it alone under the group key (8.5). A board that joins a group and gets no CLOCK within 3 s marks that group
  dead and ignores its announces until one comes with a higher term.
- The port is open only while party is on. Nothing is sent outside the subnet; there is no mDNS, UPnP or router
  setting. The announce does put the board's name on the LAN once a second while party is on; AirPlay and
  Spotify already advertise the same name there, so it tells nobody anything new.
- **Floods.** Every datagram costs an HMAC, so the receive task drops a packet before hashing it unless it comes
  from the leader or a follower, or is an announce or JOIN within a budget of 20 per second from everyone else.

### 8.3 Joining and leaving

- A follower sends **JOIN** (type 5, under the group key) to its leader every second. That is both the join and
  the keepalive. A JOIN carries the follower's IP and TSF. The leader takes it only if the source is that IP, on
  its subnet, and the TSF is past the last JOIN it took from that IP, so a captured JOIN or leave can't be
  played back.
- The leader keeps up to **3 followers** (the airtime limit in 2) that it has heard from in the last 3 s, and
  sends each one the audio, the CLOCK messages and the NACK answers by unicast. Its announce lists them. A
  fourth board is not in the list, gets no audio, keeps its JOINs going in case a place frees up, and its page
  says the group is full.
- NACKs are taken only from a follower, with a sequence number that must rise, and within the 1 s per peer per
  second resend budget of 3.5.
- **Leave:** switching party off on a follower sends one JOIN with the leave flag. A follower that just goes
  quiet (power, Wi-Fi) drops off after 3 s.

### 8.4 Who leads

- The modes are **off** and **on**, plus a **Lead** button.
- **The lead goes where the music is.** A member whose own source starts (LMS, Spotify, AirPlay, the line in)
  takes the lead with a higher term, and the rest of the group follows it. So casting to any board in the party
  plays on all of them. The old leader becomes a follower, and its own player is stopped.
- **The line in doesn't lead from a follower.** A board that is following stays a follower while its line in
  plays (input=line, say after a test signal ends and the line in takes the idle output first): it takes its
  output back from the line in within a second, and the line in counts as its own source only on a board that
  isn't following.
- **Lead** (page or `api party {"lead":true}`) makes the board lead now. Lead is taken at most once per 5 s on a
  board, so nothing on the LAN can make the group flap faster than that.
- An announce is ranked by (picked, term, IP): a lead from Lead or from a source starting is *picked*; the
  first-on lead is not. A leader that hears a higher-ranked announce follows that board; a follower moves to it.
  A board that sees someone else's picked lead forgets its own pick.
- The term only goes up. It is kept in NVS with the pick, written only when they change (a few times a day at
  most), so a reboot doesn't reset it. A new lead takes the highest term the board has held or heard, plus 1.
- When party is switched on, or a board with party on boots, it listens for 2 s plus up to 0.5 s of jitter, and
  follows the best leader it heard. If it heard none, it leads (not picked). So the first board on leads, a
  board that boots joins the group that is already playing, and a board that was picked and lost power takes
  its lead back after the power cut only if it hears no leader that is itself picked.

Decided: a term number that only goes up, rather than a vote. Two boards switched on in the same half second may
both lead for a moment; the higher IP wins on the next announce.

### 8.5 When the leader goes

- The leader sends CLOCK every 200 ms whenever it leads, playing or not: with the "no audio" flag when its output
  is stopped or it plays at another rate than 44.1 kHz (the page says why). So the followers stay locked through
  a pause, a stop and a track change.
- A follower that has had no CLOCK for 3 s fades to silence (3.4) and goes back to listening. It follows the
  next leader it hears, whether the old one coming back or another board pressed to Lead.
- A follower **never promotes itself** when its leader goes quiet: the music comes from the leader's source, so
  a follower taking the lead would only lead silence. It does take the lead when its own source starts (8.4).
- **Silence is cheap.** While the leader's player is paused or idle, its AUDIO packets carry the silence flag and
  no samples (46 B instead of 1454 B), so an idle party costs about 1% airtime per follower, not 12%.
- The 300 ms delay line only runs while the leader has a follower. It is faded in when the first follower
  joins and out when the last one leaves, so a board alone plays with no extra delay, and AirPlay's own
  lip-sync figure holds when nobody follows.

### 8.6 Sources and Bluetooth on members

- A board's output belongs to the party while it follows (`DECODE_HIFI_PARTY` in `hifi_owns_output`, 3.4).
  Starting a source on it moves the lead there (8.4), so there is no "paused locally" state in v1.
- **Bluetooth is off on every follower while party is on**, and the page says so; switching party off turns it
  back on. Modem sleep breaks the TSF lock. The chosen leader keeps its Bluetooth sink (a7fcd): on the product
  image with #232 it keeps about 31 KB of internal heap with A2DP and a stand-in sender to one follower (5.1).
  A phone can connect while party plays (6 of 6 in 5.1), but with A2DP up the followers conceal far more than
  with Wi-Fi sources (about 4% of frames, 5.1). The cause and the fix are in 8.11.
- 44.1 kHz only in v1 (as the prototype). A leader at another rate sends CLOCK with "no audio" and its page says
  why.

### 8.7 RAM and what happens when it runs short

- The prototype allocates about 630 KB of PSRAM when one board holds both roles' buffers (256 KB delay ring, 183
  KB jitter buffer, 187 KB history), not the 230 or 180 KB of 3.7. The product allocates by role: the leader's
  ring and history when it leads, the follower's jitter buffer when it follows, and frees them when the role
  changes. About 1.2 MB of PSRAM is free with a stream up, so either fits.
- 3.7's capped receive buffer isn't there: `CONFIG_LWIP_SO_RCVBUF` is off and the UDP mailbox is 32 deep. v1
  turns `SO_RCVBUF` on and caps the party socket at 8 packets.
- Every measured run used the test tone. The product run measures the internal heap while leading from LMS
  (and Spotify where the bench has it) with a follower.
- **Degrade path:** a leader whose internal heap falls under the lab's 10 KB floor stops leading, sends one
  announce with no followers and frees its buffers; the followers mute after 3 s. The page says "party stopped:
  low memory". A board under the floor doesn't take the lead.

### 8.8 The API, the page and the self-test

- `GET /api/v1/party` → `{on, role: "off"|"listening"|"leader"|"follower", leader, leader_name, group, term,
  picked, followers:[ip..], full, keyed, other_ap, why, stats:{...}}`.
- `POST /api/v1/party` takes `{"on":true|false}` and `{"lead":true}` from the LAN (they're user controls, like
  the volume; 429 when Lead comes too soon), and `{"secret":...}` only from USB or the Hive.
- The CLI has `party`, `party on|off|lead`. NVS keeps `on`, the secret, the term and the pick.
- The page gets a Party card: an on/off switch, a Lead button, the role, and who leads or follows.
- The self-test gains a `party` line. It passes when party is off. When it's on, it checks the role, that a
  follower's last CLOCK is under 3 s old, and that the internal heap is over the floor, and it names the role.
- The Hive key, the dial-in, the self-test and the LAN-only rule are unchanged.

### 8.9 Tests

- Host: a simulated LAN of several boards exchanging real encoded packets. It covers:
  - join, leave, and a fourth follower turned away;
  - the first board on leading;
  - Lead moving the group, two Leads at once, and the Lead rate limit;
  - leader loss muting the followers and their re-join;
  - a replayed announce, JOIN or leave dropped, and a board without the secret ignored;
  - a flood;
  - a power cut with the term and the pick kept;
  - a local source moving the lead;
  - the leader's output stopping;
  - 48 kHz.
- Hardware:
  - a 30 min run on two Lites from a main build, within 100 µs on the marker measure (4). This measure is
    self-referenced, so it can't see an offset common to both boards' DAC paths; a cable or microphone check
    stays on the list (4);
  - a fleet `--hil` run with party on, only on boards leased for it. Party mode joins every keyed board on the
    LAN, so the other fleet boards stay off party.

### 8.10 Review of this section

| # | finding | answer |
|---|---|---|
| 1 | a JOIN, leave or NACK could be replayed | 8.3: the JOIN carries the follower's IP and TSF, which must rise; NACKs only from a follower, seq rising, budgeted |
| 2 | one household secret is a big thing to lose | 8.1: kept, with the reasons; never logged or shown, refused from the LAN, a key id to retire it |
| 3 | the lead ignored where the music plays | 8.4: a member whose source starts takes the lead |
| 4 | 8.6 allowed Bluetooth on a follower | 8.6: Bluetooth is off on members while party is on |
| 5 | the RAM budget was unmeasured and 3.7's receive cap didn't exist | 8.7: buffers by role, `SO_RCVBUF` on, heap measured from a real source, a degrade path |
| 6 | terms didn't survive a reboot; the pick was unused; Lead could make the group flap | 8.4: term and pick in NVS, rank (picked, term, IP), Lead once per 5 s |
| 7 | the TSF window isn't freshness; broadcast loss dropped good followers; a second AP led alone | 8.2, 8.5: liveness from CLOCK alone, a dead group is skipped, "another access point" shown instead of leading |
| 8 | every datagram costs an HMAC | 8.2: members first, a budget of 20 per second for the rest |
| 9 | idle and output-stop behaviour was inconsistent | 8.5: CLOCK always, silence packets without samples, the delay only with a follower |
| 10 | 8.6 contradicted 3.4's output ownership | 8.6: the output stays the party's; a local source moves the lead instead |
| 11 | /24-only subnet check; the name went on the LAN unsaid | 8.2: own netmask; the name, and why that's fine |
| 12 | the self-test always passed; checks missing; HIL would group the whole fleet | 8.8, 8.9 |

### 8.11 The leader under A2DP: why packets come late, and the fix

**What was measured** (a4b0e, 2026-11-18, `lab/data/bt-conceal-L2lead-L3fol-L5src-2026-11-18T004609.json`: Lite 2
leads, Lite 3 follows, Lite 5 streams A2DP to Lite 2):

| phase | concealed | leader ENOMEM |
|---|---|---|
| a2dp | 15.2% | 788 in 72 bursts |
| a2dp + tone | 19.2% | 1147 in 85 bursts |
| tone, no BT | 13.0% | 1777 in 23 bursts, one 45 s episode |
| a2dp again | 0.02% | 0 |

Every failed send on the leader is `ENOMEM`, and its internal heap stays at 36–42 KB free, so the failures are
not a heap shortage. In the a2dp phase the follower's jitter buffer took 20,396 packets, and 3,997 of those came
**after their play time** ("old"). Its concealed frames are worth about 3,485 packets. So most of the
concealment is packets that arrive late, not packets that are lost. The leader sent 108,729 packets with
3,744 failures, resent 18,021, and never had a NACKed packet missing from its history.

**Why they come late.** `ENOMEM` from `sendto` on a UDP socket means the Wi-Fi driver had no static TX buffer
and its cache queue was full. The Lite has 4 static TX buffers and 32 cached ones, so about 36 packets can wait
in the driver. One follower's stream is 125 packets a second, so a full queue is about 290 ms of stream. The
follower's head start is 300 ms (`DELAY`). So whenever Bluetooth, or anything else on the channel, slows the
radio enough to fill the queue, every packet behind it arrives almost a whole head start late. `ENOMEM` is only
the overflow on top. Coexistence on the ESP32 shares the air by time: each period starts at the AP's beacon and
gives Wi-Fi one slice and Bluetooth the next (IDF coexistence guide). The radio also falls back to slower rates
when frames collide with Bluetooth. One 1,450-byte frame plus its ACK per packet uses those Wi-Fi slices badly.
Two things in the leader made it worse:
- Resends had no deadline. A follower NACKs a gap and asks again, and the leader resent anything still in its
  1 s history, including packets the follower had already played past. Those resends (18,021 in the run) went
  into the same full queue, ahead of packets that were still due.
- A failed send was simply dropped. The stream kept stuffing new packets in, so the queue stayed full, and each
  loss turned into a NACK and a resend later.

**Options weighed.**
- **Wi-Fi/BT coexistence preference while leading: rejected, it does nothing on this IDF.**
  `esp_coex_preference_set` calls `coex_preference_set` in IDF 4.4.8's `libcoexist.a`. Disassembled, that is
  `entry; movi a2, 0; retw`: it returns `ESP_OK` and changes nothing. The other knob, the coexistence status
  bits, is set by the Bluetooth stack itself (`ESP_COEX_BT_ST_A2DP_STREAMING`). Clearing them would starve the
  sink, which is the leader's own source.
- **More Wi-Fi TX buffers alone: rejected.** A deeper queue holds more late packets. The packets are already
  late, not lost.
- **Pacing the per-follower unicast: folded into the retry.** In steady state the worker sends 1–2 packets a
  pass anyway. Bursts only come after a stall, and the retry below stops those.
- **A short retry on ENOMEM, inside the packet's deadline: taken.** See below.
- **AMPDU TX: taken.** This is a config choice, not an IDF upgrade, and it is IDF's default. The Lite had it off
  only because one AirPlay stream didn't need it.
- **Moving the Bluetooth controller or Bluedroid to core 1**, as the coexistence guide suggests for heavy
  traffic: not now. It moves tasks the audio path shares a core with, and the fix above should be enough. It is
  kept as the next step if the run says otherwise.
- **A fixed PHY rate** through the driver's internal calls: no. That API isn't public.

**The fix** (PR to `h2/lite-bringup`, `hifi_party_esp.c` and `board/lite-main/sdkconfig.defaults`):
1. **Every send has a deadline.** A follower plays frame k as the leader writes frame k + `DELAY`. So a packet
   is too late once its first frame is less than 512 frames, 11.6 ms (`LATE_MARGIN`), ahead of the play
   position `s_pos − DELAY`. That margin is one 256-frame DMA block, which is how far `s_pos` moves at a time,
   plus the time on air. The leader
   - resends a NACKed packet only while it is still due, and counts the rest (`rsl`);
   - skips ahead to the first due packet when the stream has fallen behind (`skip`; this replaces the old
     check that only fired a whole ring behind).
   - NACKed packets wait in a short list of 24 (`rq`), and each pass sends them first, earliest play time first.
     They are older than the stream, so they play sooner. A NACK that arrives while the queue is full waits
     there instead of being dropped; the follower asks only twice, 20 ms and 60 ms after a gap. `rsb` counts
     resends dropped because the list was full. A NACK's k outside what was sent counts as `gone`.
2. **Hold and retry on a full queue.** When `sendto` fails with `ENOMEM` or `EAGAIN`, the leader ends the pass.
   On the next pass, about 10 ms later, it sends the same packet to the followers that didn't get it, before
   anything newer. It gives up on the packet only at its deadline. The packet's bytes are already in the resend
   history, so the hold needs no extra buffer. `hold` in the stats is [sent on a retry, given up]. Another
   error still moves on, and the follower asks again. A packet the queue turned away counts once in `txf`,
   `txe` and the burst log, however many retries it takes, so `ENOMEM` figures before and after compare.
3. **AMPDU TX on, block-ack window 6, static TX buffers 4 → 6.** Aggregation sends up to six packets in one
   exchange with one block ACK. That uses the short Wi-Fi slices much better, and it is the one change that adds
   airtime instead of managing the shortage. Six buffers let a whole window be in flight, and six is what the
   board ran before the heap trim. The cost is about 3.2 KB of internal DMA RAM plus the block-ack state. Before
   the change, A2DP plus party left 36–42 KB free, and the boot floor for every image is 16,000 B. The run below
   measures the heap after the change. `lite-lab` images inherit lite-main's defaults. `lite-lab-ble` and
   `lite-lab-radio` set their own 4 TX buffers, so they don't change. The lab image booted at 18.1 KB before
   #235 trimmed 8 KB of Wi-Fi buffers, so it should be about 23 KB with this change, still clear of 16,000 B.
4. **The cache queue 32 → 16 (`CACHE_TX_BUFFER_NUM`, IDF's minimum).** The fix checks a deadline when a packet
   goes into the driver, not when it goes on air. With 38 packets ahead of it, a packet that got in on time could
   still wait almost a whole head start (Opus review). With 6 + 16, at most 22 packets are ahead of it. How long
   that takes depends on how fast the radio drains them while Bluetooth shares the air, not on how much audio
   they carry. If long `ENOMEM` stretches still give `old` frames, the next step is to add the measured wait
   (first `ENOMEM` to accepted, as a running max) to the margin while `ENOMEM` is recent. The cache holds pbufs, which are in PSRAM here (`SPIRAM_TRY_ALLOCATE_WIFI_LWIP`), so it
   costs no internal RAM. The full queue now turns packets away sooner, and the hold above deals with that.
   The run is judged on the follower's late (`old`) and concealed frames, not on the leader's `ENOMEM` count
   alone.

**Result (a7fcd, 2026-11-18): the target is not met.** The fix works while the radio still drains about 125
packets a second, but this channel often doesn't, and then nothing on the leader can keep up. Runs with
`lab/tools/bt_conceal_run.py`: Lite 2 leads, Lite 3 follows, Lite 5 streams A2DP to Lite 2. Concealed is the
follower's missed frames (`jb.miss`, summed across rejoins) over the window's frames. A window where the follower is
listening plays nothing, so it counts as 0% here and has to be read with the roles.

| run (image) | data | concealed | leader ENOMEM | drain (accepted tx/s) | follower |
|---|---|---|---|---|---|
| before (`a7fcd-party6`, #242 head) | `bt-conceal-before-…T041559.json` | 43–69% in every 30 s window | 14,330 in 647 bursts, about 75 bursts/min | 125–144 until 480 s | dropped out at about 530 s for good |
| after (`a7fcd-coex2`, this PR) | `bt-conceal-after-…T043648.json` | 0.6% at 0–30 s, 14% at 30–60 s, then 13–82% | 4,043 in 61 bursts over about 320 s, about 11.5/min | 135 at first, then 22–124 | dropped out and rejoined many times; out for good from about 320 s |
| AMPDU off (`a7fcd-coex3`) | `bt-conceal-coex3-…T052137.json` | 9% in the first 30 s | 492 in 4 bursts | 45 from the start | out for good within 30 s |

What the runs show:
- **The deadline and hold work when there is airtime.** At 0–30 s the before and after images drained the same
  135–140 packets a second, and the after image concealed 0.6% against 43%. The holds succeeded, and `old` fell
  from about 1,000 per window to 24.
- **Airtime is the limit.** The stream needs 125 packets a second to one follower. When the leader's accepted
  rate falls below that (45–115/s in the after run from about 70 s, 45/s for the whole coex3 run), the follower
  conceals the shortfall whatever the leader does with it. The deadline turns those losses into skips (9,523)
  and given-up holds (2,358) instead of late packets, so the queue holds less stale audio. It can't create
  airtime.
- **The channel changed between runs.** a4b0e's run on the old code concealed 15%. The before run, on the same
  code 3 h later, concealed 54% at full drain. coex3 started at a third of the drain. A change of the AP's channel
  load (other stations, the 08:00 UTC nightly's neighbours) is a bigger effect here than any of the options.
  So a single before/after pair can't judge a fix: the next runs have to interleave images, and log the AP
  channel's busy time beside them.
- **AMPDU isn't the cause.** With it off (coex3), the drain was worse from the first second, not better. The
  after run's slide from about 70 s therefore follows the channel, not aggregation. AMPDU TX stays on.
- **Heap.** With A2DP and party up, the leader on `a7fcd-coex2` had 40,127 B free at boot and a minimum of
  32,207 B. The old image had 43,839 B and 35,871 B. So the two extra TX buffers and block-ack state cost 3.7 KB,
  and the leader stays far above the 16,000 B floor.
- **A separate follower bug strands it.** In `core/hifi_party_group.c`, a follower that joins and gets no CLOCK
  within `LOSS_MS` marks the group and term dead (`dead_group`, `dead_term`), so it can skip a played-back
  announce. It then ignores that leader's announces for as long as the leader keeps the same term, which is
  for good. It goes to listening as an orphan, and an orphan never leads by itself. Under congestion a CLOCK can
  simply be late, so one bad second leaves the follower silent for the rest of the session: in all three runs
  it ended in listening while the leader kept leading. The fix belongs in the group logic. The dead mark should
  expire after a few `LOSS_MS`, or clear when the same leader's CLOCK or a fresh announce sequence is heard.

PR #245 was merged into `h2/lite-bringup` on 2026-11-18 (a3f17's call: it is never worse than the old code on
the same channel, and it is much better when the air can carry the stream).

**Now:** the dead mark expires (a7fcd, PR to `h2/lite-bringup`, with a host test and a board check that a follower
that missed its CLOCK rejoins by itself). A follower stuck in listening is something a person hears, so it goes
first.

**Next round's options** (on hold: a3f17, 2026-11-18. The last two need a design review before anyone builds
them). Costs are guesses:
- **Interleaved A/B runs with the channel load logged.** Old, new, old, new, 3 min each, on the same three Lites,
  with the AP channel's busy time beside each window. That needs either the AP's own stats or a fourth ESP32 in
  promiscuous mode counting airtime. Cost: about half a day for the tool change and an hour of boards a run. No
  firmware risk. This is what any other change here would be judged with.
- **Bluetooth controller and Bluedroid on core 1.** The coexistence guide suggests it for heavy Wi-Fi and BT
  traffic. Core 1 already runs the audio path (decoder, I2S, the party worker), so the risk is underruns and
  priority inversions there, not in the radio. Cost: a config change plus a day of measuring audio timing
  (DMA underruns, decoder load, party clock jitter) with A2DP, AirPlay and party each. Gain: unknown. It helps
  only if the Wi-Fi task is starved for CPU, not for air, and the runs so far point at air.
- **Two packets' audio (2 × 352 frames) per party packet.** This halves the frames on air, the ACKs and the per-packet coex
  arbitration for the same audio (62.5 packets/s instead of 125, about 2.9 KB each). The packet no longer fits
  one 1,500-byte frame, so it is either IP-fragmented (one lost fragment loses both blocks) or needs a
  two-frame send with its own sequence. The jitter buffer, NACK, resend history and deadline all count in
  packets, so they change too. The packet's latency rises by one block (5.8 ms). Cost: about 2–3 days with host
  tests and a format version bump, since mixed-version groups have to refuse each other cleanly. Gain: likely
  the biggest of the three, because the drain limit is in packets per second.
- **A compressed party stream** (for example 16-bit to a lossless or near-lossless codec). Less air per
  second. Costs CPU on every Lite, a codec in the image (flash is 93–95% full), and a week or more. Last
  resort.
- Keep the target (under 0.5% concealed, no ENOMEM bursts, 15 min) for an air where the leader can drain
  125/s. On a channel that can't, the right result is a clean hand-down to fewer followers or a lower rate, not
  concealment.

### 8.12 Round 2: what limits the drain under A2DP, and pair sending (a7fcd, 2026-11-18)

The rung: a party plays clean (under 0.5% concealed) while a phone streams to the leader by A2DP. The base is
`hifi-lite-126f47a` (#245, #258 and the dead-mark expiry). 8.11's last two options (Bluetooth on core 1, two
blocks per packet) needed a design review before anyone built them. The first draft of this design went to an
Opus review (an agent that hadn't written it). This is the design after that review. The main changes: the
priority facts were wrong, the CPU gate couldn't see interrupts, the A/B was too weak, and the AP's hop to the
follower had been left out.

**What runs where (IDF 4.4.8, this build).**
- Core 0: the Wi-Fi task (priority 23, `ESP32_WIFI_TASK_PINNED_TO_CORE_0`), the BR/EDR controller task
  (`btController`, 23) and its high-level interrupts (`BTDM_CTRL_HLI`), and Bluedroid: `hciT` 22, `BTU_TASK` 20,
  `BTC_TASK` 19 (which also runs the A2DP sink's SBC decode). lwIP's `tiT` is 18 with no affinity. The party
  worker is 5, on core 1.
- So Bluedroid is below the Wi-Fi task and can't take CPU from it. Only the controller task (at the same
  priority) and the Bluetooth and Wi-Fi interrupts can. Moving controller and Bluedroid to core 1 would put
  priority 19–23 tasks over the audio path to free almost nothing for Wi-Fi. If core 0 turns out short, the
  options are moving the Wi-Fi task to core 1 (`ESP32_WIFI_TASK_PINNED_TO_CORE_1`) or the Wi-Fi IRAM options.
  Those cost internal RAM, and there are 29.7 KB free with Spotify against a 30 KB goal.

**Step 2: measure core 0's free CPU first, and build a core move only if it is short.**
- FreeRTOS run-time stats bill interrupt time to the task that was interrupted, so `idle_pct` counts the Wi-Fi
  and Bluetooth interrupts on core 0 as idle. The round 2 lab image adds a probe: each core's idle hook spins
  (no `WAITI`) and counts the cycles between its passes that nothing else took. That is `free_pct` in
  `diag/tasks`, next to `idle_pct`. It is lab-only (the spin costs power), in its own commit, never in a PR.
- `bt_conceal_run.py --cpu` reads it with each sample, and the run log times each sample against the drain in
  the same seconds, so a fall in the drain can be lined up with core 0's free CPU.
- Gate: a core-move image is built only if core 0's `free_pct` is under about 10% in the windows where the drain
  is low, and clearly higher in windows where it isn't. If core 0 has free cycles while the drain falls, the
  limit is air, not CPU, and step 2 is dropped with those numbers.

**The AP's hop to the follower.** The leader sends each packet to the AP, and the AP sends it on to the
follower. The board has AMPDU RX off (`board/lite-main/sdkconfig.defaults`), so the AP sends each packet to the
follower as its own frame and ACK: 125 exchanges a second a follower, on a channel Bluetooth already takes slots
from. Turning AMPDU RX on is a compile-time switch (`wifi_init_config_t.ampdu_rx_enable` is read once at
`esp_wifi_init`), with a window of 10 frames: IDF refuses a window over twice the 5 static RX buffers, so it costs no
extra buffers, and its internal-RAM cost is read on the board. It applies to
followers only, so a follower image `a7fcd-r2rx` tests it. The follower's power-save mode is logged with each
run.

**Step 3: two blocks per packet doesn't fit this build, so pair sending stands in for it.**
- A party packet is 1,454 B, and two blocks would be about 2.9 KB. IP fragmentation is off (`LWIP_IP4_FRAG`
  unset), and an 802.11 MSDU is at most 2,304 B, so no setting puts both in one plain frame.
- AMPDU TX (on since #245) already sends packets that are queued together as one exchange. So the cheap test is
  to put them in the queue together: `leader_send` starts a new stream packet only when two are ready (704
  frames, 16 ms), then sends both back to back. Resends and the held packet keep their rules, and a held packet
  goes first on the next pass. The first of each pair goes out up to 8 ms later, against a 300 ms head start.
- It's a runtime switch: party op `{"op":"pair","n":1|2}`, set by the Hive or over USB, refused from the LAN
  like `key` and `tone`. `party/stats` shows it as `net.pair`, with `net.pk` counting stream packets sent (no
  clocks or resends). The default stays 1.
- A null result is ambiguous: when the driver queue is already full, AMPDU already aggregates, and passes
  already pair in part (a pass is 10–20 ms, longer than a block). So aggregation itself is read where the tools
  allow, from the driver's own statistics or a sniffer. There is no spare Lite for a sniffer, since Lites 1 and 4
  are never used.

**How it's judged.**
- The pass mark is the follower's concealed share (missed frames over the window's frames), plus its late
  (`old`) and skipped frames. The drain counts only stream packets (`net.pk`), as a secondary figure.
- Pair sending: one session on one boot with A2DP up throughout, alternating 150 s windows of pair 1 and pair
  2, 9 of each (`--phases a2dp/p1:150,a2dp/p2:150 --repeat 9`). Each pair of windows gives a difference in
  concealed share, and the run reports their mean, spread and sign count. The leader's uptime is logged per
  window, so a drift after boot shows.
- AMPDU RX: the follower's image changes, so sessions alternate A, B, B, A (a Wi-Fi flash is about 70 s), each a
  continuous A2DP window.
- Boards: as 8.11, so its numbers stay the baseline. Lite 2 leads, Lite 3 follows, Lite 5 streams A2DP. Lite 3
  keeps losing power, so sessions are short, and a session cut by a power loss is rerun, not stitched. Lite 5
  stays clear of the nightly run at 08:00Z. Lites 1 and 4 are never used.
- Order: AMPDU RX on the follower first (the hop nobody has measured), then pair sending, then a core move only
  if the probe shows core 0 short.
