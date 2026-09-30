# /// script
# requires-python = ">=3.14"
# dependencies = ["pyobjc-framework-Cocoa>=12"]
# ///
"""Charging: a small macOS window showing the power the Mac takes from its charger,
what the battery is doing, and the total system load.

Run it with `uv run Battery.py`. `python3 Battery.py` works too: without PyObjC it
relaunches itself through uv. macOS on Apple Silicon only.
"""
import ctypes
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import time

try:
    import objc
    from AppKit import (
        NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSAttributedString, NSBackingStoreBuffered,
        NSBaselineOffsetAttributeName, NSBezierPath, NSBox, NSBoxSeparator, NSButton, NSButtonTypeToggle, NSColor,
        NSControlStateValueOn, NSFloatingWindowLevel, NSFont, NSFontAttributeName, NSFontWeightMedium,
        NSFontWeightRegular, NSFontWeightSemibold, NSForegroundColorAttributeName, NSGradient, NSImage,
        NSImageOnly, NSImageSymbolConfiguration, NSLayoutAttributeLeading, NSLayoutConstraintOrientationHorizontal,
        NSLayoutPriorityDefaultHigh, NSLineBreakByTruncatingTail, NSMenu,
        NSMenuItem, NSMutableAttributedString, NSMutableParagraphStyle, NSNormalWindowLevel,
        NSParagraphStyleAttributeName, NSStackView, NSStackViewDistributionEqualSpacing, NSTextAlignmentCenter,
        NSTextField, NSTitlebarSeparatorStyleNone, NSUserInterfaceLayoutOrientationVertical, NSView,
        NSViewNoIntrinsicMetric, NSVisualEffectBlendingModeBehindWindow, NSVisualEffectMaterialMenu,
        NSVisualEffectStateActive, NSVisualEffectView, NSWindow, NSWindowOcclusionStateVisible,
        NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView, NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskTitled, NSWindowTitleHidden,
    )
    from Foundation import (
        NSBundle, NSMakeRect, NSMakeSize, NSMeasurement, NSMeasurementFormatter, NSObject, NSRunLoop,
        NSRunLoopCommonModes, NSTimer, NSUnitTemperature,
    )
    from PyObjCTools import AppHelper
except ImportError as error:
    # No PyObjC here (e.g. Apple's python3): relaunch through uv, which installs
    # the dependencies listed at the top of this file.
    if shutil.which("uv") and not os.environ.get("CHARGING_RELAUNCHED"):
        os.environ["CHARGING_RELAUNCHED"] = "1"
        os.execvp("uv", ["uv", "run", "--quiet", os.path.abspath(__file__)] + sys.argv[1:])
    sys.exit(f"Battery.py needs PyObjC ({error}). Install uv (https://docs.astral.sh/uv/), then: uv run Battery.py")

WIDTH = 300                      # window width, points
PAD, TOP, BOTTOM = 20, 40, 14    # content insets; TOP clears the traffic lights
IOREG_EVERY = 5                  # seconds between ioreg reads; macOS refreshes them about once a minute
NOT_AVAILABLE = 65535            # ioreg's "no estimate" for times
CHARGE_LIMIT_HOLD = 1 << 24      # ChargerData.NotChargingReason bit: held by the charge limit

# The expandable details section: (section title, [(key in describe()["details"], row title)]).
DETAILS = [("Charger", [("adapter", "Adapter"), ("input", "Input")]),
           ("Battery", [("voltage", "Voltage"), ("current", "Current"), ("temperature", "Temperature"),
                        ("cycles", "Cycle count"), ("capacity", "Maximum capacity")])]


# --- SMC: live watts, ticking once a second ---------------------------------
# Read-only: only command 9 (key info) and command 5 (read key) are ever sent.

class _Vers(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint8), ("minor", ctypes.c_uint8), ("build", ctypes.c_uint8),
                ("reserved", ctypes.c_uint8), ("release", ctypes.c_uint16)]


class _PLimit(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16), ("cpu", ctypes.c_uint32),
                ("gpu", ctypes.c_uint32), ("mem", ctypes.c_uint32)]


class _KeyInfo(ctypes.Structure):
    _fields_ = [("dataSize", ctypes.c_uint32), ("dataType", ctypes.c_uint32), ("dataAttributes", ctypes.c_uint8)]


class _KeyData(ctypes.Structure):  # 80 bytes, as AppleSMC's struct method expects
    _fields_ = [("key", ctypes.c_uint32), ("vers", _Vers), ("pLimitData", _PLimit), ("keyInfo", _KeyInfo),
                ("result", ctypes.c_uint8), ("status", ctypes.c_uint8), ("data8", ctypes.c_uint8),
                ("data32", ctypes.c_uint32), ("bytes", ctypes.c_uint8 * 32)]


class SMC:
    def __init__(self):
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOServiceMatching.restype = ctypes.c_void_p
        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
        iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        iokit.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                        ctypes.POINTER(ctypes.c_uint32)]
        iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
        iokit.IOConnectCallStructMethod.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                                    ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
        if not service:
            raise OSError("no AppleSMC service")
        task = ctypes.c_uint32.in_dll(ctypes.CDLL("/usr/lib/libSystem.B.dylib"), "mach_task_self_").value
        connection = ctypes.c_uint32()
        result = iokit.IOServiceOpen(service, task, 0, ctypes.byref(connection))
        iokit.IOObjectRelease(service)
        if result:
            raise OSError(f"IOServiceOpen failed: {result:#x}")
        self._iokit, self._connection, self._info = iokit, connection.value, {}

    def _call(self, key, command, size=0):
        request, reply = _KeyData(key=int.from_bytes(key.encode(), "big"), data8=command), _KeyData()
        request.keyInfo.dataSize = size
        reply_size = ctypes.c_size_t(ctypes.sizeof(reply))
        result = self._iokit.IOConnectCallStructMethod(self._connection, 2, ctypes.byref(request),
                                                       ctypes.sizeof(request), ctypes.byref(reply),
                                                       ctypes.byref(reply_size))
        if result or reply.result:
            raise OSError(f"SMC {key}: {result:#x}/{reply.result}")
        return reply

    def read(self, key):
        if key not in self._info:
            info = self._call(key, 9).keyInfo
            self._info[key] = (info.dataSize, info.dataType.to_bytes(4, "big").decode(), info.dataAttributes)
        size, kind, attributes = self._info[key]
        raw = bytes(self._call(key, 5, size).bytes[:size])
        if kind == "flt ":
            return struct.unpack("<f", raw)[0]
        if kind[:2] in ("ui", "si"):
            return int.from_bytes(raw, "little" if attributes & 0x04 else "big", signed=kind[:2] == "si")
        raise OSError(f"SMC {key}: unsupported type {kind!r}")

    def live(self):
        """Watts from the charger, used by the system and into the battery (negative = out of it),
        plus the volts and amps behind them."""
        battery_ma, battery_mv = self.read("B0AC"), self.read("B0AV")
        readings = {"in": self.read("PDTR"), "load": self.read("PSTR"), "battery": battery_ma * battery_mv / 1e6,
                    "battery_v": battery_mv / 1000, "battery_a": battery_ma / 1000}
        try:
            readings["charger"] = (self.read("VD0R"), self.read("ID0R"))
        except OSError:
            readings["charger"] = None  # only feeds the details section; keep the live watts regardless
        return readings


# --- ioreg and pmset: everything else ----------------------------------------

def read_battery():
    """The AppleSmartBattery ioreg entry, or None on a Mac without a battery."""
    out = subprocess.run(["ioreg", "-rw0", "-a", "-c", "AppleSmartBattery"], capture_output=True, check=True).stdout
    return plistlib.loads(out)[0] if out.strip() else None


def charge_limit_reason():
    """Why macOS is holding the charge, from `pmset -g battlimit` (undocumented), or None."""
    try:
        out = subprocess.run(["pmset", "-g", "battlimit"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"chargeSocLimitReason = (\w+);", out)
    if not match:
        return None
    return "Optimized Battery Charging" if match.group(1) == "optimizedBatteryCharging" else "Charge limit"


# --- What the window says ------------------------------------------------------

TEMPERATURE = NSMeasurementFormatter.alloc().init()  # °C or °F, as set in Language & Region
TEMPERATURE.numberFormatter().setMaximumFractionDigits_(1)


def minutes(battery, *keys):
    for key in keys:
        value = battery.get(key)
        if isinstance(value, int) and 0 < value < NOT_AVAILABLE:
            return value
    return None


def duration(total):
    hours, mins = divmod(total, 60)
    if not hours:
        return f"{mins} min"
    return f"{hours} h {mins} min" if mins else f"{hours} h"


def signed(value, unit, digits=1):
    text = f"{abs(value):.{digits}f} {unit}"
    if not round(value, digits):
        return text
    return ("+" if value > 0 else "−") + text


def updated_ago(seconds):
    seconds = max(0, int(seconds))
    return f"Updated {seconds} s ago" if seconds < 60 else f"Updated {seconds // 60} min ago"


def describe(battery, live, now, hold_reason=None):
    """What to show, from the ioreg entry plus live SMC readings (None when falling back to ioreg only)."""
    telemetry = battery.get("PowerTelemetryData", {})
    reported_plugged = bool(battery.get("ExternalConnected"))
    if live:
        power_in, load, into_battery = live["in"], live["load"], live["battery"]
        volts, amps = live["battery_v"], live["battery_a"]
        plugged = power_in > 0.5  # live, so unplugging shows up within a second
    else:
        power_in = telemetry.get("SystemPowerIn", 0) / 1000
        load = telemetry.get("SystemLoad", 0) / 1000
        into_battery = telemetry.get("BatteryPower", 0) / 1000
        volts, amps = battery.get("Voltage", 0) / 1000, battery.get("InstantAmperage", 0) / 1000
        plugged = reported_plugged
    percent = battery.get("CurrentCapacity", 0)
    adapter = battery.get("AdapterDetails", {}) if plugged else {}
    charger = adapter.get("Watts")

    if not plugged:
        left = None if reported_plugged else minutes(battery, "AvgTimeToEmpty", "TimeRemaining")
        status, color = "On battery" + (f" · {duration(left)} left" if left else ""), "gray"
    elif not reported_plugged:  # ioreg hasn't caught up with the plug yet
        status, color = "Plugged in", "gray"
    elif battery.get("FullyCharged"):
        status, color = "Fully charged", "green"
    elif battery.get("IsCharging"):
        to_full = minutes(battery, "AvgTimeToFull", "TimeRemaining")
        status, color = "Charging" + (f" · {duration(to_full)} to full" if to_full else ""), "green"
    elif telemetry.get("BatteryPower", 0) < -500:
        status, color = "Plugged in · Charger too weak, using battery", "orange"
    else:
        held = battery.get("ChargerData", {}).get("NotChargingReason", 0) & CHARGE_LIMIT_HOLD
        status = f"On hold at {percent}%" + (f" · {hold_reason or 'Charge limit'}" if held else "")
        color = "orange"

    charger_input = live.get("charger") if live and plugged else None
    cycles, rated = battery.get("CycleCount"), battery.get("DesignCycleCount9C")
    capacity = battery.get("BatteryData", {}).get("MaxCapacity")  # matches System Information's "Maximum Capacity"
    celsius = battery.get("Temperature")  # hundredths of a degree
    details = {
        "adapter": adapter.get("Name"),
        "input": f"{charger_input[0]:.1f} V · {charger_input[1]:.2f} A" if charger_input else None,
        "voltage": f"{volts:.2f} V",
        "current": signed(amps, "A", 2),
        "temperature": TEMPERATURE.stringFromMeasurement_(NSMeasurement.alloc().initWithDoubleValue_unit_(
            celsius / 100, NSUnitTemperature.celsius())) if isinstance(celsius, int) else None,
        "cycles": (f"{cycles:,} of {rated:,}" if rated else f"{cycles:,}") if isinstance(cycles, int) else None,
        "capacity": f"{capacity}%" if isinstance(capacity, int) else None,
    }

    return {
        "headline": power_in if plugged else abs(into_battery),
        "subtitle": (f"of {charger} W charger" if charger else "from charger") if plugged else "from battery",
        "bar": min(max(power_in / charger, 0.0), 1.0) if charger else None,
        "status": status,
        "color": color,
        "battery": f"{percent}%",
        "into_battery": signed(into_battery, "W") if plugged else None,
        "load": f"{load:.1f} W" if plugged else None,
        "footer": "" if live else updated_ago(now - battery.get("UpdateTime", now)),
        "details": details,
    }


# --- Window ------------------------------------------------------------------

STATUS_COLORS = {"green": NSColor.systemGreenColor, "orange": NSColor.systemOrangeColor,
                 "gray": NSColor.systemGrayColor}


def styled(text, size, weight, color, baseline=0):
    attributes = {NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight),
                  NSForegroundColorAttributeName: color}
    if baseline:
        attributes[NSBaselineOffsetAttributeName] = baseline
    return NSAttributedString.alloc().initWithString_attributes_(text, attributes)


def joined(*parts):
    out = NSMutableAttributedString.alloc().init()
    for part in parts:
        out.appendAttributedString_(part)
    return out


def status_lines(text, color):
    """The status line with its colored dot. When it doesn't fit, the part after the first " · "
    moves to a second, quieter line; the second value is None when it all fits on one."""
    dot = styled("●  ", 10, NSFontWeightRegular, color, baseline=1)
    line = joined(dot, styled(text, 13, NSFontWeightMedium, NSColor.labelColor()))
    if line.size().width <= WIDTH - 2 * PAD or " · " not in text:
        return line, None
    head, note = text.split(" · ", 1)
    indent = NSMutableParagraphStyle.alloc().init()
    indent.setFirstLineHeadIndent_(dot.size().width)
    note = NSAttributedString.alloc().initWithString_attributes_(note, {
        NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(12, NSFontWeightRegular),
        NSForegroundColorAttributeName: NSColor.secondaryLabelColor(), NSParagraphStyleAttributeName: indent})
    return joined(dot, styled(head, 13, NSFontWeightMedium, NSColor.labelColor())), note


def label(size, weight=NSFontWeightRegular, color=None):
    field = NSTextField.labelWithString_("")
    field.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight))
    field.setLineBreakMode_(NSLineBreakByTruncatingTail)
    if color is not None:
        field.setTextColor_(color)
    return field


def stack(views, vertical=False, spacing=8):
    view = NSStackView.stackViewWithViews_(views)
    if vertical:
        view.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        view.setAlignment_(NSLayoutAttributeLeading)
    else:
        view.setDistribution_(NSStackViewDistributionEqualSpacing)  # first view left, last view right
    view.setSpacing_(spacing)
    return view


def row(title, size):
    """A title on the left and its value on the right. Returns (row, value field)."""
    name = label(size, color=NSColor.secondaryLabelColor())
    name.setStringValue_(title)
    value = label(size, NSFontWeightMedium)
    return stack([name, value]), value


def icon(name, description):
    config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(12, NSFontWeightMedium)
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, description)
    return image.imageWithSymbolConfiguration_(config)


def icon_button(symbol, tooltip, target, action, toggled=None):
    """A borderless SF Symbol button. With `toggled`, an on/off switch that shows that symbol when on."""
    button = NSButton.alloc().init()
    button.setBordered_(False)
    button.setImagePosition_(NSImageOnly)
    button.setImage_(icon(symbol, tooltip))
    if toggled:
        button.setButtonType_(NSButtonTypeToggle)
        button.setAlternateImage_(icon(toggled, tooltip))
    button.setContentTintColor_(NSColor.secondaryLabelColor())
    button.setToolTip_(tooltip)
    button.setTarget_(target)
    button.setAction_(action)
    return button


def app_icon():
    """A green tile with a white bolt, drawn at runtime since there's no .app bundle to hold an icon."""
    def draw(rect):
        tile = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(50, 50, 412, 412), 92, 92)
        NSGradient.alloc().initWithStartingColor_endingColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.36, 0.85, 0.45, 1.0),
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.11, 0.60, 0.28, 1.0),
        ).drawInBezierPath_angle_(tile, -90)
        config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(220, NSFontWeightSemibold)
        config = config.configurationByApplyingConfiguration_(
            NSImageSymbolConfiguration.configurationWithPaletteColors_([NSColor.whiteColor()]))
        bolt = NSImage.imageWithSystemSymbolName_accessibilityDescription_("bolt.fill", None)
        bolt = bolt.imageWithSymbolConfiguration_(config)
        width, height = bolt.size().width, bolt.size().height
        bolt.drawInRect_(NSMakeRect((512 - width) / 2, (512 - height) / 2, width, height))
        return True
    return NSImage.imageWithSize_flipped_drawingHandler_(NSMakeSize(512, 512), False, draw)


class Bar(NSView):
    """Thin rounded meter for the share of the charger's rating in use."""
    fraction = 0.0
    color = None

    def intrinsicContentSize(self):
        return NSMakeSize(NSViewNoIntrinsicMetric, 6)

    def viewDidChangeEffectiveAppearance(self):
        objc.super(Bar, self).viewDidChangeEffectiveAppearance()
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        radius = bounds.size.height / 2
        # Label colors inside the frosted view are made for vibrancy blending, which this view
        # doesn't use (the fill has to keep its color), so the track gets a plain translucent color.
        dark = "Dark" in self.effectiveAppearance().name()
        NSColor.colorWithWhite_alpha_(1.0 if dark else 0.0, 0.18 if dark else 0.1).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius).fill()
        if self.fraction > 0 and self.color is not None:
            width = max(bounds.size.height, bounds.size.width * self.fraction)
            self.color.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(0, 0, width, bounds.size.height), radius, radius).fill()


class App(NSObject):
    """App delegate, window delegate and refresh-timer target."""

    def applicationDidFinishLaunching_(self, notification):
        self.timer, self.expanded = None, False
        self.battery, self.error, self.read_at, self.plugged = None, None, 0.0, None
        self.reason, self.reason_at = None, 0.0
        try:
            self.smc = SMC()
        except OSError:
            self.smc = None  # falls back to ioreg only
        self.build_menu()
        self.build_window()
        self.start(animate=False)
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)  # NSApp.activate() doesn't bring it forward from a terminal

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False  # closing the window keeps the app in the Dock

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        if not visible:
            self.window.makeKeyAndOrderFront_(None)
            self.start()
        return True

    # No reading while the window can't be seen. Occlusion notifications can lag by more than a
    # second, so closing and minimizing are handled directly.
    def windowWillClose_(self, notification):
        self.stop()

    def windowDidMiniaturize_(self, notification):
        self.stop()

    def windowDidDeminiaturize_(self, notification):
        self.start()

    def windowDidChangeOcclusionState_(self, notification):
        # Fully covered, app hidden, screen locked.
        if self.window.occlusionState() & NSWindowOcclusionStateVisible:
            self.start()
        else:
            self.stop()

    def tick_(self, timer):
        self.refresh()

    def togglePin_(self, sender):
        pinned = sender.state() == NSControlStateValueOn
        self.window.setLevel_(NSFloatingWindowLevel if pinned else NSNormalWindowLevel)
        sender.setContentTintColor_(NSColor.controlAccentColor() if pinned else NSColor.secondaryLabelColor())

    def toggleDetails_(self, sender):
        self.expanded = sender.state() == NSControlStateValueOn
        sender.setToolTip_("Hide details" if self.expanded else "Show details")
        self.refresh()  # shows or hides the section and animates the height

    @objc.python_method
    def start(self, animate=True):
        if self.timer is None:
            self.read_at = 0.0  # data may be stale after a pause
            self.refresh(animate)
            self.timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, "tick:", None, True)
            self.timer.setTolerance_(0.1)
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)

    @objc.python_method
    def stop(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None

    @objc.python_method
    def refresh(self, animate=True):
        now = time.time()
        live = None
        if self.smc is not None:
            try:
                live = self.smc.live()
            except OSError:
                pass
        plugged = live["in"] > 0.5 if live else None
        if now - self.read_at >= IOREG_EVERY:
            self.reload(now)
        elif plugged != self.plugged:
            self.reload(now)
            self.read_at = now - IOREG_EVERY + 2  # read again soon in case ioreg lags the plug
        self.plugged = plugged
        if self.battery is None:
            self.show_message(self.error)
        else:
            self.show(describe(self.battery, live, now, self.hold_reason(now)))
        self.fit(animate)

    @objc.python_method
    def reload(self, now):
        self.read_at = now
        try:
            self.battery = read_battery()
            self.error = None if self.battery is not None else "No battery found"
        except Exception:
            self.battery, self.error = None, "Can't read battery data"

    @objc.python_method
    def hold_reason(self, now):
        held = self.battery.get("ChargerData", {}).get("NotChargingReason", 0) & CHARGE_LIMIT_HOLD
        if held and now - self.reason_at >= 60:
            self.reason, self.reason_at = charge_limit_reason(), now
        return self.reason if held else None

    @objc.python_method
    def show(self, view):
        color = STATUS_COLORS[view["color"]]()
        self.message.setHidden_(True)
        for part in self.parts:
            part.setHidden_(part is self.bar and view["bar"] is None)
        self.headline.setAttributedStringValue_(joined(
            styled(f"{view['headline']:.1f}", 44, NSFontWeightSemibold, NSColor.labelColor()),
            styled(" W", 22, NSFontWeightMedium, NSColor.secondaryLabelColor())))
        self.subtitle.setStringValue_(view["subtitle"])
        if view["bar"] is not None:
            self.bar.fraction, self.bar.color = view["bar"], color
            self.bar.setNeedsDisplay_(True)
        line, note = status_lines(view["status"], color)
        self.status.setAttributedStringValue_(line)
        self.status_note.setHidden_(note is None)
        if note is not None:
            self.status_note.setAttributedStringValue_(note)
        self.values["battery"].setStringValue_(view["battery"])
        for key, row_view in self.rows.items():
            row_view.setHidden_(view[key] is None)
            if view[key] is not None:
                self.values[key].setStringValue_(view[key])
        self.footer.setStringValue_(view["footer"])

        self.details.setHidden_(not self.expanded)
        for title, fields in DETAILS:
            values = [view["details"][key] for key, _ in fields]
            self.sections[title].setHidden_(all(value is None for value in values))
            for (key, _), value in zip(fields, values):
                self.detail_rows[key].setHidden_(value is None)
                if value is not None:
                    self.detail_values[key].setStringValue_(value)

    @objc.python_method
    def show_message(self, text):
        for part in self.parts + [self.details]:
            part.setHidden_(True)
        self.message.setStringValue_(text)
        self.message.setHidden_(False)

    @objc.python_method
    def fit(self, animate):
        """Match the window's height to its content, keeping the top edge in place."""
        self.window.contentView().layoutSubtreeIfNeeded()
        height = TOP + self.content.fittingSize().height + BOTTOM
        frame = self.window.frame()
        if abs(frame.size.height - height) >= 0.5:
            top = frame.origin.y + frame.size.height
            self.window.setFrame_display_animate_(NSMakeRect(frame.origin.x, top - height, WIDTH, height),
                                                  True, animate)

    @objc.python_method
    def build_menu(self):
        menus = [("Charging", [("Hide Charging", "hide:", "h"), None, ("Quit Charging", "terminate:", "q")]),
                 ("Window", [("Minimize", "performMiniaturize:", "m"), ("Close", "performClose:", "w")])]
        bar = NSMenu.alloc().init()
        for title, items in menus:
            menu = NSMenu.alloc().initWithTitle_(title)
            for item in items:
                menu.addItem_(NSMenuItem.separatorItem() if item is None
                              else NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(*item))
            holder = NSMenuItem.alloc().init()
            holder.setSubmenu_(menu)
            bar.addItem_(holder)
        NSApp.setMainMenu_(bar)

    @objc.python_method
    def build_window(self):
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                 | NSWindowStyleMaskFullSizeContentView)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, 240), style, NSBackingStoreBuffered, False)
        window.setTitle_("Charging")
        window.setTitleVisibility_(NSWindowTitleHidden)
        window.setTitlebarAppearsTransparent_(True)
        window.setTitlebarSeparatorStyle_(NSTitlebarSeparatorStyleNone)
        window.setMovableByWindowBackground_(True)
        window.setReleasedWhenClosed_(False)
        window.setRestorable_(False)  # every launch starts centered, unpinned and collapsed
        window.setDelegate_(self)
        glass = NSVisualEffectView.alloc().init()
        glass.setMaterial_(NSVisualEffectMaterialMenu)
        glass.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        glass.setState_(NSVisualEffectStateActive)  # stay frosted when another app is in front
        window.setContentView_(glass)

        self.message = label(13, color=NSColor.secondaryLabelColor())
        self.message.setAlignment_(NSTextAlignmentCenter)
        self.headline = label(44, NSFontWeightSemibold)
        self.subtitle = label(13, color=NSColor.secondaryLabelColor())
        self.bar = Bar.alloc().init()
        self.status = label(13)
        self.status_note = label(12, color=NSColor.secondaryLabelColor())
        self.values, rows = {}, {}
        for key, title in (("battery", "Battery"), ("into_battery", "Into battery"), ("load", "System load")):
            rows[key], self.values[key] = row(title, 13)
        self.rows = {key: rows[key] for key in ("into_battery", "load")}  # hidden on battery

        line = NSBox.alloc().init()
        line.setBoxType_(NSBoxSeparator)
        self.detail_rows, self.detail_values, self.sections = {}, {}, {}
        for title, fields in DETAILS:
            header = label(11, NSFontWeightSemibold, NSColor.secondaryLabelColor())
            header.setStringValue_(title)
            for key, name in fields:
                self.detail_rows[key], self.detail_values[key] = row(name, 12)
            self.sections[title] = stack([header] + [self.detail_rows[key] for key, _ in fields],
                                         vertical=True, spacing=4)
        self.details = stack([line] + list(self.sections.values()), vertical=True, spacing=10)

        self.footer = label(11, color=NSColor.tertiaryLabelColor())
        buttons = stack([icon_button("chevron.down", "Show details", self, "toggleDetails:", toggled="chevron.up"),
                         icon_button("pin", "Keep on top", self, "togglePin:", toggled="pin.fill"),
                         icon_button("power", "Quit Charging", None, "terminate:")], spacing=12)
        buttons.setHuggingPriority_forOrientation_(NSLayoutPriorityDefaultHigh, NSLayoutConstraintOrientationHorizontal)
        bottom = stack([self.footer, buttons])

        status = stack([self.status, self.status_note], vertical=True, spacing=2)
        table = stack(list(rows.values()), vertical=True, spacing=6)
        self.parts = [self.headline, self.subtitle, self.bar, status, table, bottom]
        self.content = stack([self.message] + self.parts[:-1] + [self.details, bottom], vertical=True, spacing=0)
        self.content.setTranslatesAutoresizingMaskIntoConstraints_(False)
        for view, space in ((self.message, 0), (self.subtitle, 12), (self.bar, 16), (status, 16), (table, 12),
                            (self.details, 12)):
            self.content.setCustomSpacing_afterView_(space, view)
        glass.addSubview_(self.content)
        self.content.leadingAnchor().constraintEqualToAnchor_constant_(glass.leadingAnchor(), PAD).setActive_(True)
        self.content.trailingAnchor().constraintEqualToAnchor_constant_(glass.trailingAnchor(), -PAD).setActive_(True)
        self.content.topAnchor().constraintEqualToAnchor_constant_(glass.topAnchor(), TOP).setActive_(True)
        full_width = [self.message, self.bar, status, self.status, self.status_note, table, bottom, self.details, line]
        full_width += list(rows.values()) + list(self.sections.values()) + list(self.detail_rows.values())
        for view in full_width:
            view.widthAnchor().constraintEqualToAnchor_(self.content.widthAnchor()).setActive_(True)
        self.window = window


def main():
    # Framework builds of Python (like Homebrew's) take this as the app name; others show "python".
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = "Charging"
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    app.setApplicationIconImage_(app_icon())
    delegate = App.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.installMachInterrupt()  # Ctrl+C in the terminal quits; runEventLoop skips this once NSApp exists
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
