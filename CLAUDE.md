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
