# Source handover: Bluetooth, Spotify and AirPlay

Rung: "Bluetooth and Spotify hand over by themselves" (goals.md, k2410). The person's words: "when you connect to it
via bluetooth it should automatically exit spotify wifi mode and vice versa".

Firmware: patch 0034 (`firmware/patches/0034-source-handover.patch`) and the rule in
`components/hifi/core/hifi_source.c`, branch `h2/lite-source-handoff` of hifi-receiver.

## Before

Read from the patched source on h2/lite-bringup 759d6b3:
- Every sink refused to start while another external source played, so the first one kept the output.
- A phone on Bluetooth pressing play while Spotify played was refused and sent an AVRCP STOP. Spotify kept playing.
- Picking the Lite in Spotify while Bluetooth played was refused at the login, so the phone showed a failed connect.
- Spotify paused, then Bluetooth played: Bluetooth got the output, but the Spotify session stayed linked and the
  phone still showed the Lite as its speaker.
- LMS took over from everything, but for Bluetooth it sent only an AVRCP STOP, so the phone stayed connected.
- A Spotify playlist's end (cspot's CSPOT_STOP) was handled nowhere. The output stayed "playing" with the last title
  and held the output until another source took it (rough edge 2 in hifi-lite-tests.md).
- Bluetooth play, stop and rate events didn't check that Bluetooth held the output, so a stale stop could flush
  another source's buffer. Data from a refused source could reach the output buffer.

## The change

- Among Bluetooth, AirPlay and Spotify, the source that starts audio last takes the output. The rule is one plain-C
  function (`hifi_source_decide`) with host tests (`test/test_source.c`). The sinks call it where they used to
  refuse.
- The handover starts with a quiet fade: the output ramps down over 200 ms in 10 steps, the amp stays on and nothing
  restarts (`hifi_handover_quiet`, the same ramp as the #238 soft mute). Then the old source is ended and the new one
  starts. The volume comes back when the new source's audio starts.
- Ending Spotify: pause, then the session ends, so the phone no longer lists the Lite as playing.
- Ending Bluetooth: an AVRCP pause, then the A2DP link drops 1 s later (`bt_sink_let_go`). LMS, Spotify or AirPlay
  starting afresh does the same to an idle Bluetooth phone (`decode_phones_yield`). Their next track doesn't, so a
  phone that connected since is kept.
- A Spotify playlist's end lets the output go and clears the title, as Bluetooth and AirPlay stopping do. A stop from
  a source that doesn't hold the output is acknowledged and changes nothing.
- Play, stop and rate events from a sink that doesn't hold the output are ignored. Its data never reaches the output
  buffer: the sink checks that it owns the output again under the output lock, and again after each wait for room,
  so a phone that loses the output mid-write stops writing at once.
- A Spotify volume change that comes while another source holds the output is kept. It is applied when Spotify's
  track takes over, so Spotify doesn't play at the Bluetooth phone's volume.
- Lab images also get the lab experiment "phone" (`components/hifi/esp/lab_phone.c`): a stand-in for a phone's
  Spotify app. The product image doesn't link it.

Decided: newest wins. Starting audio on any of them takes over from the one playing.
Decided: Bluetooth takes over when the phone starts audio (presses play), not on the bare connect. Otherwise a phone
that reconnects by itself as someone walks in would cut off someone else's Spotify. Connect, then press play, and
Spotify stops.
Decided: while another source plays, a phone's audio takes over only once the phone reports playing (AVRCP play
status), or after 1 s for a phone that can't report it (5 s at most for one that can but doesn't). Audio that stops
before then, such as a notification sound or a keyboard click, is held and dropped, so it never cuts off Spotify.
From idle, a phone's audio plays at once as before.
Decided: Bluetooth gives way with a pause and then a disconnect, not a pause alone. A paused link would let a
notification sound take the output back.
Decided: LMS, the test tone, line-in and party keep their rules. A party follower stays a follower, and Bluetooth
stays off in party mode.
Decided: the Spotify login is accepted whenever its track could start. A phone picking the Lite isn't turned away
because Bluetooth or AirPlay is playing; its track start then takes over.
Decided: a Spotify playlist end lets the output go. The Lite shows idle, and the next source starts without a
takeover.

## Lab results, 2026-11-18

Bench: Lite 3 (lite-3) as the receiver, on the lab image af1e3-handoff-lab. Lite 5 (lite-5) as the
Bluetooth phone: the lab's a2dp experiment as a source, a 441 Hz tone over A2DP to Lite 3's product Bluetooth sink.
Lite 3's own "phone" experiment stands in for the Spotify app. It calls the Spotify sink's handlers in the order cspot
does (login check, track start, play, audio), so everything from the handler on is the product's path.

Script: `lab/tools/handoff_run.py`. Runs: `lab/data/handoff-lab1-2026-11-18T021247.json` (3 cycles) and
`lab/data/handoff-lab2-2026-11-18T021634.json` (5 cycles). Each cycle: Bluetooth plays, Spotify takes over, Bluetooth
takes it back while Spotify plays, Bluetooth stops. Then, once per run, Spotify plays from idle to a playlist's end.

| What | Measured (8 cycles) | How |
|---|---|---|
| Spotify takes over from a playing Bluetooth phone: login and start accepted | 8 of 8 | phone experiment's result |
| Spotify's track start call (the quiet fade, Bluetooth's pause, the takeover) | 203–227 ms | timed on the board |
| Spotify login to its first audio taken by the output | 220–244 ms | timed on the board |
| The Bluetooth phone's link dropped after the takeover | 8 of 8, 1 disconnect each; seen ≤2.2–3.2 s (the 1 s let-go plus polling over serial) | source's a2dp result |
| Bluetooth takes it back while Spotify plays: connect to streaming | 837–3111 ms (median about 1.6 s) | timed on the source board |
| The sink plays Bluetooth after it takes over | 8 of 8, within 0.5 s of the source streaming | state/audio |
| Bluetooth steady after it took over | 0 disconnects, longest gap between audio callbacks 27–29 ms | source's a2dp result |
| Bluetooth from idle: connect to streaming | 934–1875 ms | timed on the source board |
| A Spotify playlist end lets the output go | 2 of 2 (the output plays nothing after it), and Bluetooth streams right after | phone result, state/audio |
| Receiver restarts | 0; heap steady, about 34 KB free | uptime and heap from diag |

Faults: 0 in lab2. Lab1's three "faults" were the script's scoring (below) and were fixed in the script, not the
firmware.

### What the runs showed

- The Spotify stand-in saw the output owner as 0 (idle), not 2 (Bluetooth), when Bluetooth took it back. A takeover
  lets Spotify go and Bluetooth claims the output in the same handler call, a few ms apart. The stand-in's 10 ms
  check landed between the two every time, probably while the handler logs over the UART. The buffer is flushed and
  quiet at that point, so nobody hears a gap. The script now accepts 0 or 2 there, and the owner that counts is what
  the sink plays next, which was Bluetooth every time.
- The playlist end's wait for queued audio hit its 5 s limit. The output buffer holds about 12 s of audio, and the
  stand-in writes as fast as the output takes it. Real cspot (Shim.cpp:599) waits for the buffer to empty before it
  sends CSPOT_STOP, so this is a lab-only effect. A takeover flushes the buffer, so a handover never plays stale audio.
- The board's remote log shows API traffic only, not the firmware's info lines, so the fade's own log line wasn't
  seen. Its time is inside the 203–227 ms start call.

### Since the lab runs: the review's fixes

The independent review of #249 found the points above (the ownership check, notification sounds, idle phones dropped
at each track, the Spotify volume). They are fixed in patch 0034 and checked by host tests that build upstream's own
patched code against stubs (`test/upstream/test_up_bt.c` and `test_up_ext.c`). Each test was checked to fail with its
fix taken back out. The one timing these change: when another source plays, Bluetooth taking over now waits for the
phone's play report, about 1 s more for a phone that can't send one.

**Rerun on the merged code, 2026-11-18 08:54Z.** Lite 3 on `af1e3-handoff-lab3`, built from the h2/lite-bringup tip
6fb7531 (the handover with the review's fixes, plus #253's self-test fix). Lite 5 as the Bluetooth source on
`hifi-lite-lab-ps241`. Same script, 2 runs of 5 cycles: `lab/data/handoff-lab3-2026-11-18T035450.json` and
`lab/data/handoff-lab4-2026-11-18T040004.json`.

| What | First lab runs (8 cycles) | Merged code (10 cycles) |
|---|---|---|
| Spotify takes over from a playing Bluetooth phone: login and start accepted | 8 of 8 | 10 of 10 |
| Spotify's track start call | 203–227 ms | 201–241 ms |
| Spotify login to its first audio taken by the output | 220–244 ms | 221–253 ms |
| The Bluetooth phone's link dropped after the takeover | 8 of 8, seen ≤2.2–3.2 s | 10 of 10, 1 disconnect each, seen ≤2.2–2.7 s |
| Bluetooth takes it back while Spotify plays: connect to streaming | 837–3111 ms (median about 1.6 s) | 945–3024 ms (median about 2.1 s); 1 of 10 needed a second try (below) |
| The sink plays Bluetooth after it takes over | 8 of 8, within 0.5 s | 10 of 10, within 0.5–0.6 s |
| Bluetooth steady after it took over | 0 disconnects, longest audio gap 27–29 ms | 0 disconnects, longest audio gap 27–30 ms, none over 50 ms |
| Bluetooth from idle: connect to streaming | 934–1875 ms | 1052–4420 ms (one 4.4 s; the rest ≤2.1 s) |
| A Spotify playlist end lets the output go | 2 of 2 | 2 of 2, and Bluetooth streams right after |
| Receiver restarts | 0; about 34 KB free | 0 (uptime rose 101 → 663 s through both runs); 34–35 KB free, lowest 27 KB |

The median for Bluetooth taking it back rose by about 0.5 s, which is within the 1 s that the wait for the play report
can add. Faults: 1, in lab3's cycle 4. The first Bluetooth connect over Spotify failed with "connect failed, reason 0"
after 7.7 s: the sink didn't answer the page. The script's retry 19 s later connected in 1.55 s, and the sink played
Bluetooth. By then the Spotify stand-in's 20 s run had ended by itself, so there was nothing to hand over. The script
counts that as "Spotify not handed to Bluetooth". It isn't a stuck or silent state, and no reboot was needed. It is
the only failure out of 23 Bluetooth connect attempts across these two runs, and none of the 18 in the first runs
failed. The 5-cycle run straight after it had no faults and no retries. The cause isn't known. It isn't the lab
sink's kept bond (`lab/espnow.md`), because the retry worked with no reboot. A real phone retries a failed page by itself.

Desk look at the page scan under Wi-Fi (sdkconfig and `bt_app_sink.c`, no flashing). The window doesn't shrink: the
sink sets only the scan mode, so Bluedroid's defaults hold (`bt_target.h`): an 11.25 ms interlaced page scan every
1.28 s, the slowest R1 setting, about 1–2% of radio time. Coex is software coex at the default BALANCE preference, and
the sink never sets it. Wi-Fi traffic can take scan windows rather than shorten them. A phone pages for about 5 s,
which covers only about 4 of them, so a few lost to Wi-Fi give exactly this "reason 0" after about 7.7 s. The bench's
Spotify stand-in sends no Wi-Fi audio, so a real stream would make it more likely.
Cheap fix to try: `BTM_DEFAULT_CONN_INTERVAL` 0x0400 (640 ms, twice the chances) and/or `BTM_DEFAULT_CONN_WINDOW`
0x0024 (22.5 ms), set as compile defines for the bt component (`bt_target.h` guards both with `#ifndef`). That takes
about 2% more air time from Wi-Fi for either change, about 5% for both, while the sink is connectable. Untested; it
needs a bench run with real Wi-Fi load on the sink, counting failed connects over Spotify.

Lite 3's own self-test failed on loopback (the right channel's level) on its first run after each flash, both on this
build and on a7fcd-party6. On this build it also failed once later in the same boot. The other runs passed. This
is the self-test's loopback check, not the handover; it was handed to the self-test fix in lite-channel-mode.

## Not covered on the bench

- The stand-in calls the Spotify sink's handlers directly, so cspot itself isn't exercised: the Spotify servers, the
  zeroconf login, the decoder, and cspot_sink.c's title and display handling.
- The pop was not measured acoustically. The fade is the same ramp as #238's soft mute.
- AirPlay isn't on the bench. It goes through the same rule and the same takeover calls.
- A real phone's Bluetooth stack: the lab source is another Lite, which takes the pause and the disconnect quietly.
  A real phone may show a notification or try to reconnect.

Needs a person with a phone, once the image is on a Lite:
1. Play something on the phone over Bluetooth to the Lite. Then, in the Spotify app on another phone (or the same
   one), pick the Lite. Spotify should play within about a second. The Bluetooth phone should show paused and then
   disconnected.
2. While Spotify plays, connect the Bluetooth phone and press play. Bluetooth should play. The Spotify app should
   stop showing the Lite as its speaker.
3. Let a Spotify playlist run to its end. The Lite's app should show idle, not "Playing" with the last title.
