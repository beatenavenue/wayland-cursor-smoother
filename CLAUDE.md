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

### An out-of-tree KWin plugin can call `warp()` directly

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

### Dead ends, recorded so they are not re-explored

- **XTest / `xdotool`** — KWin treats XTest-injected input as untrusted and does
  not feed it into the real Wayland input pipeline.
- **The Wayland pointer-warp protocol** — KWin restricts warping to surfaces of
  the window that currently holds pointer focus, so it cannot cross screens.
- **KWin script or KWin effect** — read-only pointer access, see above.

## Open questions

These are the user's to decide and are deliberately left unanswered. Do not
pick one unilaterally.

1. Was the approach rejected during original planning the **RemoteDesktop**
   portal (screen-sharing indicator) rather than **InputCapture** (notification
   spam)? Both are rejected now, but for different reasons.
2. Was a **C++ KWin plugin** considered during the original planning and ruled
   out? If so, why? If it was never considered, it is genuinely new ground.
3. Was **Python** a requirement, or only a consequence of the assumed
   architecture? The plugin route is C++ only.
4. Is this for the author's machine only, or intended for distribution? This
   determines how much the ABI-churn maintenance cost matters, and is the single
   biggest factor in choosing a direction.

## If and when implementation starts

- The target environment's Plasma version must be established first
  (`plasmashell --version`). Every finding above is version-sensitive.
- The display layout is mixed-DPI (3840x2160 alongside two 1920x1080). Coordinate
  space bugs between logical and physical pixels are a known hazard in this area
  across compositors; do not assume scale factors are uniform.
- Validate the edge-detection condition before building anything on top of it.
  It is the load-bearing assumption, and if it does not fire, nothing else
  matters.

## Conventions

- Development happens on a feature branch, never directly on `main`.
- `README.md` is written in English. Keep it that way.
- When recording a platform constraint here, cite the file it came from and note
  that it was read from `master`, so a later reader knows to re-verify.
