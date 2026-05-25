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

> **Status — Phase 2 (single-camera spike).**
> Sections covering single capture, gallery, and delete are live and
> describe the actual interface. Burst, timer, multi-camera, settings,
> and focus mode are still placeholders — those features land in
> Phases 3–5.

# Quick start

> **Phase 2 caveat.** The payload currently has **one** camera connected,
> not three, and **no filters**. You can still take pictures and confirm
> the interface works end-to-end. Three-camera operation lands in Phase 3.

1. **Power on the Pi.** Plug in the USB-C power cable. Wait about 30
   seconds for it to boot.
2. **Connect your laptop to the same wifi as the Pi.** In the lab that
   means whatever wifi the Pi is configured for. (Field AP-mode lands
   in Phase 5.)
3. **Open your browser** to `http://koenig-pi.local:8000`.
   - If that doesn't resolve, use the Pi's IP address directly:
     `http://<pi-ip>:8000`.
4. **Click Capture.** A picture appears in the gallery below. Click the
   thumbnail to open it full-size in a new tab.

![Figure 1: The main page on first load (placeholder)](img/quickstart_homepage.png){ width=80% }

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

![Figure 2: Annotated main page (placeholder)](img/main_page_annotated.png){ width=85% }

| # | Element | What it does |
|---|---|---|
| 1 | **Capture** button | Takes one picture, adds it to the gallery below. |
| 2 | **Clear all** button | Deletes every image in the gallery (asks first). |
| 3 | Image card | A thumbnail of one captured picture, with its filename and a per-image **Delete** button. Click the thumbnail to open the full-size image in a new tab. |

The filename of each picture is its UTC timestamp:
`YYYYMMDD_HHMMSS_mmm.jpg`. Sort order in the gallery is newest first.

## Capturing pictures

### Single capture

Click the blue **Capture** button. After about a second the gallery
refreshes with the new picture on top. That's it.

If the page seems to hang after you click, the camera is still warming
up — give it ten seconds, then reload the page.

### Burst capture

*To be filled in after Phase 4.*

### Timed (interval) capture

*To be filled in after Phase 4.*

## Reviewing pictures

Every capture lands in the gallery on the same page. Click any
thumbnail to open the full-size image in a new browser tab — from
there you can right-click and **Save As** to copy the file to your
laptop, or share the URL with someone else on the same network.

The gallery refreshes whenever you load or reload the page; it does
**not** auto-update while you're staring at it. Reload to see new
captures.

## Deleting pictures

There are two ways to delete:

- **One picture at a time:** click the **Delete** button on its card.
  Confirm in the popup. The page reloads with that picture gone.
- **All pictures:** click the **Clear all** button at the top. The
  count in parentheses tells you how many will be deleted. Confirm
  in the popup.

Deletion is immediate and **cannot be undone** — there is no recycle
bin. If you might need a picture later, save it to your laptop first.

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
