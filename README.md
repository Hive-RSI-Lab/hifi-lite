# HiFi Lite

**A streaming stereo amplifier on one 118 × 90 mm board, powered from a USB-C laptop charger.**

Plug in a 65 W USB-C charger and a pair of passive speakers. It plays Spotify
Connect, AirPlay, Lyrion (LMS) and Bluetooth, takes a DJ deck or any line source
on RCA, drives a subwoofer from an RCA sub out, and is set up and controlled
from a web app on your phone, three buttons or any IR remote.

![HiFi Lite board, 3D render](docs/images/board-iso.png)

The board, its firmware, its web app, its 3D-printed case and its test bench
were designed by a team of AI agents built on Claude, under the direction of
one person. Every file the agents produced to do it is here: the KiCad
project, the scripts that generated the schematic, the simulations, the
checks and reviews, the firmware patches, the case in code, and the bring-up
reports from the five boards that were built.
**[DESIGN.md](DESIGN.md)** is the whole story, step by step.

| | |
|---|---|
| ![enclosure](docs/images/enclosure-iso.png) | ![web app](docs/app-screens/play.png) |
| Snap-fit 3D-printed case, no screws | The web app, served by the board itself |

## What it is

| | |
|---|---|
| **MCU** | ESP32-WROVER-E (8 MB flash, 8 MB PSRAM): Wi-Fi and Bluetooth Classic |
| **Codec** | Everest ES8388: DAC for the amp and the sub out, ADC for the line in |
| **Amplifier** | TI TPA3118D2 class-D, 2 × 38.7 W into 4 Ω at 20 V (1 % THD, from the design review's calculation) |
| **Power** | USB-C PD sink (WCH CH224A) asking for 20 V; runs on a 65 W or larger charger, with a power governor that keeps the amp inside the charger's contract |
| **Sources** | Spotify Connect, AirPlay, Lyrion/LMS, Bluetooth A2DP, RCA line in (DJ direct mode) |
| **Outputs** | Four 4 mm banana sockets for the speakers, RCA sub out (L/R) |
| **Controls** | Web app, three buttons (−, +, play/pause; hold for track skip and standby), IR receiver that learns your TV remote |
| **Board** | 118 × 90 mm, 2 layers, 1.6 mm FR-4, 154 parts all on the top side, assembled by JLCPCB |
| **Cost** | about $14.54 a board in parts, assembly and PCB at 100 units ([docs/bom-estimate.csv](docs/bom-estimate.csv)) |
| **Firmware** | [squeezelite-esp32](https://github.com/sle118/squeezelite-esp32) plus our own component: power sequencing, the governor, test signals, the test API and the web app |

Five boards were built and brought up; all five pass the self-test. The
measurements are in [docs/lab/reports](docs/lab/reports/README.md).

## What's in this repo

| Folder | What |
|---|---|
| [`hardware/`](hardware/README.md) | The board: KiCad 10 project, schematic generator, part data, placement and routing scripts, simulations, check reports, design reviews and the JLC fab package (`hardware/fab/`) |
| [`firmware/`](firmware/README.md) | The ESP32 firmware: the pinned upstream, our patch, our `hifi` component, host unit tests and the build scripts |
| [`app/`](app/) | The web app the board serves (built into the firmware), its mock server and tests |
| [`enclosure/`](enclosure/README.md) | The case, as build123d code fitted to the KiCad board, with STL, STEP and sliced print files |
| [`bench/`](bench/README.md) | Bring-up and test from a laptop: find, flash, self-test, power sweep, thermal soak |
| [`docs/`](docs/) | The [user manual](docs/manual/user-manual.md), [bring-up guide](docs/bringup.md), [firmware design](docs/firmware.md), [app](docs/app.md), [test API](docs/test-api.md), [product plan](docs/product.md), [known unknowns](docs/known-unknowns.md) and the lab reports |
| [`DESIGN.md`](DESIGN.md) | How it was designed, from the brief to the bench |

The firmware and the case are shared with two bigger boards in the same
family (Plus and Ultra, on a common core board). Their build targets are left
in `firmware/board/` so the build matches what was flashed, but this repo is
about the Lite.

## Build it yourself

### Order the board

`hardware/fab/` has what JLCPCB asks for: `lite-main_gerbers.zip`, `BOM.csv`
and `CPL.csv` (LCSC part numbers, rotations checked against the 3D models).
Order 2 layers, 1.6 mm, 1 oz copper, OSP finish, top-side assembly. The four
banana sockets overhang the rear edge by design and need an order remark, and
a few parts may need pre-buying (`hardware/fab/prebuy.csv`); read the
ordering notes in [hardware/README.md](hardware/README.md) first.

### Build and flash the firmware

Run the host tests first (no board and no ESP-IDF needed):

```sh
make -C firmware/test
```

Then, with ESP-IDF v4.4.8 installed ([firmware/README.md](firmware/README.md)
has the steps):

```sh
firmware/tools/build.sh                       # fetches upstream, patches, builds
cd ~/fw-build/squeezelite-esp32 && idf.py -p /dev/ttyUSB0 flash monitor
```

The board's USB-C port is also its serial console (CH340B), so flashing needs
no extra adapter. Spotify Connect needs your own free Spotify developer app
key at build time (`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`); nothing
secret is in this repo.

### Print the case

`enclosure/out/lite-snap/` has the screwless snap-fit case as STL and STEP,
and sliced files for a Bambu A1 mini. `enclosure/out/lite/` has the screwed
version. Print in PETG or ASA if the amp will run hot; see
[enclosure/README.md](enclosure/README.md).

### Set it up

Power it from a 65 W+ USB-C PD charger, join its own `hifi-lite-…` Wi-Fi
network from your phone, and the web app walks you through joining your home
Wi-Fi.
The [quick start](docs/manual/quick-start.md) has the rest.

## How it was made

The agents worked through the same steps a hardware team would: a product
plan, part choices against cost and availability, a schematic written as
Python, placement and routing with scripted checks, SPICE and thermal
simulations, rounds of design review by separate agents, a fab package,
firmware tested on a PC and in QEMU before any board existed, a case fitted
to the board in code, and then bring-up on real boards through a remote test
bench. A person approved the order, paid for it, and plugged things in.
[DESIGN.md](DESIGN.md) covers each step, what went wrong, and what the next
board would change.

## Status

A first spin. Five boards were built and all pass self-test; one is in
everyday use as a listening amp. The open items, including measurements still to
make, are in [docs/known-unknowns.md](docs/known-unknowns.md) and at the end
of [DESIGN.md](DESIGN.md).

## Third-party code

The firmware builds on
[squeezelite-esp32](https://github.com/sle118/squeezelite-esp32) and
ESP-IDF, fetched at build time at the commit in `firmware/upstream.lock`;
`firmware/third_party/` holds the small pieces vendored in, with their own
licenses. Part footprints and 3D models come from the KiCad libraries and LCSC.

## Reading the working docs

Most documents under `hardware/`, `docs/` and `docs/lab/` are the agents'
own working notes, kept as they were written. A few words in them are
local:

- **the person:** the human who directed the agents, approved the order
  and did the hands-on steps.
- **the Hive, H0, H2:** the groups of AI agents that did the work. H0 did the
  board design in October 2026; H2 took over from 2026-10-31 for ordering,
  bring-up and the firmware work on real boards.
- **the tower:** the build machine the agents ran on.
- **task numbers, issue and PR numbers** (`#136`, `task 261030-140102`):
  references to the working repo the design came from.
- **lite-1 … lite-5:** the five boards that were built.
