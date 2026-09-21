#!/usr/bin/env python3
"""Settle how to read pointing devices without handing over the keyboard.

The detector has to see that the user is still pushing while the pointer is
pinned at a dead edge, and that means reading a physical device's own motion
events.  Joining the `input` group would do it and is rejected: it grants read
access to every input device, the keyboard included, to anything running as
the user.

This probe checks the narrow alternative on real hardware.  In particular it
checks the case that makes the obvious narrow rule wrong: **a keyboard with an
integrated pointing device.**  A TrackPoint and its keyboard are two interfaces
of one USB device and share a vendor and product id, so a rule matching those
ids would grant the keyboard node too.  Matching what udev calls the device
instead should avoid that — should, which is why this runs rather than
asserts.

    python3 tools/probe_evdev_access.py              # what is readable today
    python3 tools/probe_evdev_access.py --write-rule # emit the rule + commands
    python3 tools/probe_evdev_access.py --watch 20   # which devices reach us

Run the last one after installing the rule, and exercise every pointing device
you own while it runs — mouse, TrackPoint, touchpad — then check each one is
counted and that no keystroke ever is.
"""

from __future__ import annotations

import argparse
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.evdev import (
    GRANT_EXPLANATIONS,
    RULE_FILENAME,
    InputDevice,
    access_verdict,
    decode_events,
    would_grant,
    install_commands,
    list_devices,
    motion_magnitude,
    udev_rule_text,
    uninstall_commands,
)

OWN_DEVICE_NAME = "wayland-cursor-smoother virtual pointer"

#: Where the generated rule lands when no path is given. An absolute path on
#: purpose: the default used to be a bare filename, which wrote into whatever
#: directory the probe was run from -- in practice the git checkout, leaving an
#: untracked file that looks like it might matter. The copy that matters is the
#: one installed under /etc/udev/rules.d; this is only the staging file the
#: install command reads.
DEFAULT_RULE_OUTPUT = Path(tempfile.gettempdir()) / RULE_FILENAME


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def report_devices(devices: list[InputDevice]) -> None:
    section("input devices, and what we may read")
    print(f"  {'node':<10} {'read':<5} {'by':<6} {'txt':<4} "
          f"{'roles':<28} name")
    for d in devices:
        keys = str(d.text_key_count) if d.text_key_count else "-"
        print(f"  {os.path.basename(d.path):<10} {'yes' if d.readable else 'no':<5} "
              f"{(d.grant if d.readable else '-'):<6} {keys:<4} "
              f"{d.role_summary:<28} {d.name}")
    print("\n  by  = what makes it readable: world (mode bits), group, acl")
    print("  txt = keys on that node that could spell something")

    combos = [d for d in devices
              if d.is_keyboard and d.roles & {"mouse", "pointingstick", "touchpad"}]
    if combos:
        print("\n  These nodes carry both a keyboard and a pointer. One node cannot")
        print("  be split, so reading such a device's motion means reading its")
        print("  keystrokes. The rule never grants them:")
        for d in combos:
            print(f"    {d.path}  {d.name}")

    matched = would_grant(devices)
    section("what the rule would grant")
    if not matched:
        print("  Nothing. No node carries a pointer role without a keyboard tag.")
        return
    for d in matched:
        state = "already readable" if d.readable else "NEW"
        keys = f"{d.text_key_count} text key(s)" if d.text_key_count else "no text keys"
        print(f"  {state:<16} {os.path.basename(d.path):<10} {keys:<16} {d.name}")

    risky = [d for d in matched if d.text_key_count]
    if risky:
        print("\n  WARNING: the marked nodes above also carry keys that could spell")
        print("  something. Decide what they are before accepting the rule.")
    else:
        print("\n  None of them carries a key that could spell anything.")


def report_verdict(devices: list[InputDevice]) -> bool:
    verdict = access_verdict(devices)
    section("VERDICT")
    print(f"readable pointing devices        : {len(verdict.readable_pointing)}")
    for path in verdict.readable_pointing:
        print(f"    {path}")
    print(f"keyboards readable via an ACL    : {len(verdict.keyboards_we_granted)}"
          f"   <- must be 0; this is ours")
    for path in verdict.keyboards_we_granted:
        print(f"    {path}")
    print(f"keyboards already open to us     : {len(verdict.keyboards_already_open)}"
          f"   <- pre-existing, not ours")
    for path in verdict.keyboards_already_open:
        print(f"    {path}")
    print()

    if verdict.keyboards_already_open:
        print("Those last ones were readable before this project existed. Nothing")
        print("here granted them and removing this rule will not close them. They")
        print("are worth looking at anyway:")
        for path in verdict.keyboards_already_open:
            print(f"\n  {path}:")
            print(_indent(_run(["getfacl", "-p", path]) or "<getfacl unavailable>", "      "))
        print("\n  `other::rw-` means a udev rule set that node world-readable --")
        print("  usually a vendor rule shipped for a tablet or a gaming device,")
        print("  applied to every interface of the device rather than just the one")
        print("  that needed it. `group::rw-` plus membership means the `input`")
        print("  group; check `id -nG`.")
        print()

    if verdict.ok:
        print(f"PASS -- {verdict.reason}.")
        return True

    print(f"NOT YET -- {verdict.reason}.")
    if not verdict.readable_pointing:
        print("If the rule is not installed yet, that is the expected state:")
        print("run again with --write-rule.")
    return False


def _run(command: list[str]) -> str:
    if shutil.which(command[0]) is None:
        return ""
    proc = subprocess.run(command, capture_output=True, text=True)
    return (proc.stdout or proc.stderr).strip()


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def write_rule(path: Path) -> None:
    path.write_text(udev_rule_text())
    section("the rule")
    print(udev_rule_text())
    section("install it")
    for command in install_commands(str(path)):
        print(f"  {command}")
    print("\nThen re-run this probe. To undo it completely:")
    for command in uninstall_commands():
        print(f"  {command}")
    print(f"\nWritten to: {path}")
    print("This file is only staging for the install command above; the copy")
    print("that takes effect is the one under /etc/udev/rules.d.")


def watch(devices: list[InputDevice], seconds: float) -> None:
    targets = [d for d in devices if d.is_pointing and d.readable]
    section(f"watching {len(targets)} pointing device(s) for {seconds:g}s")
    if not targets:
        print("Nothing readable to watch. Install the rule first.")
        return

    print("Exercise every pointing device you have while this runs -- mouse,")
    print("TrackPoint, touchpad, anything else. Each should appear below with a")
    print("non-zero count. A device that stays at zero cannot drive detection.")
    print()

    handles: dict[int, InputDevice] = {}
    counts: dict[str, int] = {d.path: 0 for d in targets}
    batches: dict[str, int] = {d.path: 0 for d in targets}
    try:
        for device in targets:
            try:
                fd = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                print(f"  cannot open {device.path}: {exc}")
                continue
            handles[fd] = device

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select(list(handles), [], [], 0.2)
            for fd in ready:
                device = handles[fd]
                try:
                    blob = os.read(fd, 24 * 64)
                except OSError:
                    continue
                events = decode_events(blob)
                moved = motion_magnitude(events)
                if moved:
                    counts[device.path] += moved
                    batches[device.path] += 1
            remaining = deadline - time.monotonic()
            print(f"  {remaining:4.1f}s left   " + "  ".join(
                f"{os.path.basename(p)}:{counts[p]}" for p in counts
            ), end="\r", flush=True)
    finally:
        for fd in handles:
            os.close(fd)
    print(" " * 78, end="\r")

    section("what reached us")
    for device in targets:
        total = counts[device.path]
        mark = "moved" if total else "SILENT"
        print(f"  {mark:<7} {device.path:<20} {total:>8} units in "
              f"{batches[device.path]} batches   {device.name}")
    silent = [d for d in targets if not counts[d.path]]
    if silent:
        print("\n  A device is SILENT either because you did not move it, or")
        print("  because its motion does not reach us. Re-run and move only that")
        print("  device to tell the two apart.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-rule", nargs="?", const=str(DEFAULT_RULE_OUTPUT),
                        metavar="PATH",
                        help=f"write the udev rule and print how to install it "
                             f"(default: {DEFAULT_RULE_OUTPUT})")
    parser.add_argument("--watch", type=float, metavar="SECONDS",
                        help="read the readable pointing devices and report which "
                             "ones actually deliver motion")
    args = parser.parse_args()

    devices = list_devices(exclude_names=[OWN_DEVICE_NAME])
    if not devices:
        print("No /dev/input/event* nodes found at all.")
        return 2

    report_devices(devices)
    ok = report_verdict(devices)

    if args.write_rule:
        write_rule(Path(args.write_rule))
    if args.watch:
        watch(devices, args.watch)

    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
