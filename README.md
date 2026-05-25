# koenig_wildfire

K-line wildfire-detection payload for the MSU CubeSat / drone program.

Three narrowband NIR cameras (762 / 766 / 770 nm) image the same scene
through different filters. A pixel-wise ratio of the on-line (766, 770 nm)
to off-line (762 nm) channels picks out the neutral-potassium emission
that burning vegetation releases — a wildfire fingerprint that a small
silicon-sensor payload can see without thermal IR optics.

The physics is in [`docs/k_line_primer.md`](docs/k_line_primer.md).

## Current state

This repository is mid-rewrite. The old architecture (three Pi Zero 2 W
camera nodes + ESP32/SIM7600 flight controller talking LTE to a Flask C2)
has been archived under [`legacy/`](legacy/) and tagged
`v0.2-his-final-snapshot` on `main`. Going forward, the system is a
single Raspberry Pi 4 with an Arducam Multi Camera Adapter v2.2 driving
three InnoMaker IMX296RAW global-shutter cameras, controlled by a Flask
web UI on the Pi.

The implementation is being built in phases. See
[`docs/architecture.md`](docs/architecture.md) for the target design and
the operator manual ([`docs/operator_manual.md`](docs/operator_manual.md))
for the user-facing build that grows feature-by-feature.

## Repo layout

| Path | What's here |
|---|---|
| `pi/daemon/` | Capture daemon — owns cameras, mux, scheduler |
| `pi/webui/` | Flask web UI the operator's browser talks to |
| `pi/shared/` | Settings schema and IPC shared between daemon and UI |
| `pi/systemd/` | systemd unit files for auto-start on boot |
| `pi/tests/` | Unit + integration tests |
| `docs/` | Operator manual, architecture, primer, references |
| `docs/img/` | Screenshots and figures referenced from markdown |
| `tools/` | Build scripts (PDF render, install helpers) |
| `hardware/` | Filter-holder STLs and SolidWorks sources |
| `assets/` | Build photos (v0.1, v0.2 of the old stack — kept for reference) |
| `legacy/` | Archived pre-rewrite code (see `legacy/README.md`) |

## Building the operator manual

The user-facing PDF is built from `docs/operator_manual.md` with pandoc
and a mermaid-aware preprocessor. See `tools/render_pdf.sh` (added in
Phase 1).
