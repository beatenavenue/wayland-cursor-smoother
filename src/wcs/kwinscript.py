"""Loading a KWin script, and getting something back out of it.

This is the read half: the detector needs the pointer's *global* position, and
a Wayland client cannot have it. A KWin script can, because it runs inside the
compositor.

Two things about it are unverified and are what `tools/probe_kwin_feed.py`
exists to settle.

**Which kind of script.** CLAUDE.md records that `Workspace` is available only
as a QML singleton on KWin 6+, so a script must be declarative rather than
plain JS, and that getting this wrong looks like "the cursor position cannot
be read at all" with no error. That was read from source and never run, and it
sits uneasily beside the many KWin 6 JS scripts in the wild that use the
`workspace` global. Both forms are therefore generated and both are loaded.

**How anything gets out.** A JS script has `callDBus`, which is the channel
the finished design needs anyway. A declarative one may not. Both forms also
`print`, which reaches the compositor's journal if the logging category is
enabled -- and KWin logs script *errors* as warnings, which are enabled by
default, so a failure to load is visible even when a successful read is not.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Sequence

KWIN_SERVICE = "org.kde.KWin"
SCRIPTING_PATH = "/Scripting"
SCRIPTING_IFACE = "org.kde.kwin.Scripting"

#: Printed by both script variants. Distinctive enough to grep a journal for.
MARKER = "WCS-FEED-PROBE"


class DBusError(RuntimeError):
    pass


@dataclass(frozen=True)
class DBusCaller:
    """Whichever D-Bus command line tool this machine actually has."""

    program: str

    def call(self, service: str, path: str, method: str,
             args: Sequence[str] = ()) -> str:
        if self.program.startswith("qdbus"):
            command = [self.program, service, path, method, *args]
        else:
            interface, _, member = method.rpartition(".")
            command = [
                self.program, "call", "--session",
                "--dest", service, "--object-path", path,
                "--method", f"{interface}.{member}",
                *[_gvariant(a) for a in args],
            ]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            raise DBusError((proc.stderr or proc.stdout).strip() or
                            f"{self.program} exited {proc.returncode}")
        return proc.stdout.strip()


def _gvariant(value: str) -> str:
    """gdbus wants GVariant text; everything passed here is a string."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def find_dbus_caller() -> Optional[DBusCaller]:
    for program in ("qdbus6", "qdbus", "qdbus-qt6", "gdbus"):
        if shutil.which(program):
            return DBusCaller(program)
    return None


JS_SCRIPT = f"""\
// wayland-cursor-smoother: does a plain JS KWin script see the pointer?
//
// Reports through two channels on purpose. print() reaches the compositor's
// journal only if the scripting logging category is enabled; callDBus reaches
// a notification the user can see, and is the channel the finished feed would
// use, so proving it works here proves the whole read path.

function notify(title, body) {{
    callDBus("org.freedesktop.Notifications",
             "/org/freedesktop/Notifications",
             "org.freedesktop.Notifications",
             "Notify",
             "wcs-probe", 0, "",
             title, body, [], {{}}, 8000);
}}

function report(label) {{
    var where = "unavailable";
    try {{
        var p = workspace.cursorPos;
        where = p.x + "," + p.y;
    }} catch (e) {{
        where = "ERROR " + e;
    }}
    print("{MARKER} js " + label + " " + where);
    notify("wcs feed: JS script", label + " -- cursorPos = " + where);
}}

report("on load");

var seen = 0;
function onMoved() {{
    seen += 1;
    if (seen === 1 || seen === 30) {{
        report("after " + seen + " move(s)");
    }}
}}

try {{
    workspace.cursorPosChanged.connect(onMoved);
    print("{MARKER} js connected to cursorPosChanged");
}} catch (e) {{
    print("{MARKER} js cannot connect: " + e);
    notify("wcs feed: JS script", "cursorPosChanged unavailable: " + e);
}}
"""

QML_SCRIPT = f"""\
// wayland-cursor-smoother: does a declarative KWin script see the pointer?
//
// The Workspace singleton is the form CLAUDE.md says is required on KWin 6+.
// callDBus may or may not be in scope here -- whether it is decides whether a
// declarative feed could talk to anything outside the compositor, so it is
// probed rather than assumed.

import QtQuick
import org.kde.kwin

Item {{
    function report(label) {{
        var where = "unavailable";
        try {{
            var p = Workspace.cursorPos;
            where = p.x + "," + p.y;
        }} catch (e) {{
            where = "ERROR " + e;
        }}
        console.log("{MARKER} qml " + label + " " + where);
        try {{
            callDBus("org.freedesktop.Notifications",
                     "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications",
                     "Notify",
                     "wcs-probe", 0, "",
                     "wcs feed: QML script",
                     label + " -- cursorPos = " + where, [], {{}}, 8000);
            console.log("{MARKER} qml callDBus available");
        }} catch (e) {{
            console.log("{MARKER} qml callDBus unavailable: " + e);
        }}
    }}

    Component.onCompleted: {{
        report("on load");
        try {{
            Workspace.cursorPosChanged.connect(function() {{ }});
            console.log("{MARKER} qml connected to cursorPosChanged");
        }} catch (e) {{
            console.log("{MARKER} qml cannot connect: " + e);
        }}
    }}
}}
"""


#: The daemon's D-Bus identity. Reverse-DNS of somewhere the project actually
#: lives, rather than borrowing `org.kde.*`, which is not ours to take.
BUS_NAME = "io.github.beatenavenue.WaylandCursorSmoother"
OBJECT_PATH = "/io/github/beatenavenue/WaylandCursorSmoother"
FEED_INTERFACE = "io.github.beatenavenue.WaylandCursorSmoother.Feed"


def link_script(throttle: int = 30) -> str:
    """A KWin script that calls the daemon on every ``throttle``-th motion.

    Two methods on purpose. ``callDBus`` marshals JS values into D-Bus types
    without the script saying which, so whether a JS number arrives as
    something the daemon's signature accepts is a question about the bridge,
    not about our code. Sending the same reading as a preformatted string as
    well means one run says which of the two actually lands -- and if only the
    string does, the production feed uses strings and nothing is lost.

    Throttling matters as much as the call. ``cursorPosChanged`` fires on
    every motion; calling D-Bus that often would be thousands of round trips
    a second. The finished feed will call out on a state *change* rather than
    on a sample, but the probe needs a steady stream to measure, so it takes
    every Nth.
    """
    return (
        "// wayland-cursor-smoother: can a KWin script reach the daemon?\n"
        "\n"
        "var seen = 0;\n"
        "var sent = 0;\n"
        "\n"
        "function deliver(x, y) {\n"
        "    sent += 1;\n"
        "    // Typed: two integers, as the finished feed would prefer.\n"
        "    callDBus(SERVICE, OBJECT, IFACE, 'PositionInts', x, y, sent);\n"
        "    // Untyped: the same reading, immune to marshalling surprises.\n"
        "    callDBus(SERVICE, OBJECT, IFACE, 'PositionString',\n"
        "             x + ',' + y + ',' + sent);\n"
        "}\n"
        "\n"
        "function onMoved() {\n"
        "    seen += 1;\n"
        "    if (seen % THROTTLE !== 0) { return; }\n"
        "    var p = workspace.cursorPos;\n"
        "    deliver(p.x, p.y);\n"
        "}\n"
        "\n"
        "var start = workspace.cursorPos;\n"
        "print(MARK + ' link loaded at ' + start.x + ',' + start.y);\n"
        "deliver(start.x, start.y);\n"
        "workspace.cursorPosChanged.connect(onMoved);\n"
        "print(MARK + ' link connected, reporting every THROTTLE moves');\n"
    ).replace("SERVICE", f'"{BUS_NAME}"') \
     .replace("OBJECT", f'"{OBJECT_PATH}"') \
     .replace("IFACE", f'"{FEED_INTERFACE}"') \
     .replace("THROTTLE", str(throttle)) \
     .replace("MARK", f'"{MARKER}"')
