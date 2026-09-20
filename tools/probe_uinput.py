#!/usr/bin/env python3
"""Settle, in one run, whether this project is possible on this machine.

Everything in CLAUDE.md's research hangs on one question that cannot be
answered by reading source: does libinput classify a uinput device with
absolute axes and a mouse button as a *pointer*?  If it does, an absolute
event addresses the whole layout and the pointer can be placed anywhere.  If
it is classified as a tablet or a touchscreen instead, KWin binds it to a
single output and the approach collapses.

So this script does four things and prints one verdict:

  1. reports the environment (Plasma version, session type, uinput access);
  2. reports the display layout and the dead bands computed from it, which
     should match the bands marked in img/motivation.png;
  3. creates the virtual pointer and reports how udev and libinput classified
     it -- this is the gating check;
  4. optionally demonstrates the feature: for each dead band it puts the
     pointer at the band, then warps it to where the redirect would land, so
     the landing semantics can be judged by eye before any detection logic
     exists.

Run it from a graphical KDE/Wayland session and paste the whole output.

    python3 tools/probe_uinput.py            # full run, moves the pointer
    python3 tools/probe_uinput.py --no-move  # report only, never moves it
    python3 tools/probe_uinput.py --layout-only   # no uinput device at all
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.geometry import Direction, Layout, Point, dead_bands, redirect_target
from wcs.layout import LayoutError, detect_layout, parse_spec
from wcs.uinput import BUS_USB, BUS_VIRTUAL, AxisMapping, UinputError, VirtualPointer

TAGS = ("ID_INPUT", "ID_INPUT_MOUSE", "ID_INPUT_TOUCHPAD", "ID_INPUT_TOUCHSCREEN",
        "ID_INPUT_TABLET", "ID_INPUT_TABLET_PAD", "ID_INPUT_JOYSTICK", "ID_INPUT_KEY",
        "ID_INPUT_WIDTH_MM")
DISQUALIFYING = ("ID_INPUT_TOUCHSCREEN", "ID_INPUT_TABLET", "ID_INPUT_TOUCHPAD",
                 "ID_INPUT_JOYSTICK")


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def run(cmd: list[str], timeout: float = 10.0) -> str:
    if shutil.which(cmd[0]) is None:
        return f"<{cmd[0]} not installed>"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"<{cmd[0]} timed out>"
    out = (proc.stdout + proc.stderr).strip()
    return out or f"<{cmd[0]} printed nothing, exit {proc.returncode}>"


# --- 1. environment -------------------------------------------------------


def report_environment() -> None:
    section("environment")
    print(f"kernel            : {os.uname().release}")
    print(f"XDG_SESSION_TYPE  : {os.environ.get('XDG_SESSION_TYPE', '<unset>')}")
    print(f"XDG_CURRENT_DESKTOP: {os.environ.get('XDG_CURRENT_DESKTOP', '<unset>')}")
    print(f"WAYLAND_DISPLAY   : {os.environ.get('WAYLAND_DISPLAY', '<unset>')}")
    print(f"plasmashell       : {run(['plasmashell', '--version'])}")
    print(f"kwin_wayland      : {run(['kwin_wayland', '--version'])}")
    print(f"groups            : {run(['id', '-nG'])}")
    try:
        st = os.stat("/dev/uinput")
        print(f"/dev/uinput       : mode {oct(st.st_mode & 0o777)} gid {st.st_gid} "
              f"writable={os.access('/dev/uinput', os.W_OK)}")
    except FileNotFoundError:
        print("/dev/uinput       : MISSING (try `sudo modprobe uinput`)")

    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        print("\nNOTE: this is not a Wayland session. The classification check below is\n"
              "      still meaningful, but nothing about KWin's handling of the device\n"
              "      is being tested.")


# --- 2. layout ------------------------------------------------------------


def report_layout(layout: Layout) -> None:
    section("layout (logical coordinates, as KWin sees them)")
    for o in layout.outputs:
        r = o.rect
        print(f"  {o.name:<12} {r.width}x{r.height} at ({r.x},{r.y})  "
              f"x {r.left}..{r.max_x}  y {r.top}..{r.max_y}")
    bb = layout.bounding_box
    print(f"  bounding box : {bb.width}x{bb.height} at ({bb.x},{bb.y})")
    if (bb.x, bb.y) != (0, 0):
        print("  NOTE: the bounding box does not start at (0,0). KWin scales absolute\n"
              "        events against the workspace *size*, so this origin is the one\n"
              "        assumption in the mapping that the warp test below can falsify:\n"
              "        if every landing is off by exactly this offset, that is why.")


def report_bands(layout: Layout) -> list[tuple]:
    section("dead bands (stretches of edge with no display behind them)")
    interesting = []
    for direction in Direction:
        for band in dead_bands(layout, direction, min_length=2):
            source = band_source_point(layout, band)
            r = redirect_target(layout, source, direction)
            arrow = (f"-> {r.to_output} at ({r.target.x:.0f},{r.target.y:.0f})  "
                     f"slide {r.slide:.0f}px, jump {r.gap:.0f}px" if r else "-> nothing beyond; left alone")
            print(f"  {band.output:<12} push {direction.value:<5} "
                  f"{'y' if direction.horizontal else 'x'} {band.start}..{band.end - 1} "
                  f"({band.length}px)  {arrow}")
            if r is not None:
                interesting.append((band, direction, source, r))
    if not interesting:
        print("\n  No band has anywhere to redirect to. Either the layout is already a\n"
              "  clean rectangle, or the geometry read above is wrong.")
    else:
        print("\n  Compare the list above with img/motivation.png. It should name the\n"
              "  same stretches that are marked there, and nothing else.")
    return interesting


def band_source_point(layout: Layout, band) -> Point:
    rect = next(o.rect for o in layout.outputs if o.name == band.output)
    mid = band.midpoint
    if band.direction is Direction.LEFT:
        return Point(rect.left, mid)
    if band.direction is Direction.RIGHT:
        return Point(rect.max_x, mid)
    if band.direction is Direction.UP:
        return Point(mid, rect.top)
    return Point(mid, rect.max_y)


# --- 3. classification ----------------------------------------------------


def udev_properties(syspath: str) -> dict[str, str]:
    out = run(["udevadm", "info", "--query=property", f"--path={syspath}"])
    props = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


def report_classification(pointer: VirtualPointer) -> bool:
    section("classification (THE gating check)")
    print(f"device name : {pointer.name}")
    print(f"sysname     : {pointer.sysname or '<UI_GET_SYSNAME unsupported>'}")

    if not pointer.sysname:
        print("Cannot locate the device in sysfs; classification cannot be read.")
        return False

    syspath = f"/sys/devices/virtual/input/{pointer.sysname}"
    event_nodes = [os.path.basename(p) for p in glob.glob(f"{syspath}/event*")]
    print(f"event node  : {', '.join(event_nodes) or '<none yet>'}")

    target = f"{syspath}/{event_nodes[0]}" if event_nodes else syspath
    props = udev_properties(target)
    if not props:
        print("udevadm returned nothing; is systemd-udevd running?")
        return False

    print("udev tags   :")
    for tag in TAGS:
        if tag in props:
            print(f"    {tag}={props[tag]}")

    if shutil.which("libinput"):
        section("libinput list-devices (our device only)")
        text = run(["libinput", "list-devices"], timeout=20)
        block = [b for b in text.split("\n\n") if pointer.name in b]
        print("\n".join(block) if block
              else "Our device is not listed. libinput needs read access to\n"
                   "/dev/input/event*; try `sudo libinput list-devices` if this is\n"
                   "the only thing missing.")
    else:
        print("\n(libinput is not installed; udev tags above are the whole verdict. "
              "It ships in libinput-utils / libinput-tools.)")

    is_mouse = props.get("ID_INPUT_MOUSE") == "1"
    wrong = [t for t in DISQUALIFYING if props.get(t) == "1"]

    section("VERDICT")
    if is_mouse and not wrong:
        print("PASS -- classified as a pointer (ID_INPUT_MOUSE=1, nothing else).")
        print("Absolute events from this device are scaled against the whole")
        print("workspace, so the pointer can be placed on any display.")
        return True
    if wrong:
        print(f"FAIL -- also classified as {', '.join(wrong)}.")
        print("KWin binds such a device to a single output. The capability set in")
        print("src/wcs/uinput.py has to change until this tag goes away.")
    else:
        print("FAIL -- not classified as a pointer (no ID_INPUT_MOUSE).")
        print("Try `--bus usb`: some quirk databases treat BUS_VIRTUAL differently.")
    return False


# --- 4. the feature, demonstrated ----------------------------------------


def demonstrate(pointer: VirtualPointer, layout: Layout, interesting, dwell: float) -> None:
    bb = layout.bounding_box
    mapping = AxisMapping(bb.x, bb.y, bb.width, bb.height)

    section("reach test: can absolute motion address every display?")
    print("Watch the pointer. It should visit the centre of each display in turn.")
    countdown(3)
    for o in layout.outputs:
        cx = o.rect.x + o.rect.width // 2
        cy = o.rect.y + o.rect.height // 2
        raw = pointer.move_to(mapping, cx, cy)
        print(f"  -> centre of {o.name}: ({cx},{cy})  raw {raw}")
        time.sleep(dwell)

    if not interesting:
        return

    section("landing test: is this the behaviour the project wants?")
    print("For each dead band the pointer is put at the band, then moved to where")
    print("the redirect would land. Judge two things by eye:")
    print("  * does it arrive at the NEAREST point along that edge (a slide)?")
    print("  * does it ever arrive at the CENTRE of a display? That is a failure,")
    print("    not a partial success.")
    countdown(3)
    for band, direction, source, r in interesting:
        print(f"\n  {band.output} push {direction.value}, band "
              f"{band.start}..{band.end - 1}")
        pointer.move_to(mapping, source.x, source.y)
        print(f"    at the dead edge  ({source.x:.0f},{source.y:.0f})")
        time.sleep(max(dwell, 1.0))
        pointer.move_to(mapping, r.target.x, r.target.y)
        print(f"    redirected to     ({r.target.x:.0f},{r.target.y:.0f}) "
              f"on {r.to_output}  [slide {r.slide:.0f}px]")
        time.sleep(max(dwell, 1.0))


def countdown(seconds: int) -> None:
    for remaining in range(seconds, 0, -1):
        print(f"  starting in {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 30, end="\r")


# --- main -----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout-only", action="store_true",
                        help="report the layout and dead bands; create no device")
    parser.add_argument("--no-move", action="store_true",
                        help="create the device and classify it, but never move the pointer")
    parser.add_argument("--bus", choices=("virtual", "usb"), default="virtual",
                        help="bustype to declare (default: virtual)")
    parser.add_argument("--name", default="wayland-cursor-smoother virtual pointer")
    parser.add_argument("--dwell", type=float, default=1.2,
                        help="seconds to pause at each position (default: 1.2)")
    parser.add_argument("--layout", metavar="SPEC",
                        help="override the detected layout, e.g. "
                             "'left:0,400,1920x1080;mid:1920,0,3840x2160'")
    args = parser.parse_args()

    report_environment()

    try:
        layout = parse_spec(args.layout) if args.layout else detect_layout()
    except LayoutError as exc:
        section("layout")
        print(f"Could not read the layout: {exc}")
        print("Pass --layout to supply it by hand, or --layout-only to stop here.")
        return 2

    if not args.layout:
        section("kscreen-doctor -o (verbatim)")
        print(run(["kscreen-doctor", "-o"]))

    report_layout(layout)
    interesting = report_bands(layout)

    if args.layout_only:
        print("\n--layout-only: stopping before the device is created.")
        return 0

    try:
        pointer = VirtualPointer(name=args.name,
                                 bustype=BUS_USB if args.bus == "usb" else BUS_VIRTUAL).open()
    except UinputError as exc:
        section("VERDICT")
        print(f"FAIL -- the device could not be created:\n\n{exc}")
        return 1

    try:
        pointer.settle()
        passed = report_classification(pointer)
        if passed and not args.no_move:
            demonstrate(pointer, layout, interesting, args.dwell)
        elif passed:
            print("\n--no-move: the pointer was not touched.")
    finally:
        pointer.close()

    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
