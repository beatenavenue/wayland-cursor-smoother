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
integrated pointing device, such as a TrackPoint or a trackpad, presents its
keyboard and its pointer as two interfaces of *one* USB device sharing those
ids.  A rule matching them grants the keyboard node too, which is the exact
thing the rule exists to avoid.

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


#: How a node came to be readable. The distinction matters because only one
#: of these is something this project did, and a verdict that blames our rule
#: for a permission somebody else granted sends the reader to fix the wrong
#: thing.
GRANT_WORLD = "world"      # mode bits let everyone read it
GRANT_GROUP = "group"      # we are in the owning group -- e.g. `input`
GRANT_ACL = "acl"          # an ACL names us: uaccess, or a hand-written rule
GRANT_NONE = "none"


def classify_grant(mode: int, gid: int, my_gids: Iterable[int],
                   readable: bool) -> str:
    """Why a node is (or is not) readable, from its mode and our groups.

    Checked in the order the kernel checks them, so a node that is both
    world-readable and covered by an ACL is reported as world-readable: that
    is the permission actually doing the work, and the one worth removing.
    """
    if not readable:
        return GRANT_NONE
    if mode & 0o004:
        return GRANT_WORLD
    if mode & 0o040 and gid in set(my_gids):
        return GRANT_GROUP
    return GRANT_ACL


GRANT_EXPLANATIONS = {
    GRANT_WORLD: "mode bits make it readable by everyone on the system",
    GRANT_GROUP: "we are in the group that owns it",
    GRANT_ACL: "an ACL names us -- uaccess, or a rule that grants directly",
    GRANT_NONE: "not readable",
}


@dataclass(frozen=True)
class InputDevice:
    path: str
    name: str
    roles: frozenset[str]
    readable: bool
    grant: str = GRANT_NONE
    text_key_count: int = 0
    """How many keys this node has that could spell something. `keys` in the
    roles means only that some KEY_* code exists -- a volume button counts --
    so this is what decides whether reading the node could observe typing."""

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
    keyboards_we_granted: tuple[str, ...]
    keyboards_already_open: tuple[str, ...]
    reason: str


def access_verdict(devices: Iterable[InputDevice]) -> Verdict:
    """Is access shaped the way this project asks for?

    Two conditions, and they are separated on purpose.

    Some pointing device must be readable or there is nothing to detect with.
    And no keyboard may be readable *because of us* -- that is the whole
    difference between this and joining the `input` group, and a rule that
    quietly granted one would have failed even while the feature worked.

    A keyboard that was already readable before this project existed is a
    different finding. The first real run met one: a tablet vendor's udev rule
    had set mode 0666 on all of its own nodes, its keyboard interface
    included. Reporting that as "our rule is too wide" would have sent the
    reader to edit a rule that was not responsible. It is still worth saying
    out loud, so it is reported separately rather than folded into the
    verdict.
    """
    devices = list(devices)
    pointing = tuple(d.path for d in devices if d.is_pointing and d.readable)
    keyboards = [d for d in devices if d.is_keyboard and d.readable]
    ours = tuple(d.path for d in keyboards if d.grant == GRANT_ACL)
    already = tuple(d.path for d in keyboards if d.grant != GRANT_ACL)

    if ours:
        return Verdict(False, pointing, ours, already,
                       "a keyboard is readable through an ACL; something granted "
                       "it directly and this rule must not")
    if not pointing:
        return Verdict(False, pointing, ours, already,
                       "no pointing device is readable; the rule is not in effect")
    return Verdict(True, pointing, ours, already,
                   "pointing devices readable, and no keyboard granted by a rule")


def would_grant(devices: Iterable[InputDevice]) -> list[InputDevice]:
    """The nodes the rule matches: a pointer role, and no keyboard tag.

    Mirrors ``udev_rule_text()`` so the two can be read against each other.
    Its point is to be run *before* installing anything: a rule whose effect
    is only visible after it is in place is a rule nobody checks.
    """
    return [d for d in devices if d.is_pointing and not d.is_keyboard]


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


# Key codes that can spell. A node carrying any of these can observe typed
# text; one carrying only volume, play/pause and similar cannot. This is the
# distinction `ID_INPUT_KEY` does not draw -- it is set for any KEY_* code at
# all, so a pointer with a couple of media buttons carries it, and so would a
# keylogger. Ranges from linux/input-event-codes.h.
_TEXT_KEY_RANGES = (
    (2, 13),    # KEY_1 .. KEY_EQUAL
    (16, 27),   # KEY_Q .. KEY_RIGHTBRACE
    (30, 41),   # KEY_A .. KEY_GRAVE
    (44, 53),   # KEY_Z .. KEY_SLASH
    (57, 57),   # KEY_SPACE
)


def parse_capability_bitmask(text: str) -> set[int]:
    """Decode a /sys/class/input/*/device/capabilities/* bitmask.

    The kernel prints it as space-separated hex longs, most significant
    group first, each group covering 64 bits on a 64-bit kernel.
    """
    groups = text.split()
    bits: set[int] = set()
    for index, group in enumerate(reversed(groups)):
        try:
            value = int(group, 16)
        except ValueError:
            continue
        base = index * 64
        while value:
            low = value & -value
            bits.add(base + low.bit_length() - 1)
            value ^= low
    return bits


def text_keys(key_bits: set[int]) -> set[int]:
    """The subset of ``key_bits`` that could spell something."""
    return {
        bit
        for bit in key_bits
        if any(low <= bit <= high for low, high in _TEXT_KEY_RANGES)
    }


def read_key_capabilities(event_node: str) -> set[int]:
    try:
        with open(f"/sys/class/input/{event_node}/device/capabilities/key") as handle:
            return parse_capability_bitmask(handle.read())
    except OSError:
        return set()


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
        readable = os.access(path, os.R_OK)
        try:
            info = os.stat(path)
            grant = classify_grant(info.st_mode, info.st_gid, os.getgroups(), readable)
        except OSError:
            grant = GRANT_ACL if readable else GRANT_NONE
        devices.append(
            InputDevice(
                path=path,
                name=name,
                roles=classify(properties),
                readable=readable,
                grant=grant,
                text_key_count=len(text_keys(read_key_capabilities(node))),
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
