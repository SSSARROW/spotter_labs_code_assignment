"""One-time (or re-run-as-needed) data load: reads the provided fuel-price
CSV, resolves each station's City/State to coordinates via the offline
us_places.csv lookup (built from Census Gazetteer + GeoNames - see
routing/data/README.md), and populates FuelStation.

No network calls needed for the current CSV - everything needed ships in
routing/data/, and it already resolves 100% of the current data's valid US
rows. If a future CSV update adds a station in a town too obscure for that
static table, this falls back to a live Geoapify geocode (free, no credit
card - see settings.GEOAPIFY_API_KEY) for just the unmatched residual,
rather than requiring someone to manually patch code or regenerate
us_places.csv. Unset the key and this behaves exactly as before: unmatched
rows are skipped, reported, and nothing crashes.
"""
import csv
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import FuelStation
from routing.text import normalize_city, state_to_abbr

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
FUEL_CSV = DATA_DIR / "fuel_prices.csv"
PLACES_CSV = DATA_DIR / "us_places.csv"


class Command(BaseCommand):
    help = "Load fuel station prices from the provided CSV, geocoded via the offline place table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            default=str(FUEL_CSV),
            help="Path to the fuel prices CSV (defaults to routing/data/fuel_prices.csv)",
        )

    def handle(self, *args, **options):
        places = self._load_places()
        self.stdout.write(f"Loaded {len(places)} offline places for geocoding.")

        if settings.GEOAPIFY_API_KEY:
            self.stdout.write("Geoapify fallback enabled for rows the offline table can't match.")
        else:
            self.stdout.write(
                "Geoapify fallback disabled (no GEOAPIFY_API_KEY set) - "
                "unmatched rows will just be skipped, same as before."
            )

        csv_path = Path(options["csv"])
        stations = []
        skipped_unmatched = 0
        skipped_malformed = 0
        geocoded_live = 0
        total = 0

        required_columns = ["Truckstop Name", "Address", "City", "State", "Retail Price"]
        live_geocode_cache = {}  # (state, normalized_city) -> (lat, lon) | None, this run only

        with open(csv_path, newline="", encoding="latin-1") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1

                # A row shorter than the header (or a renamed/missing column)
                # leaves DictReader entries as None - .strip() on that raises
                # AttributeError. Catch it here, per-row, rather than one bad
                # row failing an entire bulk_create batch of up to 1000 rows.
                if any(row.get(col) is None for col in required_columns):
                    skipped_malformed += 1
                    continue

                state = state_to_abbr(row["State"])
                if not state:
                    skipped_unmatched += 1
                    continue

                city = row["City"].strip()
                key = (state, normalize_city(city))
                coords = places.get(key)

                if not coords and settings.GEOAPIFY_API_KEY:
                    if key not in live_geocode_cache:
                        live_geocode_cache[key] = self._geoapify_geocode(city, state)
                        time.sleep(0.1)  # light courtesy delay between live calls only
                    coords = live_geocode_cache[key]
                    if coords:
                        geocoded_live += 1

                if not coords:
                    skipped_unmatched += 1
                    self.stdout.write(f"  unmatched: {city}, {state}")
                    continue

                try:
                    price = float(row["Retail Price"].strip())
                except ValueError:
                    skipped_malformed += 1
                    continue

                lat, lon = coords
                stations.append(FuelStation(
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=city,
                    state=state,
                    price_per_gallon=price,
                    latitude=lat,
                    longitude=lon,
                ))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(stations)}/{total} stations "
            f"({geocoded_live} via live Geoapify fallback, "
            f"{skipped_unmatched} still unmatched, {skipped_malformed} malformed rows skipped)."
        ))

    @staticmethod
    def _load_places():
        lookup = {}
        with open(PLACES_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lookup[(row["state"], row["city_norm"])] = (
                    float(row["lat"]), float(row["lon"]),
                )
        return lookup

    def _geoapify_geocode(self, city, state):
        """One live lookup for a (city, state) the offline table missed.
        Returns (lat, lon) or None - never raises, since one bad/slow
        network call shouldn't fail the whole load."""
        try:
            resp = requests.get(
                settings.GEOAPIFY_GEOCODE_URL,
                params={
                    "text": f"{city}, {state}, USA",
                    "apiKey": settings.GEOAPIFY_API_KEY,
                    "filter": "countrycode:us",
                    "limit": 1,
                },
                timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except (requests.RequestException, ValueError) as exc:
            self.stdout.write(self.style.WARNING(
                f"  Geoapify lookup failed for {city}, {state}: {exc}"
            ))
            return None

        if not results:
            return None
        return results[0]["lat"], results[0]["lon"]
