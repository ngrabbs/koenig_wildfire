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
  + three cameras with narrowband filters) powered and booted.
- You have a laptop with a modern web browser.
- You have **no prior Linux or networking experience** — anything we need
  you to know is explained here.

If something in this manual doesn't match what you see on screen, the
manual is wrong — please tell whoever handed you the gear, and check
that you're on the latest version (the version line at the top of this
PDF tells you when it was built).

> **Status — Phase 3 (three cameras through the mux).**
> Each Capture click now grabs one frame from each of the three
> channels (cam0 / 762 nm, cam1 / 766 nm, cam2 / 770 nm) and saves
> them with a shared timestamp so they sort together in the gallery.
> Burst-of-N, timer-mode auto-capture, settings UI, and focus mode
> are still placeholders — Phases 4 and 5.

# Quick start

> **Phase 3 caveat.** All three cameras work, but the filters aren't
> bolted on yet (those mount onto the lens housings, no electronics).
> Pictures all show the same scene with no spectral difference until
> filters are installed. The capture pipeline doesn't care — when you
> bolt the filters on, the science begins automatically.

1. **Power on the Pi.** Plug in the USB-C power cable. Wait about 30
   seconds for it to boot.
2. **Connect your laptop to the same wifi as the Pi.** In the lab that
   means whatever wifi the Pi is configured for. (Field AP-mode lands
   in Phase 5.)
3. **Open your browser** to `http://koenig-pi.local:8000`.
   - If that doesn't resolve, use the Pi's IP address directly:
     `http://<pi-ip>:8000`.
4. **Click Capture.** Three pictures appear in the gallery, one per
   camera (you can tell them apart by the `cam0_762nm`, `cam1_766nm`,
   `cam2_770nm` suffix in each filename). Each click takes ~8 seconds
   because the three cameras share one MIPI lane through the mux and
   must capture in sequence.

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

Click the blue **Capture** button. After about **8 seconds** the
gallery refreshes with three new pictures on top — one per channel,
labeled in the filename as `cam0_762nm`, `cam1_766nm`, and
`cam2_770nm`. The three filenames share the same timestamp prefix so
they always group together when the gallery is sorted by name.

Why it takes 8 seconds: the three cameras share one CSI data lane
through the multiplexer. The Pi has to capture from camera 0, switch
the mux, capture from camera 1, switch again, and so on. There's no
way around the sequential timing without different hardware.

If the page seems to hang past 15 seconds, something's wrong — see
the troubleshooting section.

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

If you click Capture and only get one or two pictures back instead of
three:

- **Most likely: a CSI ribbon is loose** between the multiplexer board
  and one of the cameras. Power off, reseat both ends of each ribbon,
  and try again. CSI ribbons are fragile and connectors don't always
  latch with an obvious click.
- **Second most likely: a ribbon is plugged in upside down.** Contacts
  on the camera end should face the camera PCB; contacts on the mux
  end should face the mux PCB. If you can see metal on the wrong side,
  flip it.
- **If reseating doesn't help:** SSH into the Pi and run
  `rpicam-hello --list-cameras`. You should see all three IMX477
  entries. If one is missing, it's a hardware problem — that camera
  isn't being reached at all.

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
/ 770 nm). All three share the same timestamp in their filenames so the
science team can match them up. Filename pattern is
`YYYYMMDD_HHMMSS_mmm_cam{N}_{wavelength}nm.jpg`.

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
