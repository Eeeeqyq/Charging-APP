# Charging-APP

## What this is

`Battery.py` is a small native macOS window (PyObjC/AppKit, one file) showing the power the Mac takes from its charger, what the battery is doing, and the total system load, updating every second. macOS on Apple Silicon only. The old one-line terminal version is in git history (`b1a4ffe`).

## Rules

- **The user makes every commit.** Leave all changes in the working tree. Hard guardrail: no `git commit`, `git push` or index changes (`git add`, `git rm --cached`); when one is needed, give the user the command. Put this rule in every sub-agent's prompt, since sub-agents don't see it otherwise.
- **Serial numbers stay private.** ioreg output includes battery and adapter serials (`Serial`, `BatteryData.Serial`, `AdapterDetails.SerialString`). Read fields by name and print only the fields you need. Keep serials out of output, logs, commits and fixtures; test data is made-up values, never a saved ioreg dump.
- **The README keeps the author's note** that this is a personal-interest project made just to see some charging info, and that Claude did most of the work.
- **The design is settled with the user.** Layout, wording, colors and behavior below (and the strings in `describe()`) were agreed field by field. Change them only when asked, and state any new default you pick so the user can overrule it.

## Run

```sh
uv run Battery.py        # or: python3 Battery.py
```

- Dependencies live in the PEP 723 block at the top of `Battery.py`; uv builds and caches the environment. Nothing to install besides uv.
- Without PyObjC, `Battery.py` relaunches itself through `uv run`, which is how `python3` and VS Code's ▶ (both Apple's Python 3.9 here) still work. So the whole file has to **parse on Python 3.9**: no `match`, no 3.10+ syntax. Check with `/usr/bin/python3 -c "compile(open('Battery.py').read(), 'Battery.py', 'exec')"`.
- `requires-python = ">=3.14"` is deliberate. With conda base active, uv would otherwise pick Anaconda's 3.13, a non-framework build that shows up as "python" in the Dock and menu bar. Homebrew's 3.14 is a framework build, which lets the CFBundleName rename to "Charging" work.
- Quit with ⌘Q, Dock icon → Quit, or Ctrl+C in the launching terminal. Closing the window keeps the app in the Dock, and the app lives as long as that terminal. A normal run prints nothing.

## Design

- **Window.** A titled, closable, miniaturizable window 300 pt wide, not resizable. Transparent title bar with full-size content and no title text. Frosted `NSVisualEffectView`: material `menu`, behind-window blending, always active. It follows light/dark on its own. The height fits the content and animates on plug/unplug, keeping the top edge in place. Every launch opens centered and unpinned (`restorable` off).
- **Pin.** A pin icon at the bottom right toggles `NSFloatingWindowLevel`, on the current Space only. The pinned state isn't saved.
- **Content.** The headline is power in ("62.4 W", "of 94 W charger", plus a bar showing the share of the charger's rating in use). On battery it's the power drawn from the battery ("from battery"), and the bar, "Into battery" and "System load" rows are hidden. Below that: the status line (colored dot, and a second quieter line when it doesn't fit), then rows for Battery %, Into battery and System load. The footer "Updated N s ago" appears only on the ioreg fallback. Watts have one decimal, the charger rating is a whole number, times read "1 h 12 min", digits are monospaced.
- **Status** comes from ioreg flags, except that "plugged in" is decided live from `PDTR > 0.5 W`, so unplugging shows within a second. States: charging (green), on hold with a reason (orange), on battery (gray), fully charged (green), charger too weak (orange), a gray "Plugged in" for the moment before ioreg catches up with a plug, and a centered message when there's no battery or the data can't be read.
- **Refresh.** An `NSTimer` ticks every 1 s in common modes. SMC every tick; ioreg every 5 s and right after a plug change; `pmset -g battlimit` at most once a minute while on hold. All reading stops while the window is closed, minimized, hidden or fully covered.
- **Out of scope** (by agreement): alerts, charging controls, battery health, a Dock badge, a `.app` bundle.

## Data sources

**ioreg** (`ioreg -rw0 -a -c AppleSmartBattery`, parsed with `plistlib.loads(out)[0]`, about 17 ms per call):
- Pass `-a`: the XML plist keeps negative numbers, while the text output shows them as huge unsigned ones.
- Empty output means no battery (desktop Macs). `PowerTelemetryData` only exists on Apple Silicon.
- The values refresh only about every 60 s (compare `UpdateTime` with now).

| Field | Unit | Notes |
|---|---|---|
| `CurrentCapacity` | % | Matches the menu bar. |
| `IsCharging`, `ExternalConnected`, `FullyCharged` | bool | Plugged in doesn't mean charging: Optimized Battery Charging holds at ~80%. |
| `TimeRemaining`, `AvgTimeToFull`, `AvgTimeToEmpty` | min | 65535 = no estimate. |
| `AdapterDetails.Watts` | W | Negotiated: a "96W" adapter reports 94. Missing when unplugged. |
| `PowerTelemetryData.SystemPowerIn`, `.SystemLoad` | mW | Equal to each other while the battery is idle. |
| `PowerTelemetryData.BatteryPower` | mW, signed | Negative = discharging. |
| `ChargerData.NotChargingReason` | bits | Bit 24 = held by the charge limit (same as SMC `CHNC` bit 24). |
| `Voltage`, `InstantAmperage` | mV, mA signed | |

**pmset.** `pmset -g battlimit` (undocumented) prints `chargeSocLimitReason = optimizedBatteryCharging;` and `chargeSocLimitSoc = 80;`. Any other reason string is shown as "Charge limit".

**SMC** (read-only, verified on an M4 Pro, macOS 26):
- Works without sudo: ctypes to IOKit's `AppleSMC` service, selector 2, 80-byte struct. Send only the read commands 5 (read key), 8 (key by index) and 9 (key info), never 6 (write).
- Values tick once a second. `flt ` is little-endian; integers are little-endian when key-info attribute bit 0x04 is set.
- `PDTR` = power at the charger port (≈ `VD0R` × `ID0R`). `PSTR` = system load. `PDTR − PSTR` is about 1.3 W even with the battery idle, so it isn't battery power.
- Battery power = `B0AC` (mA, si16) × `B0AV` (mV, matches ioreg `Voltage`). The code assumes negative = discharging (like ioreg's `InstantAmperage`), but the sign is **unverified**: the battery sat idle at 80% the whole time. Check it once the Mac is charging or discharging.
- `BUIC` = battery %. `CHLT`'s first byte looks like the limit (0x50 = 80%). `PPBR` is *not* battery power (it's non-zero with the battery idle).

## PyObjC gotchas (verified on macOS 26, PyObjC 12)

- **Ctrl+C.** `AppHelper.runEventLoop()` skips its SIGINT handler once `NSApp` exists, so call `AppHelper.installMachInterrupt()` first. `terminate:` bypasses `atexit`; cleanup belongs in `applicationWillTerminate_`.
- **Activation.** `NSApp.activate()` doesn't bring the window forward when launched from a terminal; `activateIgnoringOtherApps_(True)` does.
- **Vibrancy.** Inside the frosted view, the effective appearance is `VibrantLight`/`VibrantDark`. Label colors drawn by a custom view that doesn't allow vibrancy come out *inverted*, so custom drawing (like `Bar`) picks plain colors from the appearance name.
- **Occlusion lag.** `windowDidChangeOcclusionState_` can arrive more than a second late, so close, minimize and reopen start and stop the timer directly.
- Every helper method on an `NSObject` subclass needs `@objc.python_method`.

## Checking changes

- **Logic.** `describe()` is pure. Feed it made-up ioreg dicts plus `live` dicts (or `None` for the fallback), one for each status.
- **Behavior.** Import `Battery` in a script, call `applicationDidFinishLaunching_`, pump the run loop, and check `d.timer` and `d.window.level()` around close, reopen, minimize and pin. Set `PYTHONDONTWRITEBYTECODE=1` so no `__pycache__` lands in the repo. For a launch check, run `uv run Battery.py` in its own process group and SIGINT the group: expect exit 0 in about 0.2 s and no output. `CFLOG_FORCE_STDERR=1` surfaces NSLog and PyObjC exceptions (plus a lot of system noise).
- **Looks.** `screencapture` needs Screen Recording permission, so capture in-process instead. Put your own backdrop window at level 1000 with the app window at 1001 above it, then grab that rect with Quartz `CGWindowListCreateImage(rect, kCGWindowListOptionOnScreenOnly, …)`; pyobjc-framework-Quartz goes in a scratch venv. `cacheDisplayInRect` and `CGWindowListCreateImageFromArray` both skip the blur, so use them only for layout.
