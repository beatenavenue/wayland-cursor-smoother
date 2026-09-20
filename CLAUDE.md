# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## Project status

**Nothing is implemented.** The repository contains a README, a LICENSE and one
image. As of this writing there are three commits, all from 2025-11, and the
`work/feed` branch points at the same commit as `main` — it was created but
never used.

An earlier attempt (done with a different assistant) stalled before it could
even read the pointer position, and the project has been frozen since. The
research below explains why that attempt could not have succeeded, and what is
actually available.

## Goal

On KDE Plasma / Wayland with a non-rectangular multi-display layout, let the
pointer cross the "dead corners" where a display edge blocks it, instead of
getting stuck. Comparable to Windows 11's *Ease cursor movement between
displays*. See `README.md` and `img/motivation.png`.

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

### KWin adds a deliberate corner barrier, on by default

`src/pointer_input.cpp`:

```cpp
constexpr qreal cornerThreshold = 15;
const bool onCorner = (pos - lastOutputGeometry.topLeft()).manhattanLength() <= cornerThreshold
    || ... /* the other three corners */;
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

`applyEdgeBarrier()` accumulates movement into `m_movementInEdgeBarrier` and
only lets the pointer through once the accumulated push exceeds the barrier
width. At 2000px that is effectively never.

**The three awkward spots in `img/motivation.png` are all output corners.** A
large part of the reported problem may therefore be this feature rather than the
display geometry, and may be fixable with nothing but:

```ini
[EdgeBarrier]
CornerBarrier=false
EdgeBarrier=0
```

The feature arrived in Plasma 6.1 and has drawn complaints upstream about its
default. **This must be tested before any code is written.** It costs nothing
and carries no risk. It cannot, however, create screen area that does not
exist — where an output edge has no neighbour opposite it, the pointer still
has nowhere to go.

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

**Status: promising but unverified.** The open risk is that libinput classifies
an absolute-positioning uinput device as a tablet or touchscreen and maps it to
a single output, in which case KWin routes it through `tablet_input` /
`touch_input` rather than `PointerInputRedirection`. This has to be settled on
real hardware before anything is built on it. Setup also requires access to
`/dev/uinput` (an `input` group membership or a udev rule).

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
- **Try the `kwinrc` barrier settings first.** They may resolve the problem
  outright, and nothing else is worth doing until that is known.
- If code is still needed, settle the uinput question next. Whether libinput
  presents a virtual absolute-positioning device as a pointer or as a
  tablet/touchscreen decides whether a Python implementation is possible at all.
- Keep anything that could crash out of the compositor process. This is a hard
  constraint, not a preference.

## Conventions

- Development happens on a feature branch, never directly on `main`.
- `README.md` is written in English. Keep it that way.
- When recording a platform constraint here, cite the file it came from and note
  that it was read from `master`, so a later reader knows to re-verify.
