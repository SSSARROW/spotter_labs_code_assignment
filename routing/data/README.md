# `us_places.csv`

Offline `(state, normalized_city) -> (lat, lon)` lookup used to:

1. Geocode each row of `fuel_prices.csv` at load time (`manage.py load_fuel_stations`).
2. Resolve `"City, ST"` style start/finish input in API requests, with zero
   network calls (see `routing/services.py::geocode`).

## How it was built

Merged from two free, public sources (most-specific first, i.e. Census wins
on conflicts since it's the authoritative "incorporated place" centroid):

1. [US Census Bureau 2023 Gazetteer — National Places](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip)
   — all incorporated places and Census-designated places, with an internal-point
   lat/lon per place.
2. [GeoNames US postal code file](https://download.geonames.org/export/zip/US.zip)
   — broader coverage, including small unincorporated communities that have a
   ZIP but aren't a Census "place" (many highway truck-stop towns fall here).

City names are normalized (lowercased, punctuation stripped, `St`/`Mt`
expanded, place-type suffixes like "city"/"town"/"CDP" dropped) so lookups
are forgiving of minor formatting differences. Rows outside the 50 states +
DC are dropped.

Coverage check against `fuel_prices.csv`: 7,312 of 8,151 rows resolve
(remaining ~839 are Canadian provinces in the source data, or a small
residual of unmatched/misspelled city names).

This file is committed so the project is fully reproducible offline — no
network access is needed to run `load_fuel_stations`. To regenerate it from
scratch, re-download the two sources above and re-run the merge (state,
normalized city -> first lat/lon seen, Census before GeoNames).
