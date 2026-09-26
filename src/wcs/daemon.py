"""The daemon: the only place the verified pieces touch each other.

Everything it joins has been confirmed on hardware separately. What lives here
is the wiring, and the two hazards that only appear once the pieces are joined.

**Do not read back our own device.** The virtual pointer's events are indexed
alongside every other pointer, so a detector that reads all of them would feed
each redirect straight into the accumulator that asked for it. The device is
excluded by name.

**A redirect must not be mistaken for a push.** While the pointer is being
moved the physical device is usually still moving too, and the feed will report
leaving the band partway through. The detector's cooldown covers the window;
nothing else needs to.

It runs outside the compositor, so a failure here cannot take down the
session -- the constraint the whole architecture was chosen to satisfy.
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from .config import WARP, Config
from .detect import PushDetector, UndoLatch
from .evdev import (
    ABS_X,
    ABS_Y,
    EV_ABS,
    EV_KEY,
    EV_REL,
    REL_X,
    REL_Y,
    InputDevice,
    decode_events,
    list_devices,
)
from .feed import WatchBand, feed_script, watch_bands
from .geometry import Point, redirect_target
from .kwinscript import (
    BUS_NAME,
    FEED_INTERFACE,
    KWIN_SERVICE,
    OBJECT_PATH,
    SCRIPTING_IFACE,
    SCRIPTING_PATH,
    DBusError,
    find_dbus_caller,
)
from .layout import detect_layout
from .motion import glide, max_legible_duration, redirect_path
from .uinput import AxisMapping, VirtualPointer

PLUGIN_NAME = "wayland-cursor-smoother"
#: Printed by the feed script on load. Distinctive enough to grep the
#: compositor's journal for, which is the first question when nothing
#: arrives: did the script load at all?
FEED_MARKER = "wcs-feed"
DEVICE_NAME = "wayland-cursor-smoother virtual pointer"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def percent(value: float) -> str:
    """A facing ratio for a person to compare against ``min_facing``."""
    return f"{round(value, 1):g}%"


class Daemon:
    def __init__(self, config: Config, *, verbose: bool = False) -> None:
        self.config = config
        self.verbose = verbose
        self.layout = None
        self.bands: list[WatchBand] = []
        self.pointer: Optional[VirtualPointer] = None
        self.mapping: Optional[AxisMapping] = None
        self.detector = PushDetector(
            threshold=config.detect.threshold,
            window=config.detect.window,
            cooldown=config.detect.cooldown,
        )
        #: Off by default, and armed after a warp only. See UndoLatch for why
        #: on both counts.
        self._undo = (
            UndoLatch(threshold=config.detect.threshold,
                      window=config.detect.window,
                      lifetime=config.detect.undo_window)
            if config.detect.undo_window and config.redirect.style == WARP
            else None
        )
        self._armed: Optional[WatchBand] = None
        self._position = Point(0.0, 0.0)
        self._devices: dict[int, InputDevice] = {}
        self._absolute: dict[int, tuple[Optional[int], Optional[int]]] = {}
        self._script_path: Optional[Path] = None
        self._caller = find_dbus_caller()
        self._redirects = 0
        self._undos = 0
        self._edges = 0
        self._bus_name = None  # must outlive the loop; see run()
        self._feed_object = None

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> int:
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
        except ImportError as exc:
            log(f"need python3-dbus and python3-gi: {exc}")
            return 2

        if self._caller is None:
            log("no qdbus or gdbus found; cannot load the feed script")
            return 2

        DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        try:
            # The reference must be kept. dbus.service.BusName releases the
            # name in __del__, so a bare call acquires it and hands it back
            # the moment the temporary is collected -- after which callDBus
            # reaches a name nobody owns, the bus answers with an error, and
            # KWin discards that error because the call is fire-and-forget.
            # The result is total silence, which is how this was found.
            self._bus_name = dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)
        except dbus.exceptions.NameExistsException:
            log(f"{BUS_NAME} is already owned; is another copy running?")
            return 2

        daemon = self

        class Feed(dbus.service.Object):
            # No in_signature, deliberately. The link probe that proved this
            # path works declared none and accepted whatever callDBus
            # marshalled. Declaring one here was a deviation from the only
            # configuration ever tested, and a signature the caller does not
            # match is refused with an error reply that callDBus discards --
            # which looks exactly like nothing happening at all.
            @dbus.service.method(FEED_INTERFACE)
            def Edge(self, index, x, y):
                daemon._on_edge(int(index), float(x), float(y))

            @dbus.service.method(FEED_INTERFACE, in_signature="", out_signature="s")
            def Reload(self):
                return daemon.reload()

        self._feed_object = Feed(bus, OBJECT_PATH)
        log(f"owning {BUS_NAME}")

        self.pointer = VirtualPointer(name=DEVICE_NAME).open()
        self.pointer.settle(1.0)
        log(f"virtual pointer created ({self.pointer.sysname})")

        self.reload()
        self._open_devices(GLib)

        loop = GLib.MainLoop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, lambda: (loop.quit(), False)[1])
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGHUP,
                             lambda: (log(self.reload()), True)[1])

        if self._undo is not None:
            log(f"undo armed after each warp for {self._undo.lifetime:g}s; "
                f"push {self._undo.threshold:g} back to take one")
        elif self.config.detect.undo_window and self.config.redirect.style != WARP:
            log("undo not applicable: it takes a warp back, and style is "
                f"{self.config.redirect.style}")
        log("running; push against a dead edge to redirect")
        try:
            loop.run()
        finally:
            self.stop()
        return 0

    def stop(self) -> None:
        log(f"stopping after {self._edges} edge report(s), {self._redirects} "
            f"redirect(s) and {self._undos} undo(s)")
        self._unload_script()
        for fd in list(self._devices):
            try:
                os.close(fd)
            except OSError:
                pass
        self._devices.clear()
        if self.pointer is not None:
            self.pointer.close()
            self.pointer = None

    # -- layout ------------------------------------------------------------

    def reload(self) -> str:
        """Re-read the layout, recompute the strips, reload the feed script."""
        self.layout = detect_layout()
        box = self.layout.bounding_box
        self.mapping = AxisMapping(box.x, box.y, box.width, box.height)
        self.bands = watch_bands(
            self.layout,
            inset=self.config.redirect.inset,
            max_slide=self.config.redirect.max_slide,
            min_facing=self.config.redirect.min_facing,
        )
        self.detector.disarm(time.monotonic())
        self._armed = None
        self._load_script()
        summary = (f"layout {box.width}x{box.height} at ({box.x},{box.y}), "
                   f"{len(self.bands)} band(s) watched")
        for band in self.bands:
            log(f"  band {band.index}: {band.output} push {band.direction.value} "
                f"{band.rect.width}x{band.rect.height} at "
                f"({band.rect.x},{band.rect.y}) -> {band.to_output}, "
                f"facing {percent(band.facing)}")
        return summary

    def _load_script(self) -> None:
        self._unload_script()
        text = feed_script(self.bands, bus_name=BUS_NAME, object_path=OBJECT_PATH,
                           interface=FEED_INTERFACE, marker=FEED_MARKER)
        handle, name = tempfile.mkstemp(prefix="wcs-feed-", suffix=".js")
        with os.fdopen(handle, "w") as out:
            out.write(text)
        self._script_path = Path(name)
        try:
            self._caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                              f"{SCRIPTING_IFACE}.loadScript",
                              [str(self._script_path), PLUGIN_NAME])
            self._caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                              f"{SCRIPTING_IFACE}.start")
        except DBusError as exc:
            log(f"could not load the feed script: {exc}")

    def _unload_script(self) -> None:
        if self._caller is not None:
            try:
                self._caller.call(KWIN_SERVICE, SCRIPTING_PATH,
                                  f"{SCRIPTING_IFACE}.unloadScript", [PLUGIN_NAME])
            except DBusError:
                pass
        if self._script_path is not None:
            self._script_path.unlink(missing_ok=True)
            self._script_path = None

    def _take_back(self, source: Point) -> None:
        """Put the pointer back where the warp took it from.

        Always a warp, whatever else is configured: the latch is only armed
        after one, and animating the way back would describe a journey the
        pointer did not make on the way out either.
        """
        self.pointer.move_to(self.mapping, source.x, source.y)
        self._undos += 1
        log(f"undo {self._undos}: back to ({source.x:.0f},{source.y:.0f})")

    # -- devices -----------------------------------------------------------

    def _open_devices(self, GLib) -> None:
        candidates = [d for d in list_devices(exclude_names=[DEVICE_NAME])
                      if d.is_pointing and d.readable]
        if not candidates:
            log("no readable pointing device; see tools/probe_evdev_access.py")
        for device in candidates:
            try:
                fd = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                log(f"cannot open {device.path}: {exc}")
                continue
            self._devices[fd] = device
            self._absolute[fd] = (None, None)
            GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, fd, GLib.IOCondition.IN,
                                  self._on_readable, None)
            log(f"watching {device.path}  {device.name}")

    def _on_readable(self, fd, condition, _data) -> bool:
        try:
            blob = os.read(fd, 24 * 128)
        except BlockingIOError:
            return True
        except OSError as exc:
            log(f"{self._devices[fd].path} went away: {exc}")
            return False

        dx = dy = 0.0
        pressed = False
        last_x, last_y = self._absolute[fd]
        for etype, code, value in decode_events(blob):
            if etype == EV_KEY and value == 1:
                # A button press means the user meant to be where they are.
                # Undoing after a click would take back a position they have
                # already acted on.
                pressed = True
            if etype == EV_REL:
                if code == REL_X:
                    dx += value
                elif code == REL_Y:
                    dy += value
            elif etype == EV_ABS:
                # An absolute pointer reports where it is, not how far it
                # moved. Differencing keeps such a device -- a head tracker,
                # say -- able to drive a push like any other.
                if code == ABS_X:
                    if last_x is not None:
                        dx += value - last_x
                    last_x = value
                elif code == ABS_Y:
                    if last_y is not None:
                        dy += value - last_y
                    last_y = value
        self._absolute[fd] = (last_x, last_y)

        if pressed and self._undo is not None:
            self._undo.disarm()

        if dx or dy:
            now = time.monotonic()
            if self.detector.motion(dx, dy, now):
                self._fire()
            elif self._undo is not None:
                source = self._undo.motion(dx, dy, now)
                if source is not None:
                    self._take_back(source)
        return True

    # -- the feed ----------------------------------------------------------

    def _on_edge(self, index: int, x: float, y: float) -> None:
        self._edges += 1
        if self.verbose and self._edges == 1:
            log(f"first Edge received: band={index} at ({x:.0f},{y:.0f})")
        self._position = Point(x, y)
        now = time.monotonic()
        if index < 0 or index >= len(self.bands):
            if self._armed is not None:
                self.detector.disarm(now)
                self._armed = None
                if self.verbose:
                    log("left the edge")
            return
        band = self.bands[index]
        if band is not self._armed:
            self._armed = band
            if self.verbose:
                log(f"armed at band {band.index} "
                    f"({band.output} push {band.direction.value})")
        self.detector.arm(band.direction, now)

    # -- the redirect ------------------------------------------------------

    def _fire(self) -> None:
        band = self._armed
        if band is None or self.layout is None:
            return
        redirect = redirect_target(
            self.layout, self._position, band.direction,
            inset=self.config.redirect.inset,
            max_slide=self.config.redirect.max_slide,
            min_facing=self.config.redirect.min_facing,
        )
        if redirect is None:
            if self.verbose:
                log(f"push completed at {self._position} but nothing to aim at")
            return

        style = self.config.redirect.style
        target = redirect.target
        if style == WARP or self.config.redirect.duration <= 0:
            self.pointer.move_to(self.mapping, target.x, target.y)
        else:
            path = redirect_path(self._position, target, band.direction)
            total = redirect.slide + redirect.gap
            glide(
                lambda p: self.pointer.move_to(self.mapping, p.x, p.y),
                path,
                min(self.config.redirect.duration, max_legible_duration(total)),
                rate=self.config.redirect.rate,
                keep=self.layout.covers,
            )

        if self._undo is not None:
            self._undo.arm(self._position, band.direction, time.monotonic())

        self._redirects += 1
        log(f"redirect {self._redirects}: "
            f"({self._position.x:.0f},{self._position.y:.0f}) -> "
            f"({target.x:.0f},{target.y:.0f}) on {redirect.to_output}  "
            f"slid {redirect.slide:.0f}px")
