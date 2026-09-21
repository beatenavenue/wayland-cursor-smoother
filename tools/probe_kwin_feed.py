#!/usr/bin/env python3
"""Settle how a KWin script reports the pointer position, and whether it can.

This is the last unverified piece. The detector needs the pointer's global
position; only code inside the compositor has it; a KWin script is that code.
Two questions have no hardware evidence behind them:

  1. **Plain JS or declarative QML?** CLAUDE.md says `Workspace` is a QML
     singleton on KWin 6+ and a JS script therefore cannot read the cursor --
     read from source, never run, and hard to square with the KWin 6 JS
     scripts in the wild that use the `workspace` global. If that note is
     wrong it is very likely what stalled the original attempt, because the
     failure it describes is silent.

  2. **Can a script talk to anything outside the compositor?** A JS script has
     `callDBus`; a declarative one may not. Whichever kind works has to have a
     way out, or it is useless here.

Both variants are loaded and both report through every channel available to
them, so one run answers both questions instead of trading a silence for a
guess.

    python3 tools/probe_kwin_feed.py

Watch for notifications while it runs, and move the mouse when asked.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.kwinscript import (
    JS_SCRIPT,
    KWIN_SERVICE,
    MARKER,
    QML_SCRIPT,
    SCRIPTING_IFACE,
    SCRIPTING_PATH,
    DBusError,
    find_dbus_caller,
)

JS_PLUGIN = "wcs-feed-js"
QML_PLUGIN = "wcs-feed-qml"


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def journal_since(when: datetime) -> str:
    """Whatever the compositor logged since ``when``, from wherever it lands."""
    if shutil.which("journalctl") is None:
        return "<journalctl not installed>"
    stamp = when.strftime("%Y-%m-%d %H:%M:%S")
    attempts = [
        ["journalctl", "--user", "-u", "plasma-kwin_wayland.service",
         "--since", stamp, "--no-pager"],
        ["journalctl", "--user", "_COMM=kwin_wayland", "--since", stamp, "--no-pager"],
        ["journalctl", "--user", "--since", stamp, "--no-pager"],
    ]
    for command in attempts:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=25)
        except subprocess.TimeoutExpired:
            continue
        text = proc.stdout.strip()
        if MARKER in text or "kwin" in text.lower():
            return text
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=8.0,
                        help="how long to leave the scripts loaded (default: 8)")
    parser.add_argument("--keep", action="store_true",
                        help="do not unload the scripts at the end")
    args = parser.parse_args()

    section("environment")
    caller = find_dbus_caller()
    if caller is None:
        print("No D-Bus command line tool found. Install one of qdbus6, qdbus,")
        print("or gdbus (glib2 / qttools) and run again.")
        return 2
    print(f"d-bus caller : {caller.program}")

    try:
        loaded = caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                             f"{SCRIPTING_IFACE}.isScriptLoaded", ["wcs-probe-absent"])
        print(f"KWin scripting reachable (isScriptLoaded -> {loaded})")
    except DBusError as exc:
        print(f"Cannot reach {KWIN_SERVICE}{SCRIPTING_PATH}:\n  {exc}")
        print("\nIs this a KWin session? The probe cannot continue.")
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="wcs-feed-"))
    js_path = scratch / "feed.js"
    qml_path = scratch / "feed.qml"
    js_path.write_text(JS_SCRIPT)
    qml_path.write_text(QML_SCRIPT)
    print(f"scripts      : {scratch}")

    started = datetime.now()
    section("loading both variants")
    for plugin in (JS_PLUGIN, QML_PLUGIN):
        try:
            caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                        f"{SCRIPTING_IFACE}.unloadScript", [plugin])
        except DBusError:
            pass

    results = {}
    for label, method, path, plugin in (
        ("JS", "loadScript", js_path, JS_PLUGIN),
        ("QML", "loadDeclarativeScript", qml_path, QML_PLUGIN),
    ):
        try:
            out = caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                              f"{SCRIPTING_IFACE}.{method}", [str(path), plugin])
            results[label] = f"loaded ({method} -> {out})"
        except DBusError as exc:
            results[label] = f"FAILED to load: {exc}"
        print(f"  {label:<4} {results[label]}")

    try:
        caller.call(KWIN_SERVICE, SCRIPTING_PATH, f"{SCRIPTING_IFACE}.start")
        print("  start() called")
    except DBusError as exc:
        print(f"  start() FAILED: {exc}")

    section(f"running for {args.seconds:g}s")
    print("Move the mouse now. Watch for notifications titled 'wcs feed: JS")
    print("script' and 'wcs feed: QML script' -- which of them appear is half")
    print("the answer, and a coordinate pair in the body is the other half.")
    time.sleep(args.seconds)

    for label, plugin in (("JS", JS_PLUGIN), ("QML", QML_PLUGIN)):
        try:
            state = caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                                f"{SCRIPTING_IFACE}.isScriptLoaded", [plugin])
            print(f"  {label:<4} still loaded: {state}")
        except DBusError as exc:
            print(f"  {label:<4} isScriptLoaded failed: {exc}")

    section("what the compositor logged")
    log = journal_since(started)
    if not log:
        print("Nothing found. The scripting logging category is off by default,")
        print("so a silent journal does NOT mean the script failed -- the")
        print("notifications are the reliable signal here.")
    else:
        hits = [line for line in log.splitlines() if MARKER in line]
        # Deliberately narrow. An earlier version matched "js:" and "qml:",
        # which are the prefixes of every line the scripts print, so it
        # reported all of them as complaints and buried the real signal.
        errors = [line for line in log.splitlines()
                  if MARKER not in line
                  and any(word in line.lower()
                          for word in ("error", "warning", "exception",
                                       "is not defined", "unable", "failed"))]
        if hits:
            print(f"{len(hits)} line(s) from the scripts:")
            for line in hits:
                print(f"  {line}")
        else:
            print("No marker lines (the debug category is probably off).")
        if errors:
            print(f"\n{len(errors)} line(s) that look like complaints:")
            for line in errors[:40]:
                print(f"  {line}")

    if not args.keep:
        section("cleaning up")
        for label, plugin in (("JS", JS_PLUGIN), ("QML", QML_PLUGIN)):
            try:
                caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                            f"{SCRIPTING_IFACE}.unloadScript", [plugin])
                print(f"  {label:<4} unloaded")
            except DBusError as exc:
                print(f"  {label:<4} unload failed: {exc}")
        shutil.rmtree(scratch, ignore_errors=True)

    section("what to report back")
    print("1. Which notification titles appeared, and what coordinates they")
    print("   showed. 'JS script' appearing at all disproves the note that a")
    print("   plain JS script cannot read the cursor.")
    print("2. Whether the coordinates changed as you moved the mouse, or only")
    print("   showed one position.")
    print("3. Anything above under 'what the compositor logged'.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
