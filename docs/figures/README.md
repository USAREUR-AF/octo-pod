<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Figures

`make-figures.py` draws every figure in this directory. Each figure is written in
a light and a dark variant for a `<picture>` element. Every label it draws must
appear in `README.md`, `docs/images.md`, `docs/faq.md` or `docs/operations.md`;
the script refuses to draw a label those files do not state.

```sh
python3 -I docs/figures/make-figures.py           # write the SVG files
python3 -I docs/figures/make-figures.py --check   # exit 1 if a committed file is out of date
```

| Figure | Shows | Source |
|---|---|---|
| `octo-pod-boundary` | one pod at one site: sensor feeds through ingress arms or Zenoh peers into the pen, local capture and audit, the uplink bridge carrying only released keys to the fabric, and the downlink gate bringing approved keys back to egress arms and their receivers | the Functions section of `README.md`, the image roles in `docs/images.md`, the egress table in `docs/operations.md` |

Regenerate after an arm or destination type is added or renamed. Text is at least
13 units on an 880-unit canvas, so at least 12 px when shown 830 px wide.
