#!/usr/bin/env python3
"""Settle the last link: can a KWin script call into a Python-owned D-Bus name?

Everything else in the read path is confirmed on hardware. A JS KWin script
reads the global pointer position and has `callDBus`; the daemon can read the
physical devices and move the pointer. What has never been tried is the join
between them.

Two things are being measured rather than assumed:

  1. **Does the call arrive at all**, given the daemon owns a name the script
     names literally.
  2. **What types arrive.** `callDBus` marshals JS values without being told
     what they should become, so a JS number could land as an int32, an int64
     or a double. The script sends each reading twice -- once as numbers and
     once as a preformatted string -- and this prints the Python type of every
     argument received, so the finished feed can pick the form that works
     instead of discovering the answer as an unexplained silence.

    python3 tools/probe_dbus_link.py --seconds 15

Move the pointer while it runs.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.kwinscript import (
    BUS_NAME,
    FEED_INTERFACE,
    KWIN_SERVICE,
    OBJECT_PATH,
    SCRIPTING_IFACE,
    SCRIPTING_PATH,
    DBusError,
    find_dbus_caller,
    link_script,
)

PLUGIN = "wcs-link-probe"


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--throttle", type=int, default=30,
                        help="report every Nth pointer motion (default: 30)")
    args = parser.parse_args()

    try:
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except ImportError as exc:
        print(f"Need dbus-python and PyGObject: {exc}")
        print("On Debian: sudo apt install python3-dbus python3-gi")
        return 2

    section("environment")
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    try:
        name = dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        print(f"{BUS_NAME} is already owned. Is the daemon running?")
        return 2
    print(f"owning       : {BUS_NAME}")
    print(f"object       : {OBJECT_PATH}")

    received: list[tuple[float, str, tuple]] = []

    class Feed(dbus.service.Object):
        # No in_signature on purpose: whatever callDBus marshals is accepted
        # and its Python type reported, which is the question being asked.
        @dbus.service.method(FEED_INTERFACE)
        def PositionInts(self, x, y, seq):
            received.append((time.monotonic(), "PositionInts", (x, y, seq)))

        @dbus.service.method(FEED_INTERFACE)
        def PositionString(self, text):
            received.append((time.monotonic(), "PositionString", (text,)))

    Feed(bus, OBJECT_PATH)

    caller = find_dbus_caller()
    if caller is None:
        print("No qdbus/gdbus available to load the script with.")
        return 2
    print(f"d-bus caller : {caller.program}")

    scratch = Path(tempfile.mkdtemp(prefix="wcs-link-"))
    script_path = scratch / "link.js"
    script_path.write_text(link_script(args.throttle))

    section("loading the feed script")
    try:
        caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                    f"{SCRIPTING_IFACE}.unloadScript", [PLUGIN])
    except DBusError:
        pass
    try:
        out = caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                          f"{SCRIPTING_IFACE}.loadScript", [str(script_path), PLUGIN])
        print(f"  loadScript -> {out}")
        caller.call(KWIN_SERVICE, SCRIPTING_PATH, f"{SCRIPTING_IFACE}.start")
        print("  start() called")
    except DBusError as exc:
        print(f"  FAILED: {exc}")
        return 1

    section(f"listening for {args.seconds:g}s")
    print(f"Move the pointer. The script reports every {args.throttle} motions.")
    started = time.monotonic()
    loop = GLib.MainLoop()

    def stop() -> bool:
        loop.quit()
        return False  # one shot

    GLib.timeout_add(int(args.seconds * 1000), stop)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass

    try:
        caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                    f"{SCRIPTING_IFACE}.unloadScript", [PLUGIN])
        print("  script unloaded")
    except DBusError as exc:
        print(f"  unload failed: {exc}")
    shutil.rmtree(scratch, ignore_errors=True)

    section("what arrived")
    by_method: dict[str, list] = {}
    for when, method, payload in received:
        by_method.setdefault(method, []).append((when, payload))

    for method in ("PositionInts", "PositionString"):
        calls = by_method.get(method, [])
        print(f"\n  {method}: {len(calls)} call(s)")
        if not calls:
            print("    nothing arrived -- this form does not survive callDBus")
            continue
        _, first = calls[0]
        print(f"    first payload : {first!r}")
        print(f"    argument types: {[type(v).__name__ for v in first]}")
        if len(calls) > 1:
            _, last = calls[-1]
            print(f"    last payload  : {last!r}")
            gaps = [b[0] - a[0] for a, b in zip(calls, calls[1:])]
            print(f"    call spacing  : min {min(gaps)*1000:.0f}ms  "
                  f"median {sorted(gaps)[len(gaps)//2]*1000:.0f}ms  "
                  f"max {max(gaps)*1000:.0f}ms")

    section("VERDICT")
    ints = len(by_method.get("PositionInts", []))
    text = len(by_method.get("PositionString", []))
    if ints or text:
        print("PASS -- a KWin script can call into a Python-owned D-Bus name.")
        print(f"  typed arguments : {'work' if ints else 'DO NOT arrive'}")
        print(f"  string argument : {'works' if text else 'DOES NOT arrive'}")
        if ints and text:
            print("\nBoth forms work; the feed can send typed values.")
        elif text:
            print("\nOnly the string form works; the feed must preformat.")
        elif ints:
            print("\nOnly the typed form works, which is unexpected but fine.")
        elapsed = time.monotonic() - started
        print(f"\n  {ints + text} call(s) in {elapsed:.1f}s")
        return 0

    print("NOT YET -- nothing arrived.")
    print("The script loaded, so either callDBus could not reach the name, or")
    print("the pointer never moved. Check the compositor's journal:")
    print("  journalctl --user -u plasma-kwin_wayland.service --since '-2 min'")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
