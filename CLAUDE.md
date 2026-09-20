# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

> [!IMPORTANT]
> **This file is scratch. It is a working record of the trial and error leading
> up to something that runs, and the author intends to delete it before any
> public release.**
>
> It exists to stop later sessions re-walking dead ends, re-litigating settled
> decisions, and repeating errors already made and corrected here. That is why
> it keeps failed reasoning and corrections visible instead of tidying them
> away — the mistakes are the point.
>
> It is **not** the project's documentation, and nothing here may be the only
> copy of something worth keeping. Anything that should outlive the project's
> completion belongs in `README.md` instead. `README.md` deliberately does not
> reference this file, so that removing it breaks nothing.

## Project status

**Nothing is implemented.** The repository contains documentation only: this
file, `README.md`, a LICENSE and one image. No source file has ever existed
here. The `work/feed` branch was created but never used and points at the same
commit as `main`.

An earlier attempt (done with a different assistant) stalled before it could
even read the pointer position, and the project was frozen from 2025-11 until
the research recorded here was carried out on 2026-09-20. That research explains
why the earlier attempt could not have succeeded, and what is actually
available.

Nothing below has been tested on the author's hardware. It is all derived from
reading upstream source.

## Goal

Reproduce Windows 11's *Ease cursor movement between displays* on KDE Plasma /
Wayland, for a non-rectangular multi-display layout. See `README.md` and
`img/motivation.png`.

**The required semantics are specific, and getting them wrong makes the result
useless:**

- When the pointer reaches a stretch of a display edge that no neighbouring
  display covers, it must be moved onto the adjacent display **preserving its
  position along that edge** — it lands at the nearest valid point, so the
  motion reads as a slide.
- **It must never be warped to the centre of the target display**, or to any
  other fixed landmark. A jump to a fixed point destroys the continuity that is
  the entire purpose of the feature, and would be worse than the problem.

This is redirection, not the removal of resistance. See the terminology trap
below.

## Verified platform constraints

Everything in this section was verified by reading KWin and
xdg-desktop-portal-kde source at `master` on 2026-09-20. **These are private,
ABI-unstable internals — re-verify against the Plasma version actually in use
before relying on any of it.**

### A KWin script cannot move the pointer

`src/scripting/workspace_wrapper.h`:

```cpp
Q_PROPERTY(QPoint cursorPos READ cursorPos NOTIFY cursorPosChanged)
```

`READ` only, no `WRITE`, and the scripting API exposes no other pointer
manipulation. `src/effect/effecthandler.h` likewise exposes `cursorPos` as
read-only with no warp.

**Consequence:** the two-component design in `README.md` (a *Cursor Feed* KWin
script publishing positions over D-Bus, plus a Python *glide_cursor* that moves
the pointer) cannot work as written. The feed half is possible; the moving half
has no API behind it. That design is superseded.

Note on the feed half, if it is ever needed: `Workspace` is only available as a
QML singleton on KWin 6+, so a KWin script must be the QML/declarative kind, not
a plain JS script. Getting this wrong presents as "the cursor position cannot be
read at all" with no error, and is a plausible explanation for the original
dead end.

### KWin can warp the pointer to an arbitrary global position

`src/pointer_input.h`:

```cpp
bool supportsWarping() const;
void warp(const QPointF &pos);
```

This is the only mechanism that does what this project needs. The question is
what is allowed to call it.

### The InputCapture portal reaches `warp()` — but is unusable here

The full chain exists and works:

```
org.freedesktop.portal.InputCapture.Release(options{"cursor_position": (dd)})
  -> xdg-desktop-portal-kde  InputCapturePortal::Release
  -> KWin D-Bus org.kde.KWin.EIS.InputCapture  release(QPointF, bool applyPosition)
  -> EisInputCaptureManager::deactivate  ->  input()->pointer()->warp(pos)
```

KWin's barrier trigger is, remarkably, an exact match for this project's
problem — `src/plugins/eis/eisinputcapturemanager.cpp`:

```cpp
// Detect the user trying to move out of the workArea and across the barrier:
// Both current and previous positions are on the barrier but there was an orthogonal delta
```

That is precisely "pointer pinned against a dead edge while the user keeps
pushing".

**This approach is nevertheless rejected.** `xdg-desktop-portal-kde.notifyrc`:

```ini
[Event/inputcapturestarted]
Comment=Input is now being managed by an application
Action=Popup
Urgency=High
```

and `inputcapture.cpp` fires that notification on *every* activation:

> **Input Capture Started** — *<app> has taken over the pointer and keyboard.
> Press Meta+Shift+Esc to stop this.*

KWin also calls `Cursors::self()->hideCursor()` for the duration of each
capture. Since activation here means "the user bumped a corner", this produces
a high-urgency popup and a cursor blink every few seconds of normal use. That
is not an acceptable trade for smoother pointer movement.

This matches a rejection made during the original planning, and the rejection
stands — though note that the *screen-sharing indicator and highlighted
windows* are behaviour of the **ScreenCast / RemoteDesktop** portals, not of
InputCapture. RemoteDesktop is separately unsuitable for the same class of
reason: absolute pointer positioning through it requires a ScreenCast stream,
which raises the sharing indicator.

Secondary sources place InputCapture support in xdg-desktop-portal-kde at the
Plasma 6.1 cycle. Not independently verified.

### An out-of-tree KWin plugin can call `warp()` directly — REJECTED

> Kept for the record only. Ruled out by decision 4 under "Settled
> decisions": this code would run inside the compositor, and a crash takes
> down the session. Do not propose it.

KWin installs these as devel headers (`src/CMakeLists.txt`):

```
input.h  input_event.h  input_event_spy.h
pointer_input.h
plugin.h  pluginmanager.h
cursor.h  workspace.h  screenedge.h  ...
```

So a KWin **plugin** — not a script, not an effect — can do exactly what the
built-in `eis` plugin does: install an `InputEventSpy`, detect the
pinned-at-edge-with-orthogonal-delta condition, and call
`input()->pointer()->warp(target)`.

No portal, no permission dialog, no notification, no cursor hiding. The core
logic is roughly the ~40 lines of `BarrierSpy` plus `deactivate()`, rewritten
with this project's own policy.

Costs, which are real:

- Private, ABI-unstable headers. Expect to rebuild on every Plasma release and
  to be broken by renames (the KWin scripting API was renamed in Plasma 6.5).
- C++ and CMake. The Python helper in the README goes away.
- Requires the KWin development package.
- The code runs on every pointer motion event inside the compositor. **A crash
  takes down KWin and the user's session.**

### Terminology trap — read this before proposing anything

Several things in this problem space have near-identical names and opposite
purposes. Confusing them has already cost this project time, both in the
original ChatGPT sessions and once in a Claude session (see the correction note
below). Keep them apart:

| Name | What it is | Relation to this project |
|---|---|---|
| KWin **EdgeBarrier / CornerBarrier** | Deliberate resistance when the pointer crosses between screens | **Opposite of the goal.** Adds stickiness on purpose |
| KWin **screen edges / ElectricBorders** | Hot corners and edge actions (trigger Overview, etc.) | Unrelated. Only appears here because CornerBarrier exists to serve it |
| InputCapture portal **pointer barriers** | Trigger lines that start an input-capture session | A mechanism, unrelated to the two above. Rejected for other reasons |
| Windows **Ease cursor movement between displays** | Active redirection: slides the pointer along an edge into the adjacent display | **This is the goal.** Nothing in KWin provides it |

The distinction that matters: *removing resistance* is not *adding
redirection*. Only the latter solves this.

### KWin's corner barrier is real, but is NOT the cause of this problem

`src/pointer_input.cpp`:

```cpp
constexpr qreal cornerThreshold = 15;   // Manhattan distance from an output corner
...
} else if (options->cornerBarrier() && onCorner) {
    return EdgeBarrierType::CornerBarrier;
}
```
```cpp
case EdgeBarrierType::CornerBarrier:
    return 2000;
case EdgeBarrierType::NormalBarrier:
    return barrierWidth;
```

`src/kwin.kcfg`:

```xml
<group name="EdgeBarrier">
    <entry name="CornerBarrier" type="Bool"><default>true</default></entry>
    <entry name="EdgeBarrier" type="Int"><default>100</default><min>0</min><max>1000</max></entry>
</group>
```

**Correction, 2026-09-20.** An earlier version of this file claimed the three
marked areas in `img/motivation.png` were output corners, and that disabling
`CornerBarrier` might therefore resolve most of the problem. **That was wrong.**
The marked areas are tall *vertical bands* along the centre output's left and
right edges, covering the ranges that the neighbouring outputs do not span:

- centre's top edge down to the left monitor's top edge
- the left monitor's bottom edge down to centre's bottom edge
- the right monitor's bottom edge down to centre's bottom edge

Those bands are hundreds of pixels tall. `cornerThreshold` is 15px Manhattan and
only applies at an output's four corners, so the barrier touches at most the
extreme ends of each band. **The problem is geometry: the pointer has no
destination at those heights.**

What the settings actually buy:

- `EdgeBarrier=0` — removes the 100px resistance where a neighbouring output
  *does* exist. A genuine annoyance fix, unrelated to the dead bands.
- `CornerBarrier=false` — removes the 2000px barrier within 15px of an output
  corner. Marginal here.

Worth setting, free, zero risk, but **not a solution and not a prerequisite.**
Do not present it as one.

### The coordinate-space question — resolved, and favourably

**The author's hypothesis, recorded as stated:** on Wayland, the cursor
coordinates that can be read or written in a multi-display setup are relative to
a single display — the one owning the foreground window — rather than to the
whole desktop. Evidence offered: memory of the earlier attempt, and the
observed behaviour of an XP-Pen tablet, which stays confined to one display.
This was expected to be the project's main obstacle.

**The observation is real, but it is two separate phenomena, and neither blocks
this project.**

*For Wayland clients, the hypothesis is correct.* A client receives only
surface-local pointer coordinates and has no protocol-level way to learn the
global cursor position. This is by design. It is precisely why the read side
must not be a Wayland client — a KWin script runs inside the compositor, and
`workspace.cursorPos` is layout-global.

*The XP-Pen behaviour is a different mechanism: device-to-output mapping.*
`src/backends/libinput/connection.cpp` treats absolute device classes
differently:

```cpp
case LIBINPUT_EVENT_POINTER_MOTION_ABSOLUTE: {
    Q_EMIT pe->device()->pointerMotionAbsolute(
        pe->absolutePos(workspace()->geometry().size()), pe->time(), pe->device());
```

```cpp
// touch, by contrast, is resolved against one specific output
const QPointF globalPos = devicePointToGlobalPosition(te->absolutePos(output->modeSize()), output);
```

An absolute **pointer** event is scaled against `workspace()->geometry()` — the
bounding box of the entire layout. Only touch and tablet devices are bound to a
single output (see the `deviceOutput` resolution later in the same file, which
honours `device->outputName()` and falls back to a single output for touch).

**Consequences:**

- A tablet is confined to one display because it is a tablet, not because
  Wayland coordinates are per-display. The XP-Pen is not evidence of a
  coordinate-space limit.
- A uinput device that libinput classifies as an **absolute pointer** addresses
  the whole desktop. Getting that classification is the engineering requirement:
  `ABS_X`/`ABS_Y` plus `BTN_LEFT`, and it must **not** look like a touchscreen
  (`INPUT_PROP_DIRECT`) or a tablet (`BTN_TOOL_PEN`), or it will be mapped to a
  single output and the approach collapses.
- Since the scale target is read per event, a layout change needs no device
  re-creation.
- The bounding box includes dead area in a non-rectangular layout. That is
  harmless: `updatePosition()` resolves `outputAt(pos)` and confines to that
  output, so any target inside a real output is honoured.

This significantly raises confidence in the uinput approach below. It remains
untested on real hardware.

### Absolute motion bypasses the edge barrier — possible Python write path

`PointerInputRedirection::applyEdgeBarrier()` opens with:

```cpp
// edge barriers are counter-productive for absolute motion
if (relativeMotion.isNull()) {
    m_movementInEdgeBarrier = QPointF();
    return pos;
}
```

and `updatePosition()` validates the *destination*, not the path:

```cpp
const LogicalOutput *currentOutput = workspace()->outputAt(pos);
QPointF p = confineToBoundingBox(pos, currentOutput->geometry());
```

So a position landing inside any output is accepted, and absolute motion skips
the barrier entirely.

Unlike XTest, a `/dev/uinput` virtual device is a real evdev device as far as
libinput and KWin are concerned, so its events are trusted. This suggests a
write path that satisfies every constraint: **Python (`python-evdev`), outside
the compositor, so a bug cannot take down KWin**, with no portal and no
notification.

**Status: promising but untested on hardware.** The risk is now understood
precisely rather than vaguely — see "The coordinate-space question" above. It is
not that absolute coordinates are per-display; it is that libinput must classify
the device as an absolute *pointer*. If it is tagged as a touchscreen or tablet
instead, KWin binds it to one output and routes it through `touch_input` /
`tablet_input`, and the approach collapses. The XP-Pen's single-display
behaviour is exactly this failure mode in the wild.

Device definition therefore matters more than the event loop: `ABS_X`/`ABS_Y`
plus `BTN_LEFT`, no `INPUT_PROP_DIRECT`, no `BTN_TOOL_PEN`. Verify the
classification (`libinput list-devices`) before writing any movement logic.
Setup also requires access to `/dev/uinput` (an `input` group membership or a
udev rule).

If this holds, the read half can be a QML KWin script feeding positions over
D-Bus, as originally sketched — see below.

### The original two-component design, and why it was removed from the README

`README.md` originally described a *Cursor Feed* KWin script publishing pointer
positions over D-Bus plus a Python *glide_cursor* that moved the pointer. It was
removed from the README at the author's request, on the understanding that it
was definitively impossible.

That is true only of the half that was never specified: **a KWin script cannot
move the pointer.** The feed half works, and the uinput finding above may supply
the missing mover. The architecture itself is therefore **not** dead and may be
revived — it simply should not sit in the README as though it were a plan until
the uinput question is answered. Do not treat its absence from the README as a
rejection.

### Dead ends, recorded so they are not re-explored

- **XTest / `xdotool`** — KWin treats XTest-injected input as untrusted and does
  not feed it into the real Wayland input pipeline.
- **The Wayland pointer-warp protocol** — KWin restricts warping to surfaces of
  the window that currently holds pointer focus, so it cannot cross screens.
- **KWin script or KWin effect** — read-only pointer access, see above.
- **A C++ KWin plugin** — technically the cleanest option, but rejected: a crash
  inside the compositor takes down the session. See "Settled decisions".

## Settled decisions

Answered by the author on 2026-09-20. Treat these as closed; do not reopen them
without being asked.

1. **The approach rejected during the original planning was almost certainly the
   RemoteDesktop portal**, not InputCapture. The remembered objection — forced
   highlighting and overlay buttons on shared screens — is ScreenCast /
   RemoteDesktop behaviour. Both portals are rejected now regardless.
2. **A C++ KWin plugin was never considered.** The original work was done with
   ChatGPT and C++ was judged too heavy for that setup. It is genuinely new
   ground rather than something previously ruled out.
3. **Python was a real preference, not just an artifact of the architecture.**
   The reason was practical: error output is easy to paste into a chat session.
   Weight this when choosing between candidate approaches.
4. **This is for the author's own machine.** Distribution is not a goal, so
   ABI-churn maintenance cost is tolerable in principle — **but taking KWin down
   with it is not.** A tool that can kill the session on a bug would be
   stressful enough to defeat the purpose.

### Consequence: the C++ KWin plugin route is rejected

It remains the technically cleanest path, and it is documented above so the
reasoning is not lost. It is nonetheless **out of scope**: plugin code runs
inside the compositor on every pointer motion event, so a crash takes down the
session. Decision 4 rules that out. Do not propose it again.

## If and when implementation starts

- The target environment's Plasma version must be established first
  (`plasmashell --version`). Every finding above is version-sensitive.
- The display layout is mixed-DPI (3840x2160 alongside two 1920x1080). Coordinate
  space bugs between logical and physical pixels are a known hazard in this area
  across compositors; do not assume scale factors are uniform.
- The `kwinrc` barrier settings are worth applying for comfort, but they do not
  address the dead bands. Do not treat them as a gating step.
- Settle the uinput question first, and settle it in one step: create the
  virtual device and confirm with `libinput list-devices` that it is classified
  as a pointer, not a tablet or touchscreen. That single check decides whether a
  Python implementation is possible at all. Do not build movement logic before
  it passes.
- Then verify the landing semantics, not merely that the pointer moves. A jump
  to the centre of the target display is a failure, not a partial success — see
  "Goal".
- Keep anything that could crash out of the compositor process. This is a hard
  constraint, not a preference.

## Conventions

- Development happens on a feature branch, never directly on `main`.
- `README.md` is written in English. Keep it that way.
- When recording a platform constraint here, cite the file it came from and note
  that it was read from `master`, so a later reader knows to re-verify.
- **Before writing a finding here, ask whether it should survive this file's
  deletion.** If it would still help someone after the project is finished or
  abandoned, put it in `README.md` and leave only the working detail here. The
  corner-barrier trap is the worked example: the explanation a stranger needs
  lives in `README.md`, while the source excerpts and the record of how this
  project got it wrong stay here.
- Never add a `README.md` reference to this file. It has to remain deletable in
  one step.
- Corrections are appended and labelled, not applied silently. A later session
  needs to see that a claim was once believed and why it failed, or it will
  believe it again.

## Session record, 2026-09-20 (second session): implementation started

The previous session ended with research only. This one wrote code. Read the
split below before assuming anything here works: **no line of this has run on
the author's hardware.** It was written in a headless container with no
Wayland session, no KWin and no `/dev/uinput`, so the gating check that
CLAUDE.md itself demands could not be performed here.

### What is actually verified, and what is not

| Part | Status |
|---|---|
| `src/wcs/geometry.py` — dead bands, landing semantics | **Verified.** Pure functions, 28 unit tests, no platform dependency |
| `src/wcs/layout.py` — `kscreen-doctor -o` parsing | **Verified against a synthetic sample**, not against real output |
| `src/wcs/uinput.py` — struct layouts, ioctl numbers, axis mapping | **Constants verified** by compiling against `/usr/include/linux/uinput.h`; the device has never been created |
| Device classification, warping, landing behaviour | **Unverified.** Needs `tools/probe_uinput.py` on the real machine |

Run `python3 -m unittest discover -s tests` for the first row. It needs no
dependencies beyond the standard library.

### The classification rule, stated precisely

The previous session recorded the requirement ("`ABS_X`/`ABS_Y` plus
`BTN_LEFT`, no `INPUT_PROP_DIRECT`, no `BTN_TOOL_PEN`") without the rule
behind it. It comes from systemd's udev builtin,
`src/udev/udev-builtin-input_id.c`, whose ordering is what matters:

```c
if (test_bit(EV_ABS, ...) && test_bit(ABS_X, ...) && test_bit(ABS_Y, ...)) {
        if (test_bit(BTN_STYLUS, ...) || test_bit(BTN_TOOL_PEN, ...))
                is_tablet = true;
        else if (test_bit(BTN_TOOL_FINGER, ...) && !is_direct)
                is_touchpad = true;
        else if (test_bit(BTN_MOUSE, ...))
                /* This path is taken by VMware's USB mouse, which has
                 * absolute axes, but no touch/pressure button. */
                is_mouse = true;
        else if (test_bit(BTN_TOUCH, ...))
                is_touchscreen = true;
}
```

`BTN_MOUSE` and `BTN_LEFT` are the same code (`0x110`). The device this
project needs is therefore the *fourth* branch, and it exists in the wild
already: VMware's and QEMU's absolute USB pointers are exactly this shape and
work under Wayland compositors. That is a real precedent rather than a
hopeful reading, and it is the strongest evidence so far that the approach
will hold. It is still not a test on the author's machine.

Read from systemd `main` on 2026-09-20; re-verify against the installed
systemd version if classification comes back wrong.

### The axis mapping has an off-by-one worth knowing about

libinput scales a raw absolute value by `to_range / absinfo_range`, where
`absinfo_range()` is `maximum - minimum + 1`, not `maximum - minimum`. With
axes declared `0..65535` the divisor is therefore 65536. Getting this wrong
puts the far edge of the layout one pixel outside it — which lands in no
output at all, on exactly the edge this project cares about. `AxisMapping` in
`src/wcs/uinput.py` uses the +1 form and `tests/test_uinput.py` pins it.

The normalised `0..65535` range was chosen over "declare the axis as the
layout's pixel width" on purpose: KWin reads the scale target from the live
workspace geometry on every event, so a normalised device survives a display
being moved or added without being recreated.

### New: the read side cannot be `cursorPos` alone

This was not anticipated by the earlier research and it changes the design.

The plan was: a KWin script watches `workspace.cursorPos`, notices the pointer
is pinned at a dead edge, and asks the mover to redirect. **But a pinned
pointer stops moving, so `cursorPosChanged` stops firing.** The script can see
that the pointer *touched* a dead edge; it cannot see that the user is still
pushing. Redirecting on touch alone would fling the pointer to another display
every time the user reaches for something at the left of the centre screen —
worse than the problem.

KWin's own barrier does not have this problem because it sits inside the input
pipeline and reads the raw delta directly (`eisinputcapturemanager.cpp`: "Both
current and previous positions are on the barrier but there was an orthogonal
delta").

The way out, and it costs nothing extra: this project already needs
`/dev/uinput` access, which in practice means membership of the `input` group,
which also grants read access to `/dev/input/event*`. So the detector can read
the physical mouse's own `REL_X`/`REL_Y` stream and accumulate outward push
while the position feed says "pinned at a dead edge". That reproduces the
barrier semantics faithfully, outside the compositor, with no new permission.

**Not yet designed or written.** Recorded now so the next session does not
rediscover it after building a position feed that cannot work.

### What `tools/probe_uinput.py` settles, and why it is one command

CLAUDE.md said to settle the uinput question in one step. The probe goes
further than classification, because the same run can answer everything the
author's machine is needed for:

1. environment — Plasma version (which every finding here is sensitive to),
   session type, `/dev/uinput` access;
2. the live layout, and the dead bands computed from it — this alone is worth
   running, needs no permissions at all (`--layout-only`), and falsifies the
   geometry understanding immediately if the bands do not match
   `img/motivation.png`;
3. the gating classification check;
4. a scripted demonstration of the feature: for each dead band it places the
   pointer at the band and then warps it to the computed landing point.

Step 4 is the point. It exercises the landing semantics end to end **without
any detection logic existing yet**, so "does it slide to the nearest point or
jump to a display centre" can be judged by eye before the harder half is
built. If the semantics are wrong, they are wrong cheaply.

### Open, in the order they should be answered

1. Does the device classify as a pointer? (`tools/probe_uinput.py`) Nothing
   else matters until this passes.
2. Does the bounding box origin need subtracting? KWin scales against
   `workspace()->geometry().size()`, and KScreen normally normalises layouts
   to start at (0,0), so the question usually does not arise — but a layout
   with a negative origin would expose it as a constant offset. The probe
   prints a warning when the origin is not (0,0) so the symptom is
   recognisable rather than mysterious.
3. Detection: the `REL_X`/`REL_Y` accumulator described above, plus whatever
   supplies the global position. A QML KWin script feed remains the candidate
   for the position half; it is still unwritten.

## Probe result, 2026-09-20: the gating check PASSED

First run of `tools/probe_uinput.py` on the author's machine.

```
plasmashell 6.3.6 / kwin 6.3.6, Wayland, KDE
Debian 13, kernel 6.12.107+deb13-amd64
/dev/uinput: mode 0666, gid 102, writable

sysname     : input44
event node  : event258
udev tags   : ID_INPUT=1
              ID_INPUT_MOUSE=1
VERDICT     : PASS -- classified as a pointer (nothing else)
```

**The project's central risk is retired.** A uinput device declaring `ABS_X`,
`ABS_Y` and `BTN_LEFT`, with no `INPUT_PROP_DIRECT`, no `BTN_TOOL_PEN` and no
`BTN_TOUCH`, is tagged `ID_INPUT_MOUSE` and nothing else — so it takes the
absolute *pointer* path, is scaled against the whole workspace, and is not
bound to one output. The systemd `input_id` reading recorded above holds on
the installed version.

Outstanding, minor: `libinput list-devices` could not confirm this directly,
because it needs read access to `/dev/input/event*` and the author is not in
the `input` group (see the correction below). The udev tags are what libinput
itself consults, so this is a missing confirmation rather than a missing fact.
`sudo libinput list-devices | grep -A5 wayland-cursor-smoother` would close it
while the probe is running.

### The real layout, and two assumptions it settles

```
DP-1  1080x1920 at (5760,92)   portrait (rotation 2)
DP-3  1920x1080 at (0,512)
DP-4  3840x2160 at (1920,0)
bounding box 6840x2160 at (0,0)
```

Captured verbatim at `tests/data/kscreen-doctor-plasma-6.3.6.txt` and pinned
by `tests/test_layout.py`, so the parser is now tested against real output
rather than an imagined format.

- **Open question 2 is closed.** The bounding box origin is (0,0), so nothing
  needs subtracting from the axis mapping. A test pins it, so a future
  rearrangement that breaks the assumption will be noticed rather than
  presenting as a constant offset.
- **The mixed-DPI hazard does not currently apply.** Every output reports
  `Scale: 1`, including the 3840x2160 one, so logical and physical pixels
  coincide today. This is a property of the current configuration, not of the
  design; changing the 4K display's scale would reintroduce it.

### There is a fourth dead band, and it is real

The computed bands for DP-4 are:

```
push left   y    0.. 511  (512px)  -> DP-3
push left   y 1592..2159  (568px)  -> DP-3
push right  y    0..  91  ( 92px)  -> DP-1
push right  y 2012..2159  (148px)  -> DP-1
```

`img/motivation.png` marks three of these. The fourth — DP-4's right edge
above the portrait display's top edge — is only 92px tall, which is presumably
why it was never noticed, but it is the same defect. Worth knowing before
someone reads the mismatch between the image and the tool as a bug in the
tool.

### Correction, 2026-09-20: reading the physical mouse is NOT free

The session record above claims the `REL_X`/`REL_Y` detector "costs nothing
extra", reasoning that `/dev/uinput` access implies `input` group membership
which implies read access to `/dev/input/event*`. **The premise is false on
this machine.**

`/dev/uinput` here is mode `0666` — world-writable — so the write side needs
no group membership at all, and the author's groups are:

```
meatball dialout cdrom floppy sudo audio dip video plugdev users kvm netdev libvirt
```

No `input`. So the write half works today and the read half does not, and the
two permissions are independent rather than implied. `sudo usermod -aG input
$USER` plus a re-login would grant it — the author has sudo — but that is a
decision about widening access to every input device on the system, including
the keyboard, and it should be made deliberately rather than assumed.

**Do not design the detector around evdev access until this is answered.**

### Still unknown: what the demonstration actually looked like

The probe emitted the reach test and the landing test, and its log shows the
events it wrote:

```
-> centre of DP-1: (6300,1052)  raw (60362, 31918)
-> centre of DP-3: (960,1052)   raw (9198, 31918)
-> centre of DP-4: (3840,1080)  raw (36792, 32768)
DP-4 push left,  band    0.. 511: (1920,256)  -> (1917,514)  on DP-3
DP-4 push left,  band 1592..2159: (1920,1876) -> (1917,1589) on DP-3
DP-4 push right, band    0..  91: (5759,46)   -> (5762,94)   on DP-1
DP-4 push right, band 2012..2159: (5759,2086) -> (5762,2009) on DP-1
```

A log cannot say whether the pointer moved, or where it landed. Until the
author reports what was on screen, "the warp works" and "the landing semantics
are right" are both unconfirmed — the classification PASS above says only that
the events were accepted onto the pointer path.

### Reach test CONFIRMED; landing test still open

The author's report of the first run: *the cursor was visibly moving and did
appear on each display in turn, but it went too fast to tell whether it landed
in the right place.*

**That confirms the load-bearing claim empirically.** The pointer reached all
three displays, so an absolute event from this device really is scaled against
the whole workspace and really is not bound to one output. Up to this point
that was an inference from reading `connection.cpp`; it is now an observation.

The landing semantics remain unconfirmed, and that was a defect in the probe
rather than in the idea: four bands at a fixed 1.2s interval is fast enough to
see motion and far too fast to judge a position. Reworked:

- the demonstration now steps, waiting for Enter between parking the pointer
  at a dead edge and redirecting it;
- prompts are written to `/dev/tty`, so they still appear on screen when the
  run is piped to a log — which is how the first run was captured, and would
  otherwise have hidden every prompt in the file;
- each position is printed in terms that can be checked by looking:
  `(1917,514) = on DP-3 [1920x1080], 2px from its RIGHT edge, 2px from its TOP
  edge` rather than a bare coordinate pair.

`--no-step` restores the old timed behaviour for an unattended run.

### The landing point is constant within a band, and that is correct

Watching the stepped demonstration, every landing for a given band is the same
corner of the neighbouring display. That looks like the failure mode the Goal
section warns about — "never a fixed landmark" — and it is not. Within a dead
band, *every* position's nearest valid point is that corner, because the whole
band lies beyond the neighbour's extent along that edge. There is nowhere else
for it to be.

What varies, and what the user feels, is the slide: entering the band close to
the live edge slides a little, entering it far away slides a lot.
`test_slide_is_monotonic_in_distance_from_the_live_edge` pins exactly that.

The failure mode to keep watching for is a landing that ignores the entry
position *across* bands, or one that sits in the middle of the target display.
Neither is what a per-band corner is.
