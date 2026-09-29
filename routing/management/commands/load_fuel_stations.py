"""One-time (or re-run-as-needed) data load: reads the provided fuel-price
CSV, resolves each station's City/State to coordinates via the offline
us_places.csv lookup (built from Census Gazetteer + GeoNames - see
routing/data/README.md), and populates FuelStation.

No network calls: everything needed ships in routing/data/.
"""
import csv
from pathlib import Path

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

        csv_path = Path(options["csv"])
        stations = []
        skipped = 0
        total = 0

        with open(csv_path, newline="", encoding="latin-1") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                state = state_to_abbr(row["State"])
                if not state:
                    skipped += 1
                    continue
                key = (state, normalize_city(row["City"]))
                coords = places.get(key)
                if not coords:
                    skipped += 1
                    continue
                lat, lon = coords
                stations.append(FuelStation(
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=row["City"].strip(),
                    state=state,
                    price_per_gallon=row["Retail Price"].strip(),
                    latitude=lat,
                    longitude=lon,
                ))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(stations)}/{total} stations "
            f"({skipped} skipped - non-US or unmatched city/state)."
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
