# Charging-APP

[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![Platform: macOS (Apple Silicon)](https://img.shields.io/badge/platform-macOS%20%28Apple%20Silicon%29-lightgrey)

A small Python script that shows your MacBook's charger wattage and power draw on one line in the terminal.

## What it shows

`Battery.py` prints a single line and redraws it in place about once a second:

```text
Charger: 94 W | Power in: 12.1 W | System load: 12.1 W
```

- **Charger**: the charger's wattage rating as macOS reports it. This is the negotiated value, which can be a little under the number printed on the adapter (a 96 W adapter shows 94 W). It shows `?` when no charger is connected.
- **Power in**: the power the Mac is drawing from the charger.
- **System load**: the total power the Mac is using.

## Requirements

- macOS on a MacBook with Apple Silicon. On Intel Macs, Power in and System load always read 0.0 W, because the telemetry they come from only exists on Apple Silicon.
- Python 3.7 or newer. The script uses only the standard library, so there's nothing to install; the `python3` that comes with Apple's Command Line Tools works.
- A real terminal, such as the macOS Terminal app or VS Code's integrated terminal. The line is redrawn with a carriage return (`\r`), so it doesn't display properly in VS Code's Output panel or through a pipe.

Tested on an M4 Pro MacBook Pro running macOS 26.

## Usage

```sh
git clone https://github.com/Eeeeqyq/Charging-APP.git
cd Charging-APP
python3 Battery.py
```

Stop it with **Ctrl+C**. The script doesn't catch the interrupt, so Python prints a `KeyboardInterrupt` traceback as it exits; that's harmless.

## How it works

About once a second, `Battery.py` runs:

```sh
ioreg -rw0 -a -c AppleSmartBattery
```

This prints the battery's entry in the macOS I/O Registry as an XML property list (`-a`). The script parses it with Python's `plistlib`, reads three fields by name, converts the power readings from milliwatts to watts, and overwrites the previous line:

| Shown as | ioreg field | Unit in ioreg |
| --- | --- | --- |
| Charger | `AdapterDetails.Watts` | W |
| Power in | `PowerTelemetryData.SystemPowerIn` | mW |
| System load | `PowerTelemetryData.SystemLoad` | mW |

macOS refreshes these values only about once every 60 seconds (observed on AC power), so the line is redrawn every second but the numbers usually change about once a minute.

## Limitations

- **Doesn't work on desktop Macs.** They have no battery, so `ioreg` returns nothing and the script exits with a `plistlib.InvalidFileException` traceback.
- **Readings can be stale.** A reading can be up to about a minute old, and the line doesn't show its age.
- **Plugged in doesn't mean charging.** The script doesn't show whether the battery is charging, and a non-zero Power in doesn't prove it is: macOS Optimized Battery Charging can hold the battery at around 80% while the charger keeps running the Mac. The example above was captured with the battery at 80% and not charging, which is why Power in equals System load. Run `pmset -g batt` to see the charging state.
- **Leftover characters.** The line isn't cleared before it's redrawn, so when a reading gets shorter (say, 10.2 W dropping to 9.8 W), a stray character from the previous line can stay at the end.

## Roadmap

A windowed (GUI) version is being designed; nothing has been built yet.

## License

Released under the [MIT License](LICENSE).
