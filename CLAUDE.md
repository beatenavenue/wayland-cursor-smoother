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

**It works, it is in use, and development is closed for now.** `bin/wcsd`
runs as a systemd user service and redirects the pointer out of the dead bands
in the author's layout. Built and confirmed on hardware on 2026-09-21: Plasma
6.3.6 / KWin 6.3.6, Debian 13, kernel 6.12.

The author's own summary: *not entirely ideal, but quite practical for
something one person built.* See "Closed, 2026-09-21", at the end of this
file, for what was confirmed on hardware last and what was deliberately left
alone.

> [!IMPORTANT]
> **This file is a record of how that happened, not a description of what
> exists.** For what the thing is and how to run it, read `README.md`, which
> is written for a stranger. Read this one to find out why a decision was
> taken, what was tried and rejected, and which claims below were believed and
> then turned out to be false.
>
> **Sections written before something ran are marked where they have been
> superseded.** Nothing is deleted — a later reader needs to see that a claim
> was once believed — but a stale claim that is merely old and a stale claim
> that was actively harmful are different things, and the harmful ones now say
> so at the point of reading rather than four hundred lines later.

An earlier attempt (done with a different assistant) stalled before it could
even read the pointer position, and the project was frozen from 2025-11 until
the research recorded here was carried out on 2026-09-20. The record below
explains why that attempt could not have succeeded — and, further down, why
one of the explanations this file first offered for it was itself wrong.

### Where to look

| | |
|---|---|
| What exists and how to run it | `README.md` |
| Why each approach was rejected | "Verified platform constraints", below |
| What the author decided, and why | "Settled decisions" parts 1 and 2, and decisions 8 to 12 |
| What was measured on hardware | the dated probe-result sections |
| Claims that turned out wrong | every section headed **Correction** |

### What is still open

Very little, and nothing that blocks use. Everything that was once listed
here as unverified has now run on the author's hardware — see "Closed,
2026-09-21".

- **Answered, 2026-09-23: the undo is now off by default.** The oddity below
  was the undo firing anywhere on screen. See "Settled decision 12".
- **The undo leaves a residual oddity.** It works and is practical, and the
  author still notices something slightly off about it. What that something is
  has not been pinned down; two candidates were named in advance and neither
  has been ruled in. See "Closed, 2026-09-21".
- **`min_facing` is implemented and has not run on hardware.** It is on
  `feat/edgerule`. See "min_facing, 2026-09-25".
- **Mixed-DPI is out of scope, not open.** See settled decision 11 — do not
  raise it as unfinished business.
- **A stale `Cursor Feed 1.0` KWin script package** is installed on the
  author's machine from the original attempt. It is disabled and unrelated to
  anything here — the daemon loads its feed from a temp file and never
  installs a package — but it will confuse whoever finds it next.

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

> [!WARNING]
> Written from source, before anything ran. Most of it held; **two claims did
> not**, and both are flagged inline below. Source-reading was good enough to
> rule approaches out and not good enough to rule one in.

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

> [!CAUTION]
> **The note that follows is WRONG and acting on it would cost you days.**
> A plain JS script reads the cursor fine, and a declarative one has no
> `callDBus`, so following this advice produces a script that works perfectly
> and can tell nothing outside the compositor. Left in place because it was
> believed; see "Feed probe, 2026-09-21" for what was measured.

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

> [!NOTE]
> It held. The device classifies as a pointer and reaches every display; see
> "Probe result, 2026-09-20". The read half is a **JS** script, not a QML one,
> for the reason flagged above.

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

> [!NOTE]
> **Historical: this was the plan, and it was followed.** Every item below was
> done and every one of them paid off, which is the reason it is kept rather
> than deleted. It is also the right checklist to re-run after a Plasma
> upgrade — the probes it describes all still exist.

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
- **Shell blocks in `README.md` are ```` ```bash ```` and carry no `$` prompt.**
  The author's objection, and it is right: every command there exists to be
  copied, and a prompt character is something the reader has to delete by hand
  or, worse, pastes by accident. Keep output out of those blocks too — a block
  the reader can select whole and run is the point.
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

## The method, since it worked three times

Three failures in this project were silences: something started cleanly, logged
nothing wrong, and did nothing. Each was found the same way, and none was found
by reading the code.

**Cut the silence in half, with a tool, and let the machine answer.** Not
"which line is wrong" but "which half of the chain is dead". `--diagnose`
exists because the daemon was mute; the feed script's `trace` mode exists
because `--diagnose` narrowed it to one of two possibilities and could not
choose between them.

**Make the thing under test say what it sees.** `wcs-feed sees 1920,2054 ->
band 1` settled in one line what no amount of reading the rectangle test could:
the geometry was right, the position was right, and the call was reached. That
left only the address, and the address was a bus name that had been released.

**A guess is not a diagnosis, even a good one.** Midway through, the
`in_signature="iii"` deviation was spotted and corrected. The reasoning was
sound — never run a configuration different from the one that was proven — and
it was not the bug. Reporting it as fixed would have cost the author another
round trip. Correct the deviation, say it is *a* deviation, and keep measuring.

**The log already knew.** Three consecutive redirects from an identical source
position can only mean the pointer never moved, because the feed reports on
change. That was printed on screen before anyone asked the right question of
it. Read the evidence that already exists before generating more.

**Write the guard where the failure has no runtime signal.** A discarded
`BusName` and a default path that writes into the checkout are both invisible
at runtime. `tests/test_source_guards.py` reads source rather than behaviour,
which is worth doing exactly this rarely.

## Settled decision 9 — a tray-resident front end is rejected, 2026-09-21

The author raised their own `dimmgr` (a PyQt5 `QSystemTrayIcon` front end for
PowerDevil's display timeout) as a model for making this daemon's residency
visible, and then withdrew it: *the state being visible is nice, but it only
adds clutter.* Treat that as closed.

Recorded because the reasoning was not only taste, and a later session may be
tempted by the same idea:

- **Merging the GUI into the daemon costs the proven configuration.** The
  daemon is built on `DBusGMainLoop` + `GLib.MainLoop` + `GLib.unix_signal_add`
  + GLib fd watches, and its D-Bus receiving object is literally the shape
  `probe_dbus_link.py` proved on hardware. A Qt main loop means rewriting that
  half, against this file's own lesson about never running a configuration
  different from the one that was tested.
- **It also weakens decision 4's separation.** A tray bug would take the
  redirect down with it.
- **A tray as a separate controller** (the shape `dimmgr` actually has --
  presets, a restart item, a status icon, talking to something else that does
  the work) has none of those problems and remains available: the daemon
  already owns a bus name with a `Reload` method, so adding `Status`, `Pause`
  and `SetStyle` would use the proven path. It is not wanted now.
- Residency and the tray are orthogonal in any case. A tray app still has to
  be started at login, so it is a front layer, not an autostart mechanism.

## Running it with the session, 2026-09-21: generated, and UNVERIFIED

> [!NOTE]
> **Superseded: it has since been installed and runs well.** The reasoning
> below stands; only the "never been run" framing is stale. See "Closed,
> 2026-09-21".

`wcsd --write-service` renders a systemd user unit for the checkout it is run
from. **Nothing below has been run through systemd**: this was written in a
container with no systemd user manager, no session bus and no KWin. What the
tests pin is the shape of the file, not that it starts anything.

### Why generate it rather than paste one into the README

The README carried a unit with `%h/src/wayland-cursor-smoother/bin/wcsd` in it
and a note to adjust the path. Two reasons that is the worse answer here, and
both are the argument `--write-rule` already made for the udev rule:

- **The paths are knowable.** `wcsd` knows where it is installed, which
  interpreter is running it, and which of `qdbus`/`gdbus`/`busctl` exists. A
  generated unit needs no editing, and editing is where a `%h` guess becomes a
  unit that fails to load.
- **Installables are not committed to the checkout.** The copy that takes
  effect lives under `~/.config/systemd/user`; a second copy tracked in the
  repository is exactly the "is this part of the project?" cost that commit
  7a2eae9 removed. `--write-service` prints to stdout, like `--write-config`,
  and writing a file stays an explicit choice.

### The hazard the unit exists to guard

`Daemon._load_script` logs a failure and carries on (`src/wcs/daemon.py`). That
is right for a compositor that goes away mid-session — the daemon should not
take itself down — but it means **a daemon started before KWin answers on the
session bus runs perfectly and receives nothing.** Silence, again, and with no
nonzero exit `Restart=on-failure` would not notice.

`After=plasma-kwin_wayland.service` is expected to be sufficient on its own.
That expectation is **not verified** — it rests on KWin's unit being
`Type=notify`, which was not checked against the installed Plasma. So the unit
also blocks on an `ExecStartPre` that polls KWin's `/Scripting` object until it
answers, bounded by `timeout 30`. It asks with whichever tool
`find_dbus_caller()` picks, so a machine where the gate passes is a machine
where the script load will work.

**The option not taken:** making the script load retry, or fail the process,
inside the daemon. That is the fix with real teeth, and it is a change to how
the daemon behaves when KWin restarts mid-session — a separate decision from
how it is started, and not one to take while implementing an autostart unit.

### Small things the unit settles

- `ExecReload` sends `SIGHUP`, so `systemctl --user reload` re-reads the
  layout without dropping the virtual pointer or the feed script. The signal
  handler already existed; nothing but the unit was needed to expose it.
- `RestartSec=5`, because a second copy started by hand exits 2 on the taken
  bus name and would otherwise spin against the start limit.
- No `%` specifier appears anywhere, and `tests/test_service.py` fails if one
  does: every path is baked, so a `%` could only be a mistake, and an unknown
  specifier makes systemd refuse the whole unit.

## Prior art read, 2026-09-21: MouseUnSnag

The author found <https://github.com/MouseUnSnag/MouseUnSnag> — a Windows 10
tool, C#, MIT — that solves this project's problem, and asked what it does
differently. Read at `master` on 2026-09-21. **It is not Windows 11's own
*Ease cursor movement between displays***; it is a third-party tool aimed at
the same defect, and it is the closest reference implementation available.

### What it does, from the code

`MouseHookHandler.LlMouseHookCallback` is the whole of the input side:

```csharp
var mouse = Win32Mouse.GetMouseLocation(lParam);
if (!_cursorScreenBounds.Contains(mouse) && NativeMethods.GetCursorPos(out var cursor)
    && _mouseLogic.HandleMouse(mouse, cursor, out var newCursor))
{
    Win32Mouse.SetCursorPos(newCursor);
    return (IntPtr) 1;
}
```

`mouse` is the position the mouse **asked** for — `WH_MOUSE_LL` runs before
the clamp, so the hook sees intent. `cursor` is where the pointer actually is.
`MouseLogic.HandleMouse` needs nothing else:

```csharp
var isStuck = (cursor != _lastMouse) && (mouseScreen != cursorScreen);
```

"The cursor is not where the previous event asked it to be, and the new ask is
on a different screen (or on none)." **One event. No accumulator, no
threshold, no window, no cooldown** — `grep` finds no timer and no hysteresis
anywhere in the program, and `Options` is three booleans (`Unstick`, `Jump`,
`Wrap`).

The landing:

```csharp
newCursor = jumpScreen.Bounds.ClosestBoundaryPoint(cursor);
```

Clamp the cursor into the target rectangle — the same semantic as
`redirect_target`, with an inset of zero. `DisplayList.JumpScreen` picks among
the screens lying in the direction of travel the one nearest the *intended*
point by Euclidean distance; `redirect_target` picks the one needing the
smallest slide. Different spellings of "preserve the position along the edge".

And `return (IntPtr) 1` **swallows the motion event**: the movement that
caused the jump never reaches the system.

### The five differences, and which are forced

| | MouseUnSnag | wcsd | Forced? |
|---|---|---|---|
| Intent (pre-clamp position) | given, every event | not observable; reconstructed from device deltas | **Forced.** No Wayland client sees it |
| The user's own motion | consumed (`return 1`) | cannot be removed; we only add events | **Forced.** A uinput device adds |
| A position beyond every screen | Windows refuses; cursor stays put | KWin resolves it to the *nearest* output and clamps there | **Forced**, and it is the suspected cause of the bounce-back loop |
| Trigger | one refused event | `threshold` device counts accumulated within `window` | **Chosen** |
| Motion, and landing | instant `SetCursorPos`, onto the boundary | `glide` by default, `inset` px inside | **Chosen** |

The first three explain why this project is shaped the way it is. **The last
two are ours, and they are where the behaviour the author calls wrong is most
likely to live.**

`detect.py` justifies the threshold as avoiding a redirect when someone merely
*reaches* for a display's edge. MouseUnSnag has no such guard and is reported
to be pleasant to use, which is evidence — not proof — that the guard is
costing more than it buys. The threshold is also exactly what makes a held
TrackPoint loop: pressure keeps producing counts, so the accumulator refills
about once a second, forever. A trigger that fires once per *refused motion*
rather than once per *accumulated distance* has no such failure mode.

Worth stating for the corner case that started this: at DP-3's bottom-right
corner, pushing further down, MouseUnSnag would find **no screen in that
direction** and simply do nothing — `ScreensInDirection` requires the
candidate to lie in the direction of travel, and DP-4 lies to the *right* of
DP-3, not below it. The cursor would rest against DP-3's bottom edge. There is
nothing in its design that could hand the pointer back to the display it came
from; that behaviour is KWin's nearest-output rule, which Windows does not
have.

### Two smaller notes

- **MouseUnSnag never animates.** If "Windows-like feel" is the target, that
  is a data point for `style = warp` over `glide`, and it bears on the open
  question "the feel is not settled".
- **It is a tray application** with its options persisted to
  `%APPDATA%/MouseUnSnag/config.txt`. That is the shape settled decision 9
  declined, and finding it here is not a reason to reopen it.

## Prior art read, 2026-09-21 (second): Little Big Mouse, and a correction

The author raised <https://github.com/mgth/LittleBigMouse> — Windows 10/11,
C# + a Rust daemon, and **an experimental Linux port developed on KDE Plasma 6
Wayland**. Read at `master` on 2026-09-21. It answers a question this file got
wrong a few hours earlier.

### Correction, 2026-09-21: "a uinput device can only add events" is NOT a forced constraint

The MouseUnSnag comparison above lists, as **forced**:

> | The user's own motion | consumed (`return 1`) | cannot be removed; we only add events | **Forced.** A uinput device adds |

**That is wrong.** `rust/crates/lbm-hook/src/hook/linux/evdev/router.rs`:

```rust
//! From the first `EVIOCGRAB` the physical mice deliver ONLY to this process
```

`EVIOCGRAB` takes the device away from everyone else, including the
compositor. LBM grabs the physical mice, and re-injects a corrected stream
through its own uinput device. So the user's motion *can* be removed on
Wayland; this project simply does not do it.

The price is stated plainly by their code and README, and it is not small:

- once grabbed, **you are the pointer driver**. LBM re-implements pointer
  acceleration itself, reading `kcminputrc` per device
  (`hook/linux/accel.rs`), and carries a second virtual *keyboard* for the
  key usages of combined mouse/keyboard receiver nodes;
- their README asks for `input` group membership, which is settled decision 5.
  Whether the `uaccess` ACL this project already installs is enough for
  `EVIOCGRAB` was **not checked** — it is a question, not a blocker;
- a bug now stops the mouse working at all, rather than failing to help.

The rest of that table stands. This row does not.

### What LBM actually does, from the code

Not "detect stuck, then jump". It owns movement, over a layout in millimetres:

- `lbm-layout/src/zoning/mod.rs::compute_links` cuts each edge at every
  coordinate where any other zone starts or ends (and at user-drawn section
  boundaries), then for each interval picks the nearest zone *beyond* that
  edge which **fully covers the interval**. An interval no zone covers gets
  `target: None` — **a wall**.
- `lbm-engine/src/lib.rs::find_target_zone` casts the movement vector and
  takes the zone whose border it crosses at the shortest travel;
  `NoZoneMatches` clips the cursor back into the current zone.
- Each interval carries `border_resistance` and `border_resistance_px` as
  `[move, drag]` pairs, plus `move_block`/`drag_block`. **Resistance per
  stretch of edge, and a different value while dragging a window.**

**So LBM does not solve this project's problem out of the box**: a dead band
is a wall, and the pointer stops there. What it solves is *where* a crossing
lands. Two things are still worth taking from it.

**Per-section, per-mode resistance.** The author's objection to loosening
`threshold` is a real defect this project shares with MouseUnSnag: reaching
for a window's edge inside a dead band can fling the pointer to another
display. LBM's answer is not one global threshold but resistance drawn onto
the stretch of edge that needs it, with a separate value for dragging.

**The continuous model itself.** With the device grabbed, there is no trigger
to tune, so there is no false trigger, and a crossing is reversible — moving
back takes you back. That is much closer to what the author describes missing
("coming back is oddly smooth") than anything a jump can do. It is also
exactly what a layout with no gaps would give: if every stretch of edge maps
somewhere, nothing is ever stuck and nothing ever teleports. LBM can be made
gap-free by adjusting relative display sizes in its UI, at the cost of a
crossing that is proportional rather than position-preserving — which is a
different semantic from the Goal section above, and a deliberate choice, not
a bug.

### What Windows 11 does, and what it does not

Searched 2026-09-21. The feature is *Ease cursor movement between displays*,
Settings -> System -> Display -> Multiple displays, since build 22557, stored
as `CursorDeadzoneJumpingSetting` in `HKCU\Control Panel\Cursors`. Microsoft's
own name for the mechanism is therefore **deadzone jumping**.

**It is the same family as this project, and it draws the same complaint.**
Users with misaligned monitors report the pointer teleporting to the corner of
the display above when they merely touch the top edge, and go looking for the
toggle. So "Windows 11 never does this to me" is not evidence that Windows has
a better mechanism; the symptom is documented on Windows too. What Windows
does *not* have is any tuning: one checkbox, no threshold, no style.

**A caution for a later session.** The search turns up US12124757, *Movement
of cursor between displays based on motion vectors*, which describes choosing
the destination display by the direction of travel. It is tempting to read it
as Microsoft's implementation. **It is not: the assignee is Lenovo**, filed
2022-07-26, granted 2024-10-22. Do not cite it as what Windows does.

## Settled decision 10 — the user feels the hand, not the pointer (2026-09-21)

The author's ruling, and it is the premise the rest of this file should have
been reasoned from. Recorded close to verbatim, because paraphrasing it loses
the part that matters.

> As a first premise, the user **does not look at the mouse cursor much**, and
> **frequently loses it**. People need countermeasures to find a lost cursor:
> an eyeball widget that follows it, the Ctrl-key ripple, shaking it to make
> it briefly huge.
>
> What the user feels at all times is **the amount of motion in their hand**,
> not the pointer's position on screen. That is exactly why a dead zone —
> where the hand moves and the pointer does not appear where it is expected —
> is stressful, and that is this project's motivation itself.

Three consequences, as the author stated them:

1. **The discomfort is "having discovered the deviation, being unable to
   return with an equal amount of motion".** It is *not* "losing sight of the
   pointer".
2. **No `landing` setting is needed. Only warp needs a remedy.**
3. **Only warp needs a way back.** A glide has already returned by the same
   amount of motion it took to slide in.

> [!NOTE]
> **Consequences 2 and 3 were revised on 2026-09-23.** Neither style needs a
> way back: the way back is never walled. See "Settled decision 12".

### Correction, 2026-09-21: the axis is reciprocity, not information

Earlier this session I argued the glide/warp split this way:

> **glide is a claim about how the pointer moved** — the animation draws the
> path, so the path must be a motion someone made. **warp is a function** —
> no path, so all that exists is the mapping, and a mapping wants to be
> invertible.

That is not wrong as far as it goes, and it is **the wrong axis**. It treats
the difference as *information* — the glide shows you where it went, the warp
does not — and therefore frames the warp's problem as disorientation. The
author's answer is that disorientation is not the complaint, because the
pointer is barely watched in either mode. The complaint is that **the hand
pushed one way and cannot undo it by pushing the same amount back**. A warp
leaves the hand holding a displacement it never made.

Reasoning from the screen rather than from the hand is the same mistake this
project exists to correct, and I made it while designing the fix for it.

### The reciprocal mapping is recorded, and not taken

The idea that reached this decision was the author's: a dead band should map
onto a *range* on the neighbour rather than a single point, and the range
should map back. Worked through, the return leg forces the target to be a
dead edge of the neighbour (nowhere else can a push be refused, so nowhere
else can a return be triggered), which makes the mapping "unroll the corner":
depth past the neighbour's extent becomes distance from the shared corner.
For this desk it fits — all four reciprocal ranges land inside the neighbour,
and the four edges they need (DP-3's top and bottom, DP-1's top and bottom)
are walls today, so nothing existing is overridden.

It is **not being implemented**, for two reasons:

- under `glide` it would animate a path nobody travelled (out of the band, up
  570px, across, then 568px back along the neighbour's bottom edge — roughly
  double today's journey);
- it returns the pointer to the *corresponding* point, whereas an undo
  returns it to the *exact* point, which is what "an equal push back" means.

Worth keeping because it is the right shape for a different goal: it makes the
layout gap-free rather than making a jump reversible.

## The undo, 2026-09-21: symmetry as the whole design

> [!CAUTION]
> **Off by default since 2026-09-23, and built on a false premise.** It
> assumed the way back was a detour. It is not; the way back is open. See
> "Settled decision 12".

> [!NOTE]
> **Superseded in part: it has since run on hardware and is in use.** The
> "what is not verified" section at the end is the stale part. See "Closed,
> 2026-09-21".

`UndoLatch` in `src/wcs/detect.py`, wired in `daemon.py`, off in glide.

**The same `threshold` buys the redirect and buys it back.** No second number:
"an equal push the other way" is the requirement, so a separate
`undo_threshold` would be a different feature wearing this one's name. The
gesture is its own inverse and there is nothing new to learn.

What the latch is, precisely:

- armed at each warp with the source position and the push direction;
- accumulates the *reverse* component of device motion, using the same
  `outward_component` negated, so motion along the edge still counts for
  nothing and a wobble outward drains it rather than being ignored;
- fires once, warping back to the exact source, and disarms;
- expires after `undo_window` (default 3s) — long enough to notice, short
  enough that a push a minute later is a new intention;
- **disarms on a button press.** Clicking means the user meant to be there,
  and taking back a position they have already acted on would be worse than
  not offering the undo at all.

### Two things that could have gone silently wrong

**A setting that does nothing.** `undo_window` is inert under `glide`, which
is exactly the failure decision 8's validation section is about. Both
`--check` and the daemon's startup say so in as many words —
`undo: 3s, but INACTIVE -- it takes a warp back and style is glide` — rather
than leaving the reader to discover it.

**Spelling "off" two ways.** `--undo-window 0` and `undo_window = off` have to
mean the same thing, so zero maps to `None` in `_validate` rather than being
refused by the range check. This is the same trap as `--threshold 0`, which
was once accepted on the command line while `threshold = 0` was refused.

### What is not verified

The unit tests pin the gesture's arithmetic and nothing else. **No warp has
been taken back on hardware.** Two things to watch for on the first run:

- the reverse push moves the pointer before it fires, because we cannot
  consume input — so the pointer visibly travels one way and then snaps back.
  Whether that reads as a correction or as a second surprise is a question for
  the eye, not the tests;
- a held TrackPoint pushed back and forth could ping-pong between a redirect
  and its undo. The cooldown guards one direction and `undo_window` the
  other, but the pairing has never been exercised.

## Settled decision 11 — mixed-DPI is out of scope (2026-09-21)

The author's ruling: **not needed, outside my interest.**

This file listed "mixed-DPI is untested" as open from the first session
onward, and it was never a defect — only an untested assumption. Every display
on this desk reports `Scale: 1`, the code works in KWin's logical coordinates
throughout, and the author has no scaled display and no plan to add one.

So it is closed rather than pending. **Do not raise it as unfinished
business.** `README.md` still says mixed-DPI layouts are untested, which is
true and is the right thing to tell a stranger; that is a statement of fact,
not a task.

## Closed, 2026-09-21: the last three unknowns, answered on hardware

The author ran what was left and reported back. All three items that this file
carried as unverified are now settled, and development is closed for now.

| | Result |
|---|---|
| The systemd user unit | **Installed and running. Works well.** |
| The undo (taking a warp back) | **Works. Slightly odd still, but practical.** |
| Mixed-DPI | **Not investigated, and not going to be** — settled decision 11 |

### The unit needed nothing

It was written in a container with no systemd, no session bus and no KWin, and
it started and ran on the first attempt on real hardware. Both guesses it was
built on held: the ordering against KWin, and the `ExecStartPre` that waits
for the scripting interface rather than trusting `After=` alone. Whether the
gate was ever load-bearing is unknown and now unanswerable — nothing failed,
which is the outcome it was there to produce.

### The undo works, and something about it is still not right

The author's report: it works and is practical, **and a slight oddity
remains** — not enough to want it changed, not nothing either. What the
oddity is has not been pinned down.

Two candidates were named before the first run, and **neither has been ruled
in or out**:

- the reverse push moves the pointer before the undo fires, because this
  project cannot consume input, so the pointer visibly travels one way and
  then snaps back. Whether that reads as a correction or as a second surprise
  was always a question for the eye;
- the same-`threshold` symmetry may simply be more motion than a correction
  wants. Asymmetry — a lighter push to undo than to fire — is the obvious
  thing to try, and was deliberately not built, because "an equal push the
  other way" is what settled decision 10 asked for.

If this is ever picked up again, that is the place to start, and the honest
first step is to find out which of the two it is rather than changing both.

### Where "the feel is not settled" went

That item sat in the open list from the first working build, saying only that
the author found it different from Windows in some way not yet pinned down.
**It is not dropped, it is answered**, and the answer took most of a session:
the difference was never the animation or the threshold. It was that a warp
goes one way and the hand cannot take it back — settled decision 10, and the
undo built from it. What is left of it is the residual oddity above, which is
a much smaller and much better specified thing than the item it replaces.

### What this file is now for

Everything it records has either shipped, been ruled out, or been recorded as
a correction. It has done its job, and the author intends to delete it before
any public release — the note at the top still applies. `README.md` stands on
its own and does not reference this file, so deleting it breaks nothing.

## Settled decision 12: undo is off by default (2026-09-23)

The author's report: after a warp, for a while, a slight movement back
snapped the pointer to where the warp started, wherever the pointer was.
Crossing the wall reads as "did I just brush a corner?", and is close to
invisible. The undo fired with the pointer in the middle of the other display,
and it was not "the same amount of motion back" either.

### What the code did

- `UndoLatch` takes no position, and could not use one. The feed reports only
  inside the watch strips, so once a warp has left the strip the daemon has no
  position at all.
- The accumulator stops at zero. So it measures travel back **since the
  furthest point reached**, not net travel since the warp. Run against the
  class: 300 counts left then 100 right fires; downward motion drifting +1 x
  per batch fires after 100 batches.
- It shares the push's 100, but the push only accumulates while the pointer
  is pinned at a dead edge. The undo accumulates anywhere. Same number, very
  different condition.

This is very probably the "residual oddity" left open on 2026-09-21. Of the
two candidates named then, the first (the pointer moves before the undo
fires) is part of what was seen. The second (the same threshold may be too
much motion) was backwards: it fired on too little, and in the wrong place.

### The alternative the author proposed, and why it fails as stated

For a while after a warp, watch the exit of the way back, and re-cross when
the user pushes there. **The exit is not a wall.** Computed for all four bands
on this desk, 3px back from each landing point is on DP-4:

```
band 0  DP-4 left   -> DP-3 (1917,514)   3px back: DP-4 (1920,514)
band 1  DP-4 left   -> DP-3 (1917,1589)  3px back: DP-4 (1920,1589)
band 2  DP-4 right  -> DP-1 (5762,94)    3px back: DP-4 (5759,94)
band 3  DP-4 right  -> DP-1 (5762,2009)  3px back: DP-4 (5759,2009)
```

This follows from the landing rule: the landing point is the nearest point
the neighbour reaches, which is where the two displays meet. A push detector
needs the pointer pinned. On a live edge KWin passes it straight through.
This desk has `EdgeBarrier=0` (read from `kwinrc` on 2026-09-23), so the
pointer crosses on the first event and a push there would never fire. With
the default `EdgeBarrier=100` it would race KWin's own barrier.

A reading that does work was offered and not taken: use the crossing itself
as the trigger. The feed already sends `Edge(-1, x, y)` on leaving a strip, so
a position past the edge right after an exit strip means the pointer came
back.

On whether strips can change at runtime: they need not. The exit is fixed per
band (every point in a band lands on the same corner), so exit strips can be
computed at layout time. The script can always report them and the daemon can
decide whether they are open. The only direction proven on hardware is
script to daemon.

### The experiment, and the ruling

With `--undo-window 0` the author found the return more natural: the way back
has no catch at all, so the movement that matches the crossing push brings the
pointer back. Ruling: **keep the feature, default it to off**, and correct the
explanation everywhere it appears.

### Correction, 2026-09-23: "the way home is a detour" was wrong

Written on 2026-09-21 in `README.md`, `README.ja.md`, the `--write-config`
comment and the `UndoLatch` docstring:

> Without this the pointer does not come back: the way home is a detour around
> the edge the redirect slid along.

**False.** The way back is always open, for the reason above. What differs is
where the pointer comes out: at the end of the dead band, one slide away from
where it started (258px for band 0). That is true for `glide` as much as for
`warp`. The claim was reasoned and never measured, and the whole undo was
built on it. The diagram (`img/style.svg`) drew the same claim as a green
arrow back to the start, and now draws the way back crossing at the corner.

### On the author's machine

A config file written by `--write-config` before this change says
`undo_window = 3` in so many words, and the new default does nothing to it.
That line has to be changed to `off` or removed.

## min_facing, 2026-09-25: implemented, not yet run on hardware

The author's hypothesis, written up in both READMEs under "A hypothesis about
when a wall feels expected": a wall feels expected when the display beyond it
mostly sits off to the side, and unexpected when it is almost straight ahead.
The measure is the **facing ratio**: of the target display's edge that faces
the current display, the share that actually touches it. `min_facing`
(`[detect]`, percent, default 0) redirects only where the ratio is at least
that.

Default 0 changes nothing: every band on the author's desk faces 100%, and a
ratio is never below 0. The four watch strips are byte for byte what they were.

### Decisions taken while implementing, and why

- **Checked after choosing the target, not used to choose it.** The READMEs
  say that below the threshold "nothing happens, and the pointer stops at the
  wall". Filtering candidates first would instead send the pointer to some
  other, further display. `test_a_refused_display_is_not_swapped_for_a_further_one`
  pins it. Asked of the author; see "Open" below.
- **Measured on the target's edge**, as the READMEs define it. So it depends
  on direction. The laptop in `img/desk-setup.svg` faces the monitor 26%, and
  the monitor faces the laptop 37%.
- **A display that does not touch the current one is 0%**, because it has no
  opening. "Touching" is the same test `dead_bands` uses for a backed edge.
  So with any `min_facing` above 0, a redirect across a gap or past another
  display is refused.
- **A band is split where its target changes.** Before, one probe in the
  middle of a band decided whether the whole band was watched. That was right
  while every point in a band went to the same display. With two or more
  displays beyond one edge the nearest can change partway, and the two parts
  can have different ratios. `_runs_by_target` in `src/wcs/feed.py` splits
  those bands; a band with one display beyond is untouched, so this costs
  nothing on a normal desk.
- **`[detect]`, not `[redirect]`**, because that is where the author's
  README placed it. `max_slide`, which refuses redirects the same way, is in
  `[redirect]`. **Reversed on 2026-09-26: it is in `[redirect]` now.** See
  "Answered, 2026-09-26" below.

### A bug found on the way

`configparser`'s default interpolation gives `%` a meaning, so
`min_facing = 50%` (the obvious thing to write for a percentage) raised a
traceback instead of a `ConfigError`. Every setting had this; none had a
reason to type `%` until now. `parse_config` turns interpolation off, and
`50%` is refused as not a number.

### Open

- Whether a refused target should instead fall through to another display.
- Whether the asymmetry by direction is wanted.
- ~~Whether `50%` should be accepted as 50.~~ Answered below: no.
- Whether the facing ratio matches what is felt at all. It is a hypothesis,
  and nothing here tests that part.

### Answered, 2026-09-26

- **`min_facing` moved to `[redirect]`.** The author's ruling, from reading
  the code: it is passed to `redirect_target` and `watch_bands` beside
  `max_slide` and `inset`, so it belongs with them. The `--min-facing` flag
  moved to the redirect group, and the Japanese comment translation moved
  with it. Nothing had shipped, so no config file can hold it under
  `[detect]`.
- **`50%` stays refused.** Only a plain number is accepted.
- **The first two open items are being reworked by the author.** The concept
  and the calculation are being reconsidered, and instructions will follow.
  Do not settle them from here.

### The author expects no redirect to a non-adjacent display. The code does it.

Raised on 2026-09-26. The author's understanding: *this tool should not help
the pointer move to a display that is not adjacent.* That is not what the code
does, and it has not been since the first geometry commit (252deda,
2026-09-20). `redirect_target` takes every display lying wholly past the edge,
at any distance, and the smallest slide wins. Run against the base before any
`min_facing` work, with three displays in a row where the middle one is short:

```
A  x    0..1000  y 0..1000   tall
B  x 1000..2000  y 0..400    short, top-aligned
C  x 2000..3000  y 0..1000   tall

C left edge y=450: -> A at (997,450)  slide 0  jump 1003px
```

The pointer jumps over B to A, because A needs no slide and B needs one.
`test_the_smallest_slide_wins_over_the_smallest_gap` has pinned the same
thing since that commit, with a 900px gap. **Not changed.** Whether it should
be is the author's decision, and it touches the facing-ratio rework above.
