"""Finding the pointing devices, and granting read access to those only.

The detector needs to see the user still pushing while the pointer is pinned
at a dead edge, and the only faithful source for that is the physical device's
own motion events.  Reading them means read access to an event node, which by
default only `root` and the `input` group have.

Joining `input` is rejected: it grants read access to **every** input device,
the keyboard included, to anything running as the user.  This module builds
the narrow alternative instead.

**Matching by vendor and product is the obvious narrow rule and it is wrong
here.**  `ATTRS{idVendor}` walks up to the USB device, and a keyboard with an
integrated pointing device — a TrackPoint, a trackpad on a keyboard — presents
its keyboard and its pointer as two interfaces of *one* USB device sharing
those ids.  A rule matching them grants the keyboard node too, which is the
exact thing the rule exists to avoid.

Matching by capability does not have that problem, and is also stable when a
mouse is replaced: grant to nodes udev tags as a pointer, never to a node it
tags as a keyboard.  ``tools/probe_evdev_access.py`` checks that claim against
real hardware rather than trusting this paragraph.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from glob import glob
from typing import Iterable, Optional

# struct input_event; the same layout src/wcs/uinput.py writes.
_INPUT_EVENT = struct.Struct("@llHHi")
assert _INPUT_EVENT.size == 24, _INPUT_EVENT.size

EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
REL_X, REL_Y = 0x00, 0x01
ABS_X, ABS_Y = 0x00, 0x01

#: Where the rule goes. systemd tags devices for ``uaccess`` in
#: 70-uaccess.rules and applies the ACL in 73-seat-late.rules, so a rule that
#: adds the tag has to run between the two.
RULE_FILENAME = "71-wayland-cursor-smoother.rules"

#: udev property -> the word this project uses for it.
_ROLES = {
    "ID_INPUT_MOUSE": "mouse",
    "ID_INPUT_POINTINGSTICK": "pointingstick",
    "ID_INPUT_TOUCHPAD": "touchpad",
    "ID_INPUT_TOUCHSCREEN": "touchscreen",
    "ID_INPUT_TABLET": "tablet",
    "ID_INPUT_JOYSTICK": "joystick",
    "ID_INPUT_KEYBOARD": "keyboard",
    "ID_INPUT_KEY": "keys",
}

#: Roles whose motion this project wants to read.
POINTING_ROLES = frozenset({"mouse", "pointingstick", "touchpad"})


def parse_udev_properties(text: str) -> dict[str, str]:
    """Parse ``udevadm info --query=property`` output."""
    properties: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            properties[key.strip()] = value.strip()
    return properties


def classify(properties: dict[str, str]) -> frozenset[str]:
    """The roles udev has assigned to a device."""
    return frozenset(
        role for prop, role in _ROLES.items() if properties.get(prop) == "1"
    )


@dataclass(frozen=True)
class InputDevice:
    path: str
    name: str
    roles: frozenset[str]
    readable: bool

    @property
    def is_pointing(self) -> bool:
        return bool(self.roles & POINTING_ROLES)

    @property
    def is_keyboard(self) -> bool:
        return "keyboard" in self.roles

    @property
    def role_summary(self) -> str:
        return ",".join(sorted(self.roles)) or "-"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    readable_pointing: tuple[str, ...]
    readable_keyboards: tuple[str, ...]
    reason: str


def access_verdict(devices: Iterable[InputDevice]) -> Verdict:
    """Is access shaped the way this project asks for?

    Two conditions, and the second matters more than the first.  Some pointing
    device must be readable or there is nothing to detect with — but *no*
    keyboard may be readable, because that is the whole difference between
    this and joining the `input` group, and a rule that quietly grants it has
    failed even while the feature works.
    """
    devices = list(devices)
    pointing = tuple(d.path for d in devices if d.is_pointing and d.readable)
    keyboards = tuple(d.path for d in devices if d.is_keyboard and d.readable)
    if keyboards:
        return Verdict(False, pointing, keyboards,
                       "a keyboard is readable; the rule is too wide")
    if not pointing:
        return Verdict(False, pointing, keyboards,
                       "no pointing device is readable; the rule is not in effect")
    return Verdict(True, pointing, keyboards,
                   "pointing devices readable, no keyboard readable")


def udev_rule_text() -> str:
    """The rule to install, with its reasoning attached.

    ``uaccess`` grants an ACL to whoever holds the active local session rather
    than to a group, so nothing is granted to a remote or inactive user, and
    the grant follows the seat rather than outliving it.
    """
    return f"""\
# wayland-cursor-smoother: read access to pointing devices, and to nothing else.
#
# Install as /etc/udev/rules.d/{RULE_FILENAME}
#
# Why not the `input` group: that grants read access to every input device,
# the keyboard among them, to anything running as the user. This grants it to
# devices udev has tagged as pointers, and explicitly not to anything it has
# tagged as a keyboard -- which matters because a keyboard with an integrated
# TrackPoint or trackpad shares one USB vendor/product with its pointer, so a
# rule matching those ids would hand over the keystrokes too.
#
# TAG+="uaccess" grants an ACL to the user of the active local session. It is
# not a group, so it does not apply to remote or inactive users, and it is how
# systemd already grants joysticks and cameras.

SUBSYSTEM!="input", GOTO="wcs_end"
KERNEL!="event*", GOTO="wcs_end"

# A node that looks like a keyboard is never granted, whatever else it is.
ENV{{ID_INPUT_KEYBOARD}}=="1", GOTO="wcs_end"

ENV{{ID_INPUT_MOUSE}}=="1", TAG+="uaccess"
ENV{{ID_INPUT_POINTINGSTICK}}=="1", TAG+="uaccess"
ENV{{ID_INPUT_TOUCHPAD}}=="1", TAG+="uaccess"

LABEL="wcs_end"
"""


def install_commands(rule_path: str) -> list[str]:
    return [
        f"sudo install -m 0644 {rule_path} /etc/udev/rules.d/{RULE_FILENAME}",
        "sudo udevadm control --reload",
        "sudo udevadm trigger --subsystem-match=input --action=change",
    ]


def uninstall_commands() -> list[str]:
    return [
        f"sudo rm /etc/udev/rules.d/{RULE_FILENAME}",
        "sudo udevadm control --reload",
        "sudo udevadm trigger --subsystem-match=input --action=change",
    ]


def _device_name(event_node: str) -> str:
    try:
        with open(f"/sys/class/input/{event_node}/device/name") as handle:
            return handle.read().strip()
    except OSError:
        return "<unknown>"


def list_devices(exclude_names: Iterable[str] = ()) -> list[InputDevice]:
    """Every ``/dev/input/event*``, with its roles and whether we can read it.

    ``exclude_names`` drops this project's own virtual pointer.  Reading back
    the events we ourselves emit would feed a redirect straight into the
    detector that asked for it.
    """
    excluded = set(exclude_names)
    devices: list[InputDevice] = []
    have_udevadm = shutil.which("udevadm") is not None
    for path in sorted(glob("/dev/input/event*"), key=_event_number):
        node = os.path.basename(path)
        name = _device_name(node)
        if name in excluded:
            continue
        properties: dict[str, str] = {}
        if have_udevadm:
            proc = subprocess.run(
                ["udevadm", "info", "--query=property", f"--name={path}"],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                properties = parse_udev_properties(proc.stdout)
        devices.append(
            InputDevice(
                path=path,
                name=name,
                roles=classify(properties),
                readable=os.access(path, os.R_OK),
            )
        )
    return devices


def _event_number(path: str) -> tuple[int, str]:
    tail = os.path.basename(path)[len("event"):]
    return (int(tail), path) if tail.isdigit() else (1 << 30, path)


def decode_events(blob: bytes) -> list[tuple[int, int, int]]:
    """``(type, code, value)`` for each complete event in ``blob``."""
    count = len(blob) // _INPUT_EVENT.size
    return [
        _INPUT_EVENT.unpack_from(blob, index * _INPUT_EVENT.size)[2:]
        for index in range(count)
    ]


def motion_magnitude(events: Iterable[tuple[int, int, int]]) -> int:
    """Total pointer motion in a batch, relative or absolute.

    Absolute devices are counted too, deliberately: a head tracker or another
    assistive pointer may report absolute positions rather than deltas, and a
    detector that only understood ``REL_*`` would silently ignore it.
    """
    total = 0
    for etype, code, value in events:
        if etype == EV_REL and code in (REL_X, REL_Y):
            total += abs(value)
        elif etype == EV_ABS and code in (ABS_X, ABS_Y):
            total += 1
    return total
