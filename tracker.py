import os
import json
import time
import serial
import requests
from skyfield.api import EarthSatellite, load, wgs84

# ── CelesTrak endpoint ────────────────────────────────────────────────────────
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"

# ── TLE cache settings ────────────────────────────────────────────────────────
TLE_CACHE_FILE    = "/tmp/tle_cache.json"
TLE_CACHE_MAX_AGE = 7200   # seconds (2 hours)

ts = load.timescale()


# =============================================================================
#  ARDUINO SERIAL
# =============================================================================

def open_serial(port: str, baudrate: int = 9600, timeout: float = 1.0) -> serial.Serial:
    """Open serial connection to Arduino."""
    try:
        ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        time.sleep(2)  # Wait for Arduino to reset after serial connection
        print(f"  [Serial] Connected to Arduino on {port} @ {baudrate} baud")
        return ser
    except serial.SerialException as e:
        raise RuntimeError(f"Cannot open serial port {port}: {e}")


def send_goto(ser: serial.Serial, azimuth: float, elevation: float):
    """
    Send a GOTO command to the Arduino rotator controller.
    Format: GOTO:<azimuth>:<elevation>\n
    Example: GOTO:182.45:34.12
    """
    # Clamp elevation to physically meaningful range
    elevation = max(0.0, min(90.0, elevation))
    azimuth   = azimuth % 360.0

    cmd = f"GOTO:{azimuth:.2f}:{elevation:.2f}\n"
    try:
        ser.write(cmd.encode("utf-8"))
        ser.flush()
        print(f"  [Serial] Sent → {cmd.strip()}")

        # Optional: read ACK from Arduino (remove if your firmware doesn't reply)
        ack = ser.readline().decode("utf-8", errors="replace").strip()
        if ack:
            print(f"  [Serial] ACK  ← {ack}")

    except serial.SerialException as e:
        print(f"  [Serial] ⚠️  Write error: {e}")


# =============================================================================
#  TLE FETCHER
# =============================================================================

def fetch_tle(norad_id: int):
    cache     = {}
    cache_key = str(norad_id)
    now       = time.time()

    if os.path.exists(TLE_CACHE_FILE):
        try:
            with open(TLE_CACHE_FILE, "r") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    if cache_key in cache:
        entry = cache[cache_key]
        age   = now - entry.get("fetched_at", 0)
        if age < TLE_CACHE_MAX_AGE:
            print(f"  [TLE] Cache hit for NORAD {norad_id} (age: {age/60:.0f} min)")
            return entry["name"], entry["tle1"], entry["tle2"]

    print(f"  [TLE] Fetching from CelesTrak (NORAD {norad_id})...")
    headers = {"User-Agent": "SatelliteTracker/1.0 (educational project)"}
    params  = {"CATNR": norad_id, "FORMAT": "tle"}

    try:
        resp = requests.get(CELESTRAK_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()

        if "No GP data found" in resp.text:
            raise ValueError(f"NORAD {norad_id} not found on CelesTrak.")

        lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
        if len(lines) < 2:
            raise ValueError(f"Unexpected TLE format for NORAD {norad_id}:\n{resp.text}")

        if not lines[0].startswith("1 "):
            name, tle1, tle2 = lines[0], lines[1], lines[2]
        else:
            name, tle1, tle2 = f"NORAD {norad_id}", lines[0], lines[1]

        cache[cache_key] = {"name": name, "tle1": tle1, "tle2": tle2, "fetched_at": now}
        try:
            with open(TLE_CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass

        print(f"  [TLE] OK — {name}")
        return name, tle1, tle2

    except requests.exceptions.Timeout:
        if cache_key in cache:
            entry = cache[cache_key]
            age   = now - entry.get("fetched_at", 0)
            print(f"  [TLE] Timeout. Using stale cache (age: {age/3600:.1f} h).")
            return entry["name"], entry["tle1"], entry["tle2"]
        raise RuntimeError("CelesTrak timed out and no cache available.")

    except Exception as exc:
        if cache_key in cache:
            entry = cache[cache_key]
            age   = now - entry.get("fetched_at", 0)
            print(f"  [TLE] Error: {exc}\n  Using stale cache (age: {age/3600:.1f} h).")
            return entry["name"], entry["tle1"], entry["tle2"]
        raise


# =============================================================================
#  SATELLITE
# =============================================================================

def build_satellite(norad_id: int) -> EarthSatellite:
    name, l1, l2 = fetch_tle(norad_id)
    return EarthSatellite(l1, l2, name, ts)


def get_satellite_data(sat: EarthSatellite, observer):
    t = ts.now()

    geocentric = sat.at(t)
    subpoint   = wgs84.subpoint(geocentric)

    difference  = sat - observer
    topocentric = difference.at(t)
    alt, az, distance = topocentric.altaz()

    return {
        "timestamp_utc" : t.utc_iso(),
        "latitude_deg"  : subpoint.latitude.degrees,
        "longitude_deg" : subpoint.longitude.degrees,
        "altitude_km"   : subpoint.elevation.km,
        "azimuth_deg"   : az.degrees,
        "elevation_deg" : alt.degrees,
        "distance_km"   : distance.km,
        "is_visible"    : alt.degrees > 0
    }


# =============================================================================
#  USER INPUT
# =============================================================================

def get_user_inputs():
    print("=" * 60)
    print("SATELLITE TRACKER - Configuration")
    print("=" * 60)

    while True:
        try:
            norad_id = int(input("\nNORAD ID (e.g. 25544 for ISS): "))
            if norad_id > 0:
                break
            print("❌ Must be positive.")
        except ValueError:
            print("❌ Please enter a valid number.")

    while True:
        try:
            refresh = float(input("Refresh interval in seconds (e.g. 5): "))
            if refresh > 0:
                break
            print("❌ Must be positive.")
        except ValueError:
            print("❌ Please enter a valid number.")

    print("\n📍 Observer position:")
    while True:
        try:
            lat = float(input("  Latitude  (e.g. 48.8566 for Paris): "))
            if -90 <= lat <= 90:
                break
            print("❌ Must be between -90 and 90.")
        except ValueError:
            print("❌ Please enter a valid number.")

    while True:
        try:
            lon = float(input("  Longitude (e.g. 2.3522 for Paris): "))
            if -180 <= lon <= 180:
                break
            print("❌ Must be between -180 and 180.")
        except ValueError:
            print("❌ Please enter a valid number.")

    print("\n🔌 Arduino serial port:")
    serial_port = input("  Port (e.g. COM3, /dev/ttyUSB0, /dev/ttyACM0): ").strip()

    while True:
        try:
            baudrate = int(input("  Baudrate (e.g. 9600): ") or "9600")
            if baudrate > 0:
                break
            print("❌ Must be positive.")
        except ValueError:
            print("❌ Please enter a valid number.")

    # Option to only send GOTO when satellite is above horizon
    visible_only = input("\nOnly send GOTO when satellite is visible? (y/N): ").strip().lower() == "y"

    return norad_id, refresh, lat, lon, serial_port, baudrate, visible_only


# =============================================================================
#  MAIN LOOP
# =============================================================================

def main():
    norad_id, refresh_seconds, lat, lon, serial_port, baudrate, visible_only = get_user_inputs()

    observer = wgs84.latlon(lat, lon)

    print("\n🛰️  Fetching satellite data...")
    try:
        sat = build_satellite(norad_id)
    except Exception as e:
        print(f"❌ Failed to load satellite: {e}")
        return

    print("\n🔌 Opening Arduino serial connection...")
    try:
        ser = open_serial(serial_port, baudrate)
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    print("\n" + "=" * 60)
    print(f"✅ Tracking : {sat.name} (NORAD {norad_id})")
    print(f"📍 Observer : {lat:.4f}°N, {lon:.4f}°E")
    print(f"⏱️  Refresh  : {refresh_seconds}s")
    print(f"🔌 Serial   : {serial_port} @ {baudrate} baud")
    print(f"🎯 GOTO mode: {'visible passes only' if visible_only else 'always'}")
    print("=" * 60)
    print("Press Ctrl+C to stop\n")

    try:
        while True:
            try:
                data = get_satellite_data(sat, observer)

                az   = data["azimuth_deg"]
                elev = data["elevation_deg"]
                vis  = data["is_visible"]
                status = "✅ VISIBLE" if vis else "❌ BELOW HORIZON"

                print(f"{data['timestamp_utc']}")
                print(
                    f"  Satellite : "
                    f"lat={data['latitude_deg']:7.3f}°, "
                    f"lon={data['longitude_deg']:7.3f}°, "
                    f"alt={data['altitude_km']:6.1f} km"
                )
                print(
                    f"  Observer  : "
                    f"az={az:6.2f}°, "
                    f"elev={elev:6.2f}°, "
                    f"dist={data['distance_km']:7.1f} km"
                )
                print(f"  Status    : {status}")

                # ── Send GOTO to Arduino ──────────────────────────────────────
                if not visible_only or vis:
                    send_goto(ser, az, elev)
                else:
                    print("  [Serial]  Skipped (satellite not visible)")

                print("-" * 60)
                time.sleep(refresh_seconds)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"❌ Error: {e}")
                time.sleep(refresh_seconds)

    except KeyboardInterrupt:
        print("\n\n🛑 Tracking stopped.")
    finally:
        if ser.is_open:
            ser.close()
            print("🔌 Serial port closed.")


if __name__ == "__main__":
    main()
