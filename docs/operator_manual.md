---
title: "Koenig Wildfire — Operator Manual"
subtitle: "MSU CubeSat Program"
author: "MSU CubeSat Team"
date: "Draft — built from latest source"
---

# About this manual

This is the user-facing guide for operating the Koenig Wildfire camera
payload from a laptop. It assumes:

- You have the assembled payload (Raspberry Pi 4 + Arducam multiplexer
  + three IMX296 cameras with narrowband filters) powered and booted.
- You have a laptop with a modern web browser.
- You have **no prior Linux or networking experience** — anything we need
  you to know is explained here.

If something in this manual doesn't match what you see on screen, the
manual is wrong — please tell whoever handed you the gear, and check
that you're on the latest version (the version line at the top of this
PDF tells you when it was built).

> **Status — Phase 1 (docs scaffold).**
> The web interface this manual describes does not exist yet. Sections
> below are placeholders that will fill in as features are built in
> Phases 2–6. The structure won't change much; the screenshots and
> step-by-step procedures will land later.

# Quick start

*To be filled in after Phase 2.*

The three-line version will eventually be:

1. **Power on** the payload (toggle the power switch on the bottom plate).
2. **Connect your laptop to wifi** named `satnet` (password: `cubesat1`),
   or — if you're in the lab — plug an Ethernet cable into the Pi.
3. **Open your browser** to `http://koenig-pi.local:8000` and click
   **Capture**.

![Figure 1: Payload power switch and wifi sticker (placeholder)](img/quickstart_payload.png){ width=80% }

![Figure 2: The main page on first load (placeholder)](img/quickstart_homepage.png){ width=80% }

# What this thing does (one paragraph)

The payload takes three pictures of the same scene through three
different narrowband filters: 762 nm, 766 nm, and 770 nm. Burning
vegetation releases potassium vapor that glows brightly at 766 nm and
770 nm but **not** at 762 nm. By comparing the three pictures
pixel-by-pixel, the science team can pick out fires against everything
else in the scene. You don't need to do this comparison yourself — your
job is to **capture good pictures**, which means: get the focus right,
get the exposure right, and frame the target. The math happens after.

The full physics is in the K-line primer (separate document) if you're
curious.

# Interface walkthrough

*To be filled in after Phase 2.*

## The main page

![Figure 3: Annotated main page (placeholder)](img/main_page_annotated.png){ width=85% }

| # | Element | What it does |
|---|---|---|
| 1 | *(placeholder)* | *(placeholder)* |

## Capturing pictures

*To be filled in after Phase 2.*

### Single capture

### Burst capture

*To be filled in after Phase 4.*

### Timed (interval) capture

*To be filled in after Phase 4.*

## Reviewing pictures

*To be filled in after Phase 2.*

## Deleting pictures

*To be filled in after Phase 2.*

## Changing camera settings

*To be filled in after Phase 3.*

> **Important.** The science only works if all three cameras use the
> **same** exposure, gain, and white-balance settings. The default
> settings panel locks them together for this reason. There is an
> **Advanced** mode that lets you change settings per-camera — use it
> only for diagnostics, not for capture runs that go to the science
> team. The interface will show a red warning banner whenever Advanced
> mode is on.

## Focus mode

*To be filled in after Phase 5.*

Focus mode lets you watch a single camera's live video while you turn
the lens to focus it on your target. Because of how the multiplexer
works, you can only watch one camera at a time — focus one, switch,
focus the next.

# Operating the payload in the field

*To be filled in after Phase 5.*

## On a drone

## On a balloon

## In the lab

# Troubleshooting

*Sections will fill in as we encounter and fix the issues in practice.
What's listed here is the menu of likely problems.*

## "I can't connect to the wifi"

*(placeholder)*

## "The web page won't load"

*(placeholder)*

## "Only one (or two) cameras show up"

*(placeholder)*

## "The pictures are all black / all white"

*(placeholder)*

## "It says 'busy' when I press capture"

*(placeholder)*

## "I'm out of disk space"

*(placeholder)*

# Glossary

**Burst.** A group of pictures taken back-to-back from one button press.
You set the size (e.g. "burst of 10"); the system captures that many
through each filter before stopping.

**Capture.** One full set of three pictures, one per filter (762 / 766
/ 770 nm). All three are saved with the same timestamp so the science
team can match them up.

**Filter.** A piece of glass in front of each camera that only lets
through light of one specific colour (wavelength). Our three filters
pass narrow slices of near-infrared light — invisible to your eyes but
visible to the cameras.

**Focus mode.** A live-video view of one camera, used so you can turn
the lens until the picture is sharp.

**Multiplexer (or "mux").** A circuit board that lets one Raspberry Pi
talk to three cameras one at a time. The Pi switches between cameras
electronically rather than having three Pis.

**Off-line / on-line.** The 766 nm and 770 nm cameras sit **on** the
potassium emission line — that's where fires show up. The 762 nm camera
sits **off** the line — that's the reference for "everything else."
The science team compares on-line vs off-line to find fires.

**K-line / potassium line.** The specific wavelengths of light that
glowing potassium atoms emit. Burning vegetation contains potassium,
which is why this works for wildfires.

# Where to get help

*(Contact info to be added.)*
