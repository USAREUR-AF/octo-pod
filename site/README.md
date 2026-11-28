<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# The landing page

A single static page, published to GitHub Pages by `.github/workflows/pages.yml`.
No framework, no build step, no external fonts, scripts or requests.

```bash
python3 site/build.py                                  # check the page against the tree
python3 -m http.server 8000 --directory site           # look at it on http://localhost:8000/
```

## What each file is

| File | What it is |
|---|---|
| `index.html` | the page: markup, CSS, and the script that replays the capture and copies the command |
| `assets/icon.svg` | the tab icon |
| `assets/packets.json` | the recorded run the first screen replays (below) |
| `captures/capture-packets.sh` | records `assets/packets.json` |
| `captures/packets/` | the small Rust program it runs: a Zenoh router on loopback (the arms' own test pen, `octo_arm_common::testing::TestPen`), two copies of the real `udp-ingest` arm against it, and a sender |
| `captures/capture-cli.py` | records `captures/cli-session.txt`: the real CLI declaring a sensor arm on a scratch pod identity, with the test harness in `platform/tests/test_cli.py` |
| `captures/cli-session.txt` | that session, shown on the page as it is |
| `build.py` | the check the pages workflow runs |
| `*.license` | copyright and license for the two recorded files, which cannot carry a comment |

## The recorded run

`captures/capture-packets.sh` builds `arms/udp-ingest` and runs it twice against a
Zenoh router on loopback: `sensor1` allows `127.0.0.1`, `sensor2` allows only
`192.0.2.30`. A sender at `127.0.0.1` sends 24 synthetic datagrams, about four a
second, every fourth one to `sensor2`. The program writes every send, every
publication the router saw, and every line the two arms logged, with times in
milliseconds. The page replays them at their recorded times; with reduced motion
it shows the end state. Run the script again to record a new run; `build.py`
then tells you which numbers on the page to change.

## Where each number comes from

`build.py` measures each one and fails when the page says something else. The page
marks each with `data-n`.

| Page says | `data-n` | Measured from |
|---|---|---|
| 18 / 18 datagrams published | `published`, `sent1` | `assets/packets.json`: publications the router saw; datagrams sent to `sensor1` |
| 1 ms, datagram to router, median | `median-ms` | the same file: median of publication time minus send time, rounded |
| 6 / 6 from a source not allowed, dropped | `dropped`, `sent2` | `sensor2`'s last `stats` line, `dropped_source=`; datagrams sent to `sensor2` |
| 10 Rust crates in arms/ | `crates` | `members = [...]` in `arms/Cargo.toml` |

`build.py` also fails if the code shown faintly behind the first screen is no
longer in `arms/udp-ingest/src/main.rs`, or if the CLI session on the page differs
from `captures/cli-session.txt`.
