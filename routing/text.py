"""Shared text normalization for matching place names against the offline
gazetteer - used both when loading fuel stations and when geocoding the
start/finish locations a caller provides, so the two stay consistent.

Note: `us_places.csv` already had Census LSAD place-type suffixes (e.g. the
"city" in "Abbeville city") stripped when it was built - see
routing/data/README.md. normalize_city() must NOT repeat that stripping here,
since plain city names (from the fuel CSV or a caller's input) never carry
that suffix in the first place, and plenty of real US cities legitimately
end in the word "City" (Oklahoma City, Kansas City, Rapid City, ...).
"""
import re

STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

VALID_STATE_ABBR = set(STATE_NAME_TO_ABBR.values())


def normalize_city(s: str) -> str:
    s = s.replace("\xa0", " ").strip().lower()
    s = re.sub(r"\.", "", s)
    s = re.sub(r"\bst\b", "saint", s)
    s = re.sub(r"\bmt\b", "mount", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def state_to_abbr(s: str) -> str | None:
    s = s.strip().rstrip(".")  # tolerate "IL." as well as "IL"
    if not s:
        return None
    upper = s.upper()
    if upper in VALID_STATE_ABBR:
        return upper
    return STATE_NAME_TO_ABBR.get(s.lower())
