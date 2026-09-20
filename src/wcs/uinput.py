"""A virtual absolute pointer on /dev/uinput.

Why absolute, and why uinput:

* KWin scales an absolute *pointer* event against ``workspace()->geometry()``
  -- the whole layout -- whereas touch and tablet events are resolved against
  one specific output.  So the device must be classified as a pointer, or the
  whole approach collapses to a single display.  ``probe`` in
  ``tools/probe_uinput.py`` is what settles that on real hardware.
* ``PointerInputRedirection::applyEdgeBarrier()`` returns early for absolute
  motion ("edge barriers are counter-productive for absolute motion"), and
  ``updatePosition()`` validates the destination rather than the path.  An
  absolute event therefore lands anywhere inside any output, in one step.
* Unlike XTest, a uinput device is a real evdev device as far as libinput and
  KWin are concerned, so its events are trusted.
* It runs outside the compositor, so a bug here cannot take down the session.

The classification rules this file exists to satisfy come from systemd's
``input_id`` udev builtin: absolute X/Y axes plus ``BTN_MOUSE`` and nothing
else yields ``ID_INPUT_MOUSE``.  Adding ``BTN_TOOL_PEN``/``BTN_STYLUS`` makes
it a tablet, ``INPUT_PROP_DIRECT`` or ``BTN_TOUCH`` makes it a touchscreen,
and ``BTN_TOOL_FINGER`` makes it a touchpad.  Do not add capabilities here
casually; every one of them is a chance to be reclassified onto one display.

ioctl numbers and struct layouts below were generated from
/usr/include/linux/uinput.h on a 6.x kernel rather than recalled, but they are
uapi and stable.
"""

from __future__ import annotations

import fcntl
import os
import struct
import time
from dataclasses import dataclass
from typing import Optional

UINPUT_PATH = "/dev/uinput"

# --- uapi constants -------------------------------------------------------

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0
BTN_LEFT = 0x110  # == BTN_MOUSE
ABS_X = 0x00
ABS_Y = 0x01

BUS_USB = 0x03
BUS_VIRTUAL = 0x06

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503
UI_ABS_SETUP = 0x401C5504
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
UI_SET_PROPBIT = 0x4004556E


def ui_get_sysname(length: int) -> int:
    """_IOC(_IOC_READ, 'U', 44, length)."""
    return (2 << 30) | (length << 16) | (0x55 << 8) | 44


# struct input_event { struct timeval time; __u16 type, code; __s32 value; }
_INPUT_EVENT = struct.Struct("@llHHi")
# struct uinput_setup { struct input_id id; char name[80]; __u32 ff_effects_max; }
_UINPUT_SETUP = struct.Struct("@HHHH80sI")
# struct uinput_abs_setup { __u16 code; struct input_absinfo absinfo; }
_UINPUT_ABS_SETUP = struct.Struct("@H2x6i")

assert _INPUT_EVENT.size == 24, _INPUT_EVENT.size
assert _UINPUT_SETUP.size == 92, _UINPUT_SETUP.size
assert _UINPUT_ABS_SETUP.size == 28, _UINPUT_ABS_SETUP.size

#: Range of the virtual absolute axes.  Deliberately layout-independent:
#: KWin reads the scale target from the live workspace geometry on every
#: event, so a normalised range survives a display being added or moved
#: without the device being recreated.
ABS_MAX = 65535
#: libinput maps a raw value onto ``value * extent / (max - min + 1)``.
_ABS_RANGE = ABS_MAX + 1


class UinputError(RuntimeError):
    pass


@dataclass(frozen=True)
class AxisMapping:
    """Turns a global logical coordinate into a raw absolute axis value.

    ``origin`` is the top-left of the layout bounding box.  KScreen normally
    normalises layouts so this is (0, 0); it is carried explicitly because
    assuming it silently would produce a constant offset that is easy to
    misread as a scaling bug.
    """

    origin_x: int
    origin_y: int
    width: int
    height: int

    def raw(self, x: float, y: float) -> tuple[int, int]:
        return (
            self._axis(x - self.origin_x, self.width),
            self._axis(y - self.origin_y, self.height),
        )

    @staticmethod
    def _axis(offset: float, extent: int) -> int:
        if extent <= 0:
            raise UinputError("layout extent must be positive")
        raw = round(offset * _ABS_RANGE / extent)
        return min(max(raw, 0), ABS_MAX)


class VirtualPointer:
    """A uinput device libinput should classify as an absolute mouse."""

    def __init__(
        self,
        name: str = "wayland-cursor-smoother virtual pointer",
        bustype: int = BUS_VIRTUAL,
        vendor: int = 0x1D6B,  # Linux Foundation
        product: int = 0x0001,
        version: int = 1,
    ) -> None:
        self.name = name
        self._bustype = bustype
        self._vendor = vendor
        self._product = product
        self._version = version
        self._fd: Optional[int] = None
        self.sysname: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> "VirtualPointer":
        try:
            fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError as exc:
            raise UinputError(
                f"{UINPUT_PATH} does not exist. Load the module with "
                "`sudo modprobe uinput` (and add it to /etc/modules-load.d/ "
                "to make that persist)."
            ) from exc
        except PermissionError as exc:
            raise UinputError(
                f"no write access to {UINPUT_PATH}. Either add yourself to the "
                "group that owns it (`ls -l /dev/uinput`, then "
                "`sudo usermod -aG input $USER` and log out and back in), or "
                "install a udev rule such as:\n"
                '  KERNEL=="uinput", GROUP="input", MODE="0660", '
                'OPTIONS+="static_node=uinput"'
            ) from exc
        self._fd = fd
        try:
            self._configure()
            fcntl.ioctl(fd, UI_DEV_CREATE)
            self.sysname = self._read_sysname()
        except Exception:
            self.close()
            raise
        return self

    def _configure(self) -> None:
        fd = self._fd
        assert fd is not None
        # Buttons first: without BTN_MOUSE, udev's input_id sees absolute axes
        # with no button and declines to call this a mouse.
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_LEFT)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        for axis in (ABS_X, ABS_Y):
            # UI_ABS_SETUP enables the axis as well as describing it.
            fcntl.ioctl(
                fd,
                UI_ABS_SETUP,
                _UINPUT_ABS_SETUP.pack(axis, 0, 0, ABS_MAX, 0, 0, 0),
            )
        # No UI_SET_PROPBIT(INPUT_PROP_DIRECT), no BTN_TOOL_PEN, no BTN_TOUCH,
        # no ABS_MT_*: each of those would move this device off the pointer
        # path and onto a single output.
        fcntl.ioctl(
            fd,
            UI_DEV_SETUP,
            _UINPUT_SETUP.pack(
                self._bustype,
                self._vendor,
                self._product,
                self._version,
                self.name.encode("utf-8")[:79],
                0,
            ),
        )

    def _read_sysname(self) -> Optional[str]:
        buf = bytearray(64)
        try:
            fcntl.ioctl(self._fd, ui_get_sysname(len(buf)), buf, True)
        except OSError:
            return None
        return buf.split(b"\0", 1)[0].decode("utf-8", "replace") or None

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "VirtualPointer":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- events ------------------------------------------------------------

    def _emit(self, etype: int, code: int, value: int) -> None:
        if self._fd is None:
            raise UinputError("device is not open")
        os.write(self._fd, _INPUT_EVENT.pack(0, 0, etype, code, value))

    def move_raw(self, raw_x: int, raw_y: int) -> None:
        """Place the pointer using raw axis values.

        The kernel drops an absolute event that repeats the current value, so
        a caller that needs the pointer *put back* where it already is has to
        nudge it first.  Callers that only ever warp to a new place -- which
        is this project's entire use -- need not care.
        """
        self._emit(EV_ABS, ABS_X, raw_x)
        self._emit(EV_ABS, ABS_Y, raw_y)
        self._emit(EV_SYN, SYN_REPORT, 0)

    def move_to(self, mapping: AxisMapping, x: float, y: float) -> tuple[int, int]:
        raw = mapping.raw(x, y)
        self.move_raw(*raw)
        return raw

    def settle(self, seconds: float = 1.5) -> None:
        """Give udev and KWin time to notice a freshly created device."""
        time.sleep(seconds)
