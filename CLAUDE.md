# Charging-APP

## What this is

`Battery.py` shows a Mac's charging info: the charger's wattage rating, the power the Mac is drawing from the charger, and the total power the Mac is using. Right now it prints one line to the terminal, overwriting it every second.

## Status

A GUI window version is being designed; the open questions are in **GUI design (in progress)** at the end of this file. Update this file when the GUI lands.

## Run

```sh
python3 Battery.py
```

- Uses only the standard library and runs only on macOS. Stop it with Ctrl+C.
- The line is overwritten with `\r`, so it only shows up properly in a real terminal (VS Code's integrated terminal works), not in VS Code's Output panel or through a pipe.

## Data source

`Battery.py` runs `ioreg -rw0 -a -c AppleSmartBattery` and parses the output with `plistlib.loads(out)[0]`.

- Always pass `-a` (XML plist output). Negative numbers come through correctly there, while ioreg's text output shows them as huge unsigned numbers.
- Empty output means there's no battery, as on desktop Macs. Check for it before parsing: `plistlib.loads(b"")` raises `plistlib.InvalidFileException`, which `Battery.py` doesn't handle yet.
- `PowerTelemetryData` only exists on Apple Silicon Macs.

## Field reference

Verified on an M4 Pro MacBook Pro running macOS 26.

| Field | Unit / type | Notes |
|---|---|---|
| `CurrentCapacity`, `MaxCapacity` | % | Battery % matching the menu bar. `MaxCapacity` is always 100 on Apple Silicon. |
| `AppleRawCurrentCapacity`, `AppleRawMaxCapacity`, `DesignCapacity`, `NominalChargeCapacity` | mAh | The raw current/max ratio doesn't equal the displayed % (e.g. 76% vs 80%). |
| `Voltage` | mV | |
| `Amperage`, `InstantAmperage` | mA, signed | Negative = discharging. |
| `Temperature` | 0.01 °C | 3028 = 30.28 °C. |
| `IsCharging`, `ExternalConnected`, `FullyCharged` | bool | Plugged in (`ExternalConnected`) doesn't mean charging. |
| `TimeRemaining`, `AvgTimeToFull`, `AvgTimeToEmpty` | minutes | 65535 = not available. |
| `CycleCount`, `DesignCycleCount9C` | cycles | `DesignCycleCount9C` is the rated cycle count (1000). |
| `AdapterDetails.Watts` | W | Negotiated figure: a "96W" adapter reports 94. Missing when unplugged. |
| `AdapterDetails.Name`, `AdapterDetails.Description` | text | `AdapterDetails` also holds the adapter's serial number (`SerialString`): never print or log it. |
| `PowerTelemetryData.SystemPowerIn` | mW | Power coming from the adapter. |
| `PowerTelemetryData.SystemLoad` | mW | What the Mac is using. |
| `PowerTelemetryData.BatteryPower` | mW, signed | Power into or out of the battery; negative = discharging. |
| `ChargerData.NotChargingReason` | int | Undocumented bitfield. |
| `UpdateTime` | Unix time (s) | When macOS last refreshed these values. |

## Gotchas

- **Stale data.** macOS refreshes these values only about once every 60 s (observed on AC power). Polling faster, as `Battery.py`'s 1 s loop does, re-reads the same numbers; compare `UpdateTime` with the current time to know how old the data is.
- **Plugged in isn't charging.** macOS Optimized Battery Charging can hold the battery at ~80% while `pmset -g batt` shows "AC attached; not charging". The code needs four states: charging, plugged in but not charging, on battery, and fully charged.
- **Battery health.** Sources disagree: System Information's "Maximum Capacity", the mAh ratios, and IOPS (IOPowerSources) `BatteryHealth`. Pick one formula and label it.
- **Other APIs fall short.** `psutil.sensors_battery()` and `pmset -g batt` only give %, whether it's plugged in, and time left. That isn't enough for wattage.
- **Python on macOS.** Bare `python3` can resolve to Apple's Command Line Tools Python 3.9, which uses the deprecated Tk 8.5, and Homebrew Python has no tkinter unless `python-tk` is installed. For GUI work, use a modern interpreter or a venv built from one.
- **GUI refresh.** Schedule refreshes with the GUI toolkit's timer instead of reusing the blocking `while True` + `time.sleep` loop, which would freeze the window.
- **Serial numbers.** ioreg output includes battery and adapter serial numbers (`Serial`, `BatteryData.Serial`, `AdapterDetails.SerialString`). Read fields by name and never print, log or commit these, including in saved ioreg dumps or test fixtures.

## GUI design (in progress)

The design interview for the window version is paused while `Battery.py` moves into this repo. Next step: get the user's answers to the open questions, then ask the next-round items. Don't write GUI code until the user confirms the whole design. Replace this section with real docs once the GUI lands.

### Settled

- Python. Any library is fine, including installing packages.
- The window only displays charging info: no alerts, no charging controls.
- It starts by running the script (the exact command is a next-round item).
- It has to look very clean.
- **Q2:** it updates live while open. How often is Q9.
- **Q6:** macOS on Apple Silicon only, since the data it reads only exists there.
- **Q5** (how the main number is drawn) is withdrawn until Q7 is answered.

### Open questions

Each lists the recommended option. Answer by filling in **Answer:** or in chat.

**Q1. Window type**
- (a) Regular app window with the red/yellow/green buttons.
- (b) Small widget with a title bar that stays on top of other windows.
- (c) Small widget with no title bar: a rounded card that stays on top, drags from anywhere, and closes with a small ×.
- Recommended: (c).
- **Answer:**

**Q3. Overall style**
- (a) Native macOS: system font (SF Pro) and colors, frosted-glass background like Control Center.
- (b) Custom modern: solid card, bold accent color, crisp flat shapes.
- (c) Ultra-minimal: almost no color, lots of empty space, thin lines.
- Recommended: (a).
- **Answer:**

**Q4. Light or dark**
- (a) Always dark.
- (b) Always light.
- (c) Follow the macOS appearance setting (currently Auto on the author's Mac).
- Recommended: (c).
- **Answer:**

**Q7. What's on screen**
- (a) Just the script's three numbers: charger rating, power in, system load.
- (b) Those three plus the charging basics: battery %, status (charging / on hold / on battery / full), power into or out of the battery, and time to full or empty when macOS provides it.
- (c) Everything in (b) plus battery health: capacity compared with new, cycle count, temperature.
- (d) Everything, including raw voltage and current.
- Recommended: (b). The status can say why it isn't charging, e.g. "On hold at 80% · Optimized Battery Charging". `pmset -g battlimit` (undocumented) shows the limit and its reason.
- **Answer:**

**Q8. What happens to Battery.py**
- (a) Add a new file next to it (e.g. `battery_window.py`) and leave `Battery.py` as is.
- (b) `Battery.py` becomes the window app. Commit the terminal version first so git history keeps it.
- (c) `Battery.py` does both: the window by default, the old terminal line with `--terminal`.
- Recommended: (b).
- **Answer:**

**Q9. How live**
- (a) Once a minute: standard ioreg data only. Simplest and least likely to break.
- (b) Every second: watts from the SMC, everything else from ioreg. Falls back to once a minute automatically if the SMC can't be read.
- Recommended: (b).
- **Answer:**

SMC facts for (b), verified read-only on an M4 Pro:
- Works without sudo: ctypes to IOKit's `AppleSMC` service, selector 2, 80-byte struct. Send only the read commands 5 (read key), 8 (key by index) and 9 (key info), never 6 (write).
- Values tick once a second; polling faster gives nothing new.
- `flt ` values are little-endian. Integers are little-endian when the key-info attribute has bit 0x04 set, big-endian otherwise.
- Keys: `PDTR` charger power (W), `PSTR` system total power (W), `VD0R`/`ID0R` charger volts/amps, `B0AC` battery current (mA, signed; sign not yet verified), `BUIC` battery % as shown in the menu bar, `CHNC` bit 24 = charging stopped by the charge limit. `CHLT`'s first byte appears to be the limit (0x50 = 80%).
- `PPBR` is not battery power (it's non-zero while the battery is idle), despite some apps labelling it that way.

### Next round (after the answers)

- **Q5 again:** which number is the headline, and how it's drawn (ring, bar or big number).
- **GUI library.** Candidates: PySide6 (Qt), PyObjC (native AppKit, including the frosted-glass effect), pywebview (HTML/CSS in a native window), CustomTkinter.
- **Interpreter, run command and dependencies.** `.venv/` is already gitignored. On the author's Mac: bare `python3` and VS Code's default interpreter are Apple's Python 3.9 (Tk 8.5); the Anaconda base Python 3.13 already has PySide6 6.9 and PyObjC 12.1; uv is installed; Homebrew's Python 3.14 has no tkinter.
- **Window details:** size, position, Dock icon, how to quit.
- **Status wording and colors.**
- **README:** whether to add run instructions.
- **Only if Q7 is (c) or (d):** which battery-health formula to show, and °C or °F.
