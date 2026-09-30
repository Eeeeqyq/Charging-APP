import plistlib, subprocess, time

while True:
    out = subprocess.run(["ioreg", "-rw0", "-a", "-c", "AppleSmartBattery"], capture_output=True).stdout
    b = plistlib.loads(out)[0]
    t = b.get("PowerTelemetryData", {})
    ac = b.get("AdapterDetails", {})
    print(f"Charger: {ac.get('Watts', '?')} W | Power in: {t.get('SystemPowerIn', 0)/1000:.1f} W | "
          f"System load: {t.get('SystemLoad', 0)/1000:.1f} W", end="\r")
    time.sleep(1)