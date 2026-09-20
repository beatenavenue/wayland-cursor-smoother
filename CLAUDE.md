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

### The demonstration animates, because a warp cannot be verified by eye

The stepped, self-describing version above was still the wrong answer. The
author's objection, and it is correct: *reading "2px from its RIGHT edge" and
then hunting for the pointer across three large displays is not something human
perception is good at.* Describing a position is no substitute for watching the
pointer arrive at it.

So the demonstration now emits a stream of absolute positions — 120 per second
by default, the order of magnitude a physical mouse reports at — and the
pointer travels. `src/wcs/motion.py`, tested by `tests/test_motion.py`.

Three things fell out of building it that are worth keeping:

**The path is the semantic, drawn.** A redirect means "slide along this edge to
the nearest point the neighbour reaches, then cross". Interpolating straight
from the pointer to the landing point instead cuts the corner through dead
space — and a position in dead space is not inert: `updatePosition()` resolves
it against the *nearest* output and clamps, so the pointer would scrabble
rather than travel. Sliding first and crossing second keeps every waypoint on
a real display. Verified against the author's layout: 2881 positions emitted
across the whole demonstration, none of them outside every display.

**Pacing by distance hides the one moment worth watching.** The slide is
258px; the crossing is 3px. Spaced evenly by arc length, the crossing gets 6%
of the animation. The redirect is therefore animated as two legs — 70% of the
time on the slide, 30% on the crossing — so leaving one display and arriving
on the next takes about a second instead of flicking past.

**The timing loop must use absolute deadlines.** Each frame writes three
evdev events; accumulating `sleep(interval)` would stretch a three-second
animation by however long that takes. `glide()` sleeps until a deadline
computed from the start, and takes its clock and its sleep as arguments so the
schedule is unit tested rather than assumed.

### Open: should the real redirect animate too?

Not asked and not decided, but the animation code now exists and the question
is cheap. A production redirect could be an instantaneous warp — what Windows
does — or a very short glide, perhaps 60–100ms, which would preserve
continuity even more strongly for the same reason the demonstration needed it:
the eye can follow a moving pointer and cannot follow a teleporting one. The
slide distances here run to a few hundred pixels, which is far enough to lose.

Worth trying both once detection exists. Do not assume the warp is correct
just because it is what the platform does natively.

## Landing test, 2026-09-20: the behaviour is right; the animation had a bug

The author's report of the animated run: *it looked as though the pointer moved
between displays in the ideal way, and that although it caught at the corner,
it slid in and crossed onto the neighbouring display.*

### What that establishes

- The pointer **slides along the edge and crosses**, rather than jumping
  straight across or landing somewhere arbitrary. That is the semantic the
  Goal section demands, and it is now an observation rather than a
  calculation.
- It does **not** land in the middle of a display. That failure would have
  been unmistakable and was not reported.
- Combined with the earlier reach test, the whole write path is confirmed on
  hardware: a virtual absolute pointer can put the cursor at a chosen point on
  any display, and the chosen points are the right ones.

**What remains is detection.** Nothing about moving the pointer is still in
question.

### "Caught at the corner" was a real defect, and it was mine

Two readings were possible: the demonstration deliberately stops the pointer at
the dead edge before redirecting, so "caught" may simply describe that. But the
pacing was also wrong, and the arithmetic says so without needing to ask.

The demonstration gave the crossing 30% of the animation. A crossing is three
pixels wide. Measured against the author's layout, where 65536 raw units span
6840px (9.58 raw units per pixel):

```
slide  258px over 2.10s  ->  206 distinct screen pixels, one step every  10ms
cross    3px over 0.90s  ->    4 distinct screen pixels, one step every 300ms
```

Of the 108 events sent during the crossing, 72% carried a raw value identical
to the previous one and were dropped by the kernel. A screen has no pixels
between pixels: **stretching a short move does not smooth it, it stutters it.**
One step every 300ms is precisely what "catching" looks like.

Fixed by pacing the crossing against what a screen can show rather than as a
share of the time — `max_legible_duration()` in `src/wcs/motion.py`, with a
floor of 20 visible steps per second. The time goes to the slide, a 0.4s still
hold marks the corner as a deliberate beat, and the crossing is drawn in 0.15s
(50ms per visible step).

**The general lesson, worth keeping:** any animation here is quantised twice —
once by the axis resolution and once by the screen. Before choosing a duration,
ask how many distinct positions the move can actually display. `258px` can fill
thirteen seconds; `3px` cannot fill one.

### The prompts never appeared, and the stepping never happened

The run logged `(no /dev/tty; falling back to timed steps)`, so the
demonstration played straight through at a fixed pace and the author could not
control it. The fallback worked as designed, but the cause matters: the process
had no controlling terminal. `Console` now tries `sys.stdin` when `/dev/tty`
cannot be opened, which covers a launcher that detaches the tty but leaves
stdin attached.

## Settled decisions, part 2 — detection permissions (2026-09-20)

Answered by the author after the write path was confirmed. Treat as closed.

5. **Joining the `input` group is rejected.** Applications do exist that ask
   for it, but it is a configuration that raises the system's attack surface
   and is regarded critically. It grants read access to every input device —
   the keyboard among them — to anything running as the user.

6. **The no-permission heuristic is not chosen, and its premise is
   incomplete.** The idea was to infer "still pushing" from the pointer's
   position feed alone: while pinned at a dead edge the x coordinate stops
   changing, but y keeps changing, because a human push is never perfectly
   axis-aligned.

   *The author's objection, which is correct and was not anticipated here:*
   not every pointing device produces smooth motion. Some move the cursor in
   fixed quantities in four directions, gamepad-fashion. For such a device the
   premise simply does not hold, and the heuristic would never fire.

   The uncomfortable part is who that excludes. Devices of that kind include
   ones used by people with disabilities — and someone driving a pointer in
   discrete steps has *more* trouble with a dead band than someone with a
   mouse, not less. A heuristic that works for the comfortable case and fails
   for the difficult one is the wrong shape for this feature.

   If it is ever revived as a fallback, that limitation has to be stated
   plainly in the documentation rather than discovered.

   Reading the device's own events does not have this problem: a device that
   moves in discrete steps still reports those steps.

7. **A udev rule is the chosen route, and is to be verified — including on a
   keyboard-attached pointing device such as a TrackPoint.**

### The obvious narrow rule is the wrong one, and the TrackPoint is why

The natural reading of "grant access to just my mouse" is a rule matching
`ATTRS{idVendor}` and `ATTRS{idProduct}`. **That is worse than useless here.**
`ATTRS{}` walks up the device tree to the USB device, and a keyboard with an
integrated pointing device presents its keyboard and its pointer as two
interfaces of one USB device, sharing those ids. A rule matching them grants
the keyboard node as well — the exact outcome the rule exists to prevent. It
is also brittle: replacing the mouse means rewriting it.

Matching on what udev has *called* the device avoids both problems:

```
ENV{ID_INPUT_KEYBOARD}=="1", GOTO="wcs_end"     # refuse first, always
ENV{ID_INPUT_MOUSE}=="1",          TAG+="uaccess"
ENV{ID_INPUT_POINTINGSTICK}=="1",  TAG+="uaccess"
ENV{ID_INPUT_TOUCHPAD}=="1",       TAG+="uaccess"
```

`TAG+="uaccess"` grants an ACL to the user holding the active local session
rather than to a group, so it does not reach remote or inactive users. It is
how systemd already grants joysticks and cameras, not a novel mechanism.

Generated by `udev_rule_text()` in `src/wcs/evdev.py`; the ordering and the
absence of any vendor match are pinned by `tests/test_evdev.py`.

### The case this cannot solve, and the probe says so

If a TrackPoint appears as a node tagged **both** keyboard and pointer, no
rule can help: one node cannot be split, so reading its motion means reading
its keystrokes. `tools/probe_evdev_access.py` names such nodes explicitly
rather than letting the rule silently skip them.

On a ThinkPad the TrackPoint is normally its own node (`TPPS/2 ... TrackPoint`)
separate from `AT Translated Set 2 keyboard`, in which case it is fine — but
that is an expectation, which is why it is being checked rather than assumed.

### What the probe settles, and its two conditions

`tools/probe_evdev_access.py` lists every `/dev/input/event*` with the roles
udev assigned and whether it is readable, then judges:

- at least one pointing device readable — otherwise there is nothing to detect
  with;
- **no keyboard readable** — and this is the condition that matters. A rule
  that is too wide still makes the feature work perfectly. Joining `input`
  would pass the first condition and fail the second, which is the whole point
  of stating them separately.

When a keyboard *is* readable it prints `getfacl` for that node, so "who
granted this" is answered rather than guessed.

`--watch SECONDS` then reads the readable pointing devices and counts motion
per device, so "does the TrackPoint actually reach us" is measured by moving
it. Absolute motion is counted as well as relative: an assistive pointer may
report positions rather than deltas, and a detector that only understood
`REL_*` would report it as silent.

### Do not read back our own device

`list_devices()` excludes the virtual pointer by name. Reading our own emitted
events would feed each redirect straight into the detector that requested it.
Not yet a live bug — the detector does not exist — but the loop is easy to
build by accident.

## Device survey, 2026-09-20: the TrackPoint is fine, and something else is not

First run of `tools/probe_evdev_access.py` on the author's machine, 38 event
nodes. Three findings.

### The TrackPoint splits, so the rule works

```
event13   keyboard,keys,pointingstick   Lenovo TrackPoint Keyboard II
event14   keys,mouse,pointingstick      Lenovo TrackPoint Keyboard II
```

`event14` carries **no `keyboard` tag**, so the rule grants it, and that is the
node the TrackPoint's motion comes out of. `event13` is the keyboard and stays
refused. The expectation recorded above holds on this hardware.

Two nodes *are* unsalvageable, and both are named rather than silently
skipped: `event13` itself, and `event31` (`XP-Pen Mouse`, tagged
`keyboard,keys,mouse`). Neither is needed.

### `keys` is not `keyboard`, and the difference had to be measured

`event14` carries `ID_INPUT_KEY`. That is *not* `ID_INPUT_KEYBOARD`: udev sets
the former for any `KEY_*` code at all — a volume button earns it — and the
latter only for a full alphabetic set. So "the rule grants a node with keys on
it" is true and says nothing about whether reading it could observe typing.

Rather than reason about it, the probe now reads
`/sys/class/input/<node>/device/capabilities/key` and counts how many of the
codes could spell something (`KEY_1`..`KEY_EQUAL`, `KEY_Q`..`KEY_RIGHTBRACE`,
`KEY_A`..`KEY_GRAVE`, `KEY_Z`..`KEY_SLASH`, `KEY_SPACE`). A node the rule
grants with a non-zero count is called out; zero is stated explicitly rather
than left as an absence.

### A keyboard was already world-readable, and it is not ours

The probe's first verdict was `NOT YET -- a keyboard is readable`. It was
`event7`, `XP-PEN DECO 03 Keyboard`, and `getfacl` gave the reason:

```
# owner: root
# group: input
user::rw-
group::rw-
other::rw-
```

`other::rw-` — mode 0666. Every node of that tablet is world **readable and
writable**, its keyboard interface included, and has been since before this
project existed. A vendor udev rule applied to the whole device rather than to
the interface that needed it.

**The verdict was wrong to blame the rule for it**, and would have sent the
reader to edit a file that was not responsible. `access_verdict()` now
separates:

- *keyboards readable via an ACL* — something granted them directly. This rule
  must never produce one, and a non-zero count fails.
- *keyboards already open* — world- or group-readable regardless of us.
  Reported with `getfacl`, and does not fail the verdict, because removing
  this project's rule would not close them.

`classify_grant()` decides which, from the mode bits and our group list, in
the order the kernel checks them.

**Worth saying plainly: this is a real exposure on the machine and it is
unrelated to this project.** Anything running as the author — or as anyone
else on that system — can read that keyboard today. Whether to tighten the
vendor's rule is a separate decision from anything here.

### Measured, 2026-09-20: every node the rule grants is free of text keys

The key-capability count run on the author's 38 nodes. What the rule grants:

```
NEW              event1     no text keys   XING WEI ... Composite Device Mouse
already readable event4     no text keys   XP-PEN DECO 03 Mouse
already readable event6     no text keys   XP-PEN DECO 03
NEW              event8     no text keys   RP2040 HID Remapper MIPB Mouse
NEW              event14    no text keys   Lenovo TrackPoint Keyboard II
```

`event14` — the TrackPoint's pointer half, the node whose `ID_INPUT_KEY` tag
prompted the measurement — carries **zero** keys that could spell anything.
The tag came from buttons, as expected, but it is now measured rather than
expected.

For contrast, every node the rule refuses that *is* a keyboard reports 47.
`SC211 (AVRCP)`, a Bluetooth audio remote, reports 11 and is refused anyway
for having no pointer role.

The probe now prints this preview **before** anything is installed, under
"what the rule would grant", marking each node NEW or already readable.
`would_grant()` mirrors `udev_rule_text()`; `tests/test_evdev.py` pins the two
to the same answer, including that a pointer node carrying non-text keys is
granted and a combined keyboard-pointer node is not.

### The author's ruling on the XP-Pen exposure

Accepted as not worth acting on: the exposed node is a tablet's auxiliary
keyboard, and its keystrokes being read would not cost anything.

One fact was raised in response and is recorded because it is a different
risk from the one weighed, not because the ruling is in doubt: `other::rw-`
is **read and write**. Writing to an evdev node injects events, so any
process on that system can send key events into the session through it.
Reading was the risk considered; injection was not.

### The rule is installed and the devices reach us

`--watch` after installing the rule:

```
SILENT  event1        0 units in   0 batches   XING WEI ... Composite Device Mouse
SILENT  event4        0 units in   0 batches   XP-PEN DECO 03 Mouse
SILENT  event6        0 units in   0 batches   XP-PEN DECO 03
moved   event8     3609 units in 878 batches   RP2040 HID Remapper MIPB Mouse
moved   event14    1749 units in 634 batches   Lenovo TrackPoint Keyboard II
```

**Both pointers that were exercised deliver motion**, the TrackPoint among
them. The three silent nodes are the tablet and an unused mouse; they were not
moved. No keyboard is readable through anything this project installed.

Batch sizes are worth noting for the detector: roughly 2.8 units per wakeup
from the TrackPoint and 4.1 from the mouse. A push accumulator will be
summing many small deltas, not a few large ones, so the threshold belongs in
pixels of accumulated travel rather than in event counts.

**Permissions are settled.** Nothing about reading the user's push is still
open.

### Remaining, and it is the last unverified piece

The detector needs the pointer's global position as well as the push. That is
the KWin script feed, and it is the one part with no hardware evidence behind
it at all — including CLAUDE.md's own claim that it must be a declarative QML
script rather than a plain JS one, which was read from source and never run.

The failure mode recorded for it is silent: no error, the position simply
never arrives. So it wants the same treatment the other two questions got —
one run that tries both forms and reports which produced output, rather than
picking one and debugging a silence.

## Feed probe, 2026-09-21: the note about JS scripts was wrong, and backwards

`tools/probe_kwin_feed.py` on Plasma 6.3.6. Both variants loaded and started;
the compositor's journal:

```
qml: WCS-FEED-PROBE qml on load 2742,1431
qml: WCS-FEED-PROBE qml callDBus unavailable: ReferenceError: callDBus is not defined
qml: WCS-FEED-PROBE qml connected to cursorPosChanged
js:  WCS-FEED-PROBE js on load 2742,1431
js:  WCS-FEED-PROBE js connected to cursorPosChanged
js:  WCS-FEED-PROBE js after 1 move(s) 2742,1431
js:  WCS-FEED-PROBE js after 30 move(s) 2918,1515
```

### Correction, 2026-09-21: a plain JS KWin script reads the cursor fine

The note recorded under "A KWin script cannot move the pointer" says:

> `Workspace` is only available as a QML singleton on KWin 6+, so a KWin
> script must be the QML/declarative kind, not a plain JS script. Getting this
> wrong presents as "the cursor position cannot be read at all" with no error,
> and is a plausible explanation for the original dead end.

**That is wrong.** A plain JS script reads `workspace.cursorPos` on KWin 6.3.6
and gets the same layout-global coordinates the QML singleton gives. Both
variants reported `2742,1431` at the same instant.

**And the recommendation it makes is the harmful direction.** A declarative
script has no `callDBus` — the probe got `ReferenceError: callDBus is not
defined` — so it can read the position and has no way to tell anything
outside the compositor about it. Following that note would produce a script
that works perfectly and delivers nothing, which is a far worse place to be
stuck than a script that fails loudly.

The JS variant's `callDBus` call did not throw: an exception there would have
aborted the script before the `connected to cursorPosChanged` line, and that
line is present, as are the two reports after it. So **JS can both read the
position and call out**, and it is the only one of the two that can.

### The feed is live, and `print()` reaches the journal unaided

`cursorPosChanged` fires on pointer motion and the reported position tracks
it: `2742,1431` at load, `2918,1515` thirty signals later. The feed half of
the original two-component design works.

`print()` from a script appears in `journalctl --user -u
plasma-kwin_wayland.service` with a `js:` or `qml:` prefix without enabling
any logging category. That was expected to be the unreliable channel and is
not; it is available for the daemon's own diagnostics.

### One small thing for the detector

The first signal after load reported the *same* position as the load-time
read (`2742,1431` twice). `cursorPosChanged` firing does not guarantee the
position changed. A detector that assumes every signal carries new data will
occasionally compute a zero delta and must not treat that as meaningful.

### Architecture, now fully determined

```
KWin JS script  reads workspace.cursorPos, holds the layout, and calls out
                only when the pointer enters or leaves a dead-band edge --
                not on every motion, which would be thousands of D-Bus calls
                a second
        |  callDBus
        v
Python daemon   watches the physical devices for accumulated outward push
                while the script says "pinned", decides when to fire, and
                writes the redirect to /dev/uinput
```

Every component of that is now verified on hardware except the D-Bus service
the script calls into, which does not exist yet. `callDBus` needs a name to
call; Python needs to own one. What is available on the machine —
`dbus-python`, `jeepney`, `pydbus` — has not been checked.

## Link probe, 2026-09-21: every link is now confirmed on hardware

`tools/probe_dbus_link.py`, 15 seconds, pointer moving:

```
PositionInts   42 call(s)   ['Int32', 'Int32', 'Int32']   (2674,1698,1) .. (3146,1343,42)
PositionString 42 call(s)   ['String']                    '2674,1698,1' .. '3146,1343,42'
call spacing   min 156ms  median 320ms  max 1009ms
```

**A KWin JS script can call into a Python-owned D-Bus name, and typed values
survive.** A JS number arrives as `dbus.Int32`, so the feed can send
coordinates as numbers; the preformatted-string fallback is not needed. Both
forms delivered all 42 calls with none dropped.

### The motion rate, measured

42 reports at one per 30 motions is ~1260 `cursorPosChanged` signals in 15
seconds: **about 84 per second** while the pointer is actually moving. The
earlier guess of "thousands of D-Bus calls a second" was wrong by more than an
order of magnitude.

That does not change the design — the feed should still call out on a state
change rather than on every sample, because a redirect needs one message, not
a stream — but it is now a choice backed by a number rather than a fear. It
also means a fallback that *does* stream is affordable if one is ever wanted.

### Nothing in the path is unverified any more

| Link | Confirmed by |
|---|---|
| Layout and dead bands match the desk | `probe_uinput.py --layout-only` |
| Virtual absolute pointer reaches every display | reach test |
| Landing semantics read as a slide | landing test |
| Pointer motion can be animated smoothly | animated demonstration |
| Physical device motion is readable, keyboards are not | `probe_evdev_access.py --watch` |
| A JS script reads the global pointer position, live | `probe_kwin_feed.py` |
| That script can call the daemon | `probe_dbus_link.py` |

What is left is code, not questions: the push detector, and the daemon that
joins the pieces.

## Settled decision 8 — the feel is configurable, 2026-09-21

The author's ruling: **warp versus glide, and the push threshold, vary by
person and must be user-settable.** Not a default with a code change behind
it; configuration.

`src/wcs/config.py`, INI at `$XDG_CONFIG_HOME/wayland-cursor-smoother.conf`,
the same shape as the `kwinrc` a KDE user already edits. Command line flags
override the file; the file overrides the defaults.

Two properties are pinned by tests rather than left to discipline:

- **The shipped default file parses to exactly `Config()`.** Otherwise
  someone who copies the documented file gets different behaviour from
  someone who has no file at all, and neither can tell why.
- **Every field of every settings dataclass is mentioned in that file.** A
  setting that exists but is not documented is one nobody will find.

### Validation is strict on purpose

An unknown key is an error, an unknown section is an error, and so is a value
outside the range that makes sense. The failure being avoided is specific: a
mistyped `threshhold` that is silently ignored leaves someone changing a
number, seeing no effect, and concluding the feature does not work. The error
messages name the offending text and list what was expected.

Three ranges are refused for reasons worth recording. A `threshold` of 0 would
fire on the first event, which is the contact-triggered behaviour the whole
detector exists to avoid. A `max_slide` of 0 would mean "never redirect",
which nobody writes on purpose — "off" has its own spellings (`none`, `off`,
`unlimited`, blank). A `duration` of 0 *is* allowed, because it is simply a
warp, and refusing it would be pedantry.

## The daemon's first failure, 2026-09-21: a discarded return value

The daemon started cleanly, created the device, loaded the feed, watched five
devices, and did **nothing**. No arming, no redirect, no error, anywhere.

### How it was found, and what the two wrong guesses cost

Bisecting a silence needs the silence cut in half, and it took two cuts.

*First cut* (`--diagnose`): the script loaded, ran, and logged its startup
line; no `Edge` call arrived. So the script was alive and the break was later.
On the way, a guess was made and was **wrong**: the daemon had declared
`in_signature="iii"` where the probe that proved the path declared none, and a
rejected signature does look exactly like silence. Changing it back to match
the probe was right on principle — never deviate from the configuration that
was actually tested — but it was not the bug, and the symptom did not move.

*Second cut* (tracing in the feed script): the script printed what it saw.

```
js: wcs-feed sees 1920,2054 -> band 1
```

The rectangle test was correct, the position was correct, `callDBus` was
reached, and the try/catch added at the same time never fired. Call made, no
exception, nothing received.

### The bug

```python
dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)      # daemon: wrong
name = dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)  # probe: right
```

`BusName` releases the bus name in `__del__`. Discarding the result acquires
the name and gives it straight back when the temporary is collected.

Every symptom follows. Acquisition succeeds, so the daemon logs that it owns
the name. The name is released a moment later. `callDBus` reaches a name
nobody owns; the bus answers with an error; **KWin discards that error because
the call is fire-and-forget**, so no JS exception is raised and nothing is
logged. A daemon that starts perfectly and is deaf.

The link probe worked only because it happened to be written `name = ...`.
One character of difference, invisible at every layer.

### Guarded, because no runtime check can see it

`tests/test_source_guards.py` parses every source file and fails on a
discarded call to anything in a list of results that must be kept. It is a
test that reads source rather than behaviour, which is justified here and
rarely elsewhere: the failure has no runtime signal at all. Reintroducing the
bug fails the suite with the file, the line and the reason.

### Two diagnostics that earned their place

The feed script's `trace` mode prints the position it read and the band it
computed, which is what separated "the geometry disagrees" from "the call
fails". Reading the code could not.

The `try`/`catch` around `callDBus` was added in the same change. Until it
existed, a call that threw and a call that was never reached looked the same.
It did not fire this time, and that silence was itself the evidence that
narrowed the search.

## The same edge twice, 2026-09-21: two correct notes that contradicted each other

Reported from real use: crossing a dead edge works once, then the *same* edge
stops firing, while a different edge is fine. The log named it without being
asked to:

```
redirect 31: (1920,2141) -> (1917,1589)
redirect 32: (1920,2141) -> (1917,1589)
redirect 33: (1920,2141) -> (1917,1589)
```

The *source* position is identical across consecutive redirects. The feed only
reports when `cursorPosChanged` fires, so an unchanged source means the pointer
never moved — the redirect was computed, logged, and had no effect.

### The cause is two things recorded separately and never put together

From `src/wcs/uinput.py`:

> The kernel drops an absolute event that repeats the current value... Callers
> that only ever warp to a new place — **which is this project's entire use** —
> need not care.

From the landing-test record, higher up this file:

> Within a dead band, *every* position's nearest valid point is that corner...
> There is nowhere else for it to be.

Both are true. Together they say the second redirect out of a band asks for
precisely the position the first one set, and the kernel discards it. The
clause in bold was simply wrong, and nothing in the code or the tests could
notice, because both statements were correct in isolation.

It is more general than repeated redirects, too. The physical pointer moves
the cursor without touching the virtual device's axes, so **any** request for
the position that device last emitted is dropped, however long ago.

### Why a glide hid it and a warp did not

A glide emits intermediate positions, which differ, so the pointer travels and
only the final event is at risk. A warp has no intermediate positions at all,
so it is one duplicate event and nothing happens. The bug was therefore
invisible until the author switched to `--style warp` while tuning — and
invisible again to anyone who only ever glides.

### The fix, and how it is tested without /dev/uinput

`move_raw` remembers the last raw pair and, when asked to repeat it, emits a
one-unit nudge in its own report first. One raw unit is about a tenth of a
pixel across a 6840px layout — below anything visible — and the position that
finally lands is exact.

`tests/test_uinput.py` writes the event stream down a real pipe and decodes
it, so the emitted reports are checked byte for byte with no device present:
that a first move is one plain report, that a repeat is preceded by a nudge,
that the nudge goes up rather than down at axis zero, and that the position
which lands is always the one asked for.
