# `us_places.csv`

Offline `(state, normalized_city) -> (lat, lon)` lookup used to:

1. Geocode each row of `fuel_prices.csv` at load time (`manage.py load_fuel_stations`).
2. Resolve `"City, ST"` style start/finish input in API requests, with zero
   network calls (see `routing/services.py::geocode`).

## How it was built

Merged from two free, public sources (most-specific first, i.e. Census wins
on conflicts since it's the authoritative "incorporated place" centroid):

1. [US Census Bureau 2023 Gazetteer — National Places](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip):
   all incorporated places and Census-designated places, with an internal-point
   lat/lon per place.
2. [GeoNames US postal code file](https://download.geonames.org/export/zip/US.zip):
   broader coverage, including small unincorporated communities that have a
   ZIP but aren't a Census "place" (many highway truck-stop towns fall here).

Building this file is a two-step normalization: (1) Census Gazetteer entries
carry an appended place-type word ("Abbeville **city**", "Abanda **CDP**") -
that gets stripped once, here, at build time, since it's not part of the
real name. (2) The resulting base name (and every lookup key at query time,
via `routing/text.py::normalize_city`) gets lowercased, punctuation-stripped,
and `St`/`Mt` expanded. Step 2 deliberately does *not* repeat step 1's
suffix-stripping - `normalize_city` must never strip a trailing "city" from
a general city name, since plenty of real US cities legitimately end in the
word "City" (Oklahoma City, Kansas City, Rapid City, Cedar City, ...). An
earlier version of this pipeline applied that stripping in both places and
silently mismatched every one of those cities as a result.

Coverage check against `fuel_prices.csv`: all 7,531 US rows resolve (the
remaining 620 of 8,151 are Canadian provinces in the source data, correctly
excluded since the assignment scopes to the USA).

This file is committed so the project is fully reproducible offline. No
network access is needed to run `load_fuel_stations`. To regenerate it from
scratch, re-download the two sources above and re-run the merge (state,
normalized city -> first lat/lon seen, Census before GeoNames).
