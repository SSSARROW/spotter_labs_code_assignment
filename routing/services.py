"""External-facing services: turning a location string into coordinates, and
calling the routing API for a route between two coordinates.

Only `get_routes` is a hard network dependency on every request (one call).
Geocoding prefers the same offline place table used to seed fuel stations
(instant, no network, and it's what actually has coverage for "City, ST"
style input - the Census geocoder is address-range based and won't match a
bare city name). Census is kept as a fallback for full street addresses.
"""
import csv
import functools
import logging
from dataclasses import dataclass
from pathlib import Path

import requests
from django.conf import settings

from routing.text import normalize_city, state_to_abbr

PLACES_CSV = Path(__file__).resolve().parent / "data" / "us_places.csv"

logger = logging.getLogger(__name__)


class GeocodingError(Exception):
    """The location text itself couldn't be resolved - bad/ambiguous input.
    A client-observable problem: maps to 422."""


class GeocodingUnavailableError(GeocodingError):
    """The geocoding service failed to respond or returned something
    unusable - an upstream failure, not a problem with the input. Maps to
    502, same as RoutingError, not 422. Subclasses GeocodingError so a bare
    `except GeocodingError` still catches this too, but callers that care
    about the distinction (see views.py) should catch this first."""


class RoutingError(Exception):
    pass


@dataclass
class GeoPoint:
    latitude: float
    longitude: float
    display_name: str


@functools.lru_cache(maxsize=1)
def _load_places():
    lookup = {}
    with open(PLACES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lookup[(row["state"], row["city_norm"])] = (
                float(row["lat"]),
                float(row["lon"]),
            )
    return lookup


@functools.lru_cache(maxsize=1)
def _load_places_for_search():
    """(city_norm, display_name) pairs for autocomplete - built from the
    same offline table used for geocoding, so suggestions always match what
    geocode() can actually resolve."""
    entries = []
    with open(PLACES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entries.append((row["city_norm"], f"{row['city_norm'].title()}, {row['state']}"))
    return entries


def search_places(query: str, limit: int = 10) -> list[str]:
    """Autocomplete for the /map/ demo page: prefix-match city names against
    the offline place table, so typing "kansas" suggests both
    "Kansas City, MO" and "Kansas City, KS" instead of the caller having to
    guess which state disambiguates it. Not used by the real /api/route/
    endpoint at all - purely a demo-page convenience."""
    q = query.strip().lower()
    if len(q) < 2:
        return []
    matches = {display for city_norm, display in _load_places_for_search() if city_norm.startswith(q)}
    return sorted(matches)[:limit]


def _try_offline_lookup(raw: str) -> GeoPoint | None:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    city_part, state_part = parts[-2], parts[-1]
    abbr = state_to_abbr(state_part)
    if not abbr:
        return None
    places = _load_places()
    key = (abbr, normalize_city(city_part))
    coords = places.get(key)
    if not coords:
        return None
    lat, lon = coords
    return GeoPoint(latitude=lat, longitude=lon, display_name=f"{city_part.strip()}, {abbr}")


def _try_census_lookup(raw: str) -> GeoPoint | None:
    try:
        resp = requests.get(
            settings.CENSUS_GEOCODER_URL,
            params={
                "address": raw,
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
            timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        # The real cause (DNS failure, connection refused, timeout, ...) is
        # often a verbose, implementation-specific string (urllib3/socket/OS
        # internals) - useful for debugging, not for an API client. Log the
        # real one, return a clean, actionable one.
        logger.warning("Census geocoder request failed: %s", exc)
        raise GeocodingUnavailableError(
            "Could not reach the geocoding service. Check your network connection and try again."
        ) from exc
    except ValueError as exc:  # includes json.JSONDecodeError
        logger.warning("Census geocoder returned an unparseable response: %s", exc)
        raise GeocodingUnavailableError(
            "The geocoding service returned an unexpected response. Please try again."
        ) from exc

    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    match = matches[0]
    coords = match.get("coordinates", {})
    if "y" not in coords or "x" not in coords:
        raise GeocodingUnavailableError("Geocoding service returned a match with no coordinates.")
    return GeoPoint(
        latitude=coords["y"],
        longitude=coords["x"],
        display_name=match.get("matchedAddress", raw),
    )


def geocode(raw: str) -> GeoPoint:
    """Resolve a free-text US location ("Chicago, IL" or a full street
    address) to coordinates. Tries the offline place table first (covers
    "City, ST" input with zero network calls); falls back to the Census
    geocoder for full street addresses it can't resolve."""
    if not raw or not raw.strip():
        raise GeocodingError("Location is required.")

    point = _try_offline_lookup(raw)
    if point:
        return point

    point = _try_census_lookup(raw)
    if point:
        return point

    raise GeocodingError(
        f"Could not resolve location '{raw}'. Try 'City, ST' or a full street address."
    )


def get_routes(start: GeoPoint, finish: GeoPoint) -> list[dict]:
    """Single call to the OSRM routing API, requesting alternatives so the
    caller can evaluate more than one road option for fuel cost instead of
    just whatever OSRM ranks first (fastest/shortest, which isn't
    necessarily cheapest to fuel). Still exactly one HTTP call - OSRM
    returns every alternative in the same response.

    Returns a list of route dicts (geometry + authoritative distance/
    duration), OSRM's top-ranked route first. Most origin/destination pairs
    only have one genuinely different alternative or none at all; when none
    exists, this list has a single entry, same as before.
    """
    url = (
        f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{start.longitude},{start.latitude};{finish.longitude},{finish.latitude}"
    )
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "alternatives": "true"},
            timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        # Same reasoning as the Census case above: requests/urllib3 exception
        # text for a connection failure includes raw socket/DNS/OS internals
        # (e.g. "NameResolutionError(...) [Errno 11001] getaddrinfo failed"
        # when there's no internet at all) - not something an API client
        # should see. Log the real one, return a clean, actionable one.
        logger.warning("OSRM routing request failed: %s", exc)
        raise RoutingError(
            "Could not reach the routing service. Check your network connection and try again."
        ) from exc
    except ValueError as exc:  # includes json.JSONDecodeError
        logger.warning("OSRM returned an unparseable response: %s", exc)
        raise RoutingError(
            "The routing service returned an unexpected response. Please try again."
        ) from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"No route found: {data.get('message', data.get('code'))}")

    meters_to_miles = 0.000621371
    try:
        routes = [
            {
                "geometry": route["geometry"]["coordinates"],  # [lon, lat] pairs
                "distance_miles": route["distance"] * meters_to_miles,
                "duration_seconds": route["duration"],
            }
            for route in data["routes"]
        ]
    except (KeyError, TypeError) as exc:
        raise RoutingError(f"Routing service response was missing expected fields: {exc}") from exc

    for r in routes:
        if len(r["geometry"]) < 2:
            raise RoutingError("Routing service returned a route with degenerate geometry.")
    return routes
