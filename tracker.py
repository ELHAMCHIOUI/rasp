import os
import json
import time
import requests
from skyfield.api import EarthSatellite, load, wgs84

# ── CelesTrak endpoint (current as of 2025) ───────────────────────────────────
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"

# ── TLE cache settings ────────────────────────────────────────────────────────
# CelesTrak updates data ~3 times per day, so 2 hours is the recommended
# minimum cache duration. Fetching more often risks IP throttling/blocking.
TLE_CACHE_FILE    = "/tmp/tle_cache.json"
TLE_CACHE_MAX_AGE = 7200   # seconds (2 hours)

ts = load.timescale()


# =============================================================================
#  TLE FETCHER  — with caching, proper User-Agent, and stale-cache fallback
# =============================================================================

def fetch_tle(norad_id: int):
    """
    Récupère les TLE depuis CelesTrak avec:
      - Un header User-Agent correct (évite les blocages silencieux)
      - Un timeout de 30 s (CelesTrak peut être lent)
      - Un cache local JSON valide 2 heures (évite le throttling)
      - Un fallback sur le cache périmé si le réseau est indisponible
    """
    cache     = {}
    cache_key = str(norad_id)
    now       = time.time()

    # ── Charger le cache local si disponible ─────────────────────────────────
    if os.path.exists(TLE_CACHE_FILE):
        try:
            with open(TLE_CACHE_FILE, "r") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    # ── Retourner le cache s'il est encore frais ──────────────────────────────
    if cache_key in cache:
        entry = cache[cache_key]
        age   = now - entry.get("fetched_at", 0)
        if age < TLE_CACHE_MAX_AGE:
            print(f"  [TLE] Cache local utilisé pour NORAD {norad_id} "
                  f"(âge : {age/60:.0f} min)")
            return entry["name"], entry["tle1"], entry["tle2"]

    # ── Requête vers CelesTrak ────────────────────────────────────────────────
    print(f"  [TLE] Téléchargement depuis CelesTrak (NORAD {norad_id})...")
    headers = {
        # Un User-Agent identifiable est requis — les requêtes anonymes
        # sont parfois bloquées silencieusement par le serveur CelesTrak.
        "User-Agent": "SatelliteTracker/1.0 (educational project)"
    }
    params = {"CATNR": norad_id, "FORMAT": "tle"}

    try:
        resp = requests.get(
            CELESTRAK_URL,
            params=params,
            headers=headers,
            timeout=30          # 30 s — CelesTrak peut être lent
        )
        resp.raise_for_status()

        # Réponse explicite de CelesTrak quand le NORAD ID est inconnu
        if "No GP data found" in resp.text:
            raise ValueError(
                f"NORAD {norad_id} introuvable sur CelesTrak. "
                f"Vérifiez l'identifiant."
            )

        lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
        if len(lines) < 2:
            raise ValueError(
                f"Format TLE inattendu pour NORAD {norad_id}:\n{resp.text}"
            )

        if not lines[0].startswith("1 "):
            name, tle1, tle2 = lines[0], lines[1], lines[2]
        else:
            name, tle1, tle2 = f"NORAD {norad_id}", lines[0], lines[1]

        # ── Sauvegarder dans le cache ─────────────────────────────────────────
        cache[cache_key] = {
            "name": name, "tle1": tle1, "tle2": tle2,
            "fetched_at": now
        }
        try:
            with open(TLE_CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass   # Échec d'écriture non fatal

        print(f"  [TLE] OK — {name}")
        return name, tle1, tle2

    except requests.exceptions.Timeout:
        # ── Fallback sur cache périmé ─────────────────────────────────────────
        if cache_key in cache:
            entry = cache[cache_key]
            age   = now - entry.get("fetched_at", 0)
            print(f"  [TLE] Timeout réseau. Utilisation du cache périmé "
                  f"(âge : {age/3600:.1f} h). Précision réduite possible.")
            return entry["name"], entry["tle1"], entry["tle2"]
        raise RuntimeError(
            "CelesTrak n'a pas répondu (timeout) et aucun cache disponible.\n"
            "Vérifiez votre connexion internet et réessayez dans quelques minutes."
        )

    except Exception as exc:
        # ── Fallback sur cache périmé pour toute autre erreur ─────────────────
        if cache_key in cache:
            entry = cache[cache_key]
            age   = now - entry.get("fetched_at", 0)
            print(f"  [TLE] Erreur : {exc}\n"
                  f"  Utilisation du cache périmé (âge : {age/3600:.1f} h).")
            return entry["name"], entry["tle1"], entry["tle2"]
        raise


# =============================================================================
#  CONSTRUCTION DU SATELLITE
# =============================================================================

def build_satellite(norad_id: int) -> EarthSatellite:
    """Construit l'objet satellite Skyfield à partir des TLE."""
    name, l1, l2 = fetch_tle(norad_id)
    return EarthSatellite(l1, l2, name, ts)


# =============================================================================
#  CALCUL DE LA POSITION
# =============================================================================

def get_satellite_data(sat: EarthSatellite, observer):
    """Calcule position absolue + position relative (azimut/élévation)."""
    t = ts.now()

    # Position absolue du satellite (géodésique)
    geocentric = sat.at(t)
    subpoint   = wgs84.subpoint(geocentric)

    # Position relative vue depuis l'observateur
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
#  SAISIE UTILISATEUR
# =============================================================================

def get_user_inputs():
    """Demande les paramètres à l'utilisateur."""
    print("=" * 60)
    print("SATELLITE TRACKER - Configuration")
    print("=" * 60)

    # NORAD ID
    while True:
        try:
            norad_id = int(input(
                "\nEntrez le NORAD ID du satellite "
                "(ex: 25544 pour l'ISS, 63632 pour Shenzhou-20): "
            ))
            if norad_id > 0:
                break
            print("❌ Le NORAD ID doit être positif.")
        except ValueError:
            print("❌ Veuillez entrer un nombre valide.")

    # Intervalle de rafraîchissement
    while True:
        try:
            refresh = float(input(
                "Intervalle de rafraîchissement (en secondes, ex: 5): "
            ))
            if refresh > 0:
                break
            print("❌ L'intervalle doit être positif.")
        except ValueError:
            print("❌ Veuillez entrer un nombre valide.")

    # Position de l'observateur
    print("\n📍 Position de l'observateur:")
    while True:
        try:
            lat = float(input("  Latitude  (ex: 48.1172 pour Rennes): "))
            if -90 <= lat <= 90:
                break
            print("❌ La latitude doit être entre -90 et 90.")
        except ValueError:
            print("❌ Veuillez entrer un nombre valide.")

    while True:
        try:
            lon = float(input("  Longitude (ex: -1.6778 pour Rennes): "))
            if -180 <= lon <= 180:
                break
            print("❌ La longitude doit être entre -180 et 180.")
        except ValueError:
            print("❌ Veuillez entrer un nombre valide.")

    return norad_id, refresh, lat, lon


# =============================================================================
#  BOUCLE PRINCIPALE
# =============================================================================

def main():
    norad_id, refresh_seconds, lat, lon = get_user_inputs()

    observer = wgs84.latlon(lat, lon)

    print("\n🛰️  Récupération des données du satellite...")
    try:
        sat = build_satellite(norad_id)
    except Exception as e:
        print(f"❌ Erreur lors de la récupération du satellite: {e}")
        return

    print("\n" + "=" * 60)
    print(f"✅ Tracking: {sat.name} (NORAD {norad_id})")
    print(f"📍 Observateur: {lat:.4f}°N, {lon:.4f}°E")
    print(f"⏱️  Rafraîchissement: {refresh_seconds}s")
    print("=" * 60)
    print("\nAppuyez sur Ctrl+C pour arrêter\n")

    while True:
        try:
            data = get_satellite_data(sat, observer)

            status = "✅ VISIBLE" if data["is_visible"] else "❌ SOUS L'HORIZON"

            print(f"{data['timestamp_utc']}")
            print(
                f"  Position satellite: "
                f"lat={data['latitude_deg']:7.3f}°, "
                f"lon={data['longitude_deg']:7.3f}°, "
                f"alt={data['altitude_km']:6.1f} km"
            )
            print(
                f"  Vue observateur:   "
                f"az={data['azimuth_deg']:6.2f}°, "
                f"élév={data['elevation_deg']:6.2f}°, "
                f"dist={data['distance_km']:7.1f} km"
            )
            print(f"  Statut: {status}")
            print("-" * 60)

            time.sleep(refresh_seconds)

        except KeyboardInterrupt:
            print("\n\n🛑 Arrêt du tracking.")
            break
        except Exception as e:
            print(f"❌ Erreur: {e}")
            time.sleep(refresh_seconds)


if __name__ == "__main__":
    main()
