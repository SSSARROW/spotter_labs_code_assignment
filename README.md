# Fuel Route API

Given a start and finish location in the USA, returns the driving route plus
a cost-optimized plan for where to stop and fuel up along the way (vehicle
range: 500 miles), and the total dollar cost of fuel for the trip.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py load_fuel_stations   # one-time: loads routing/data/fuel_prices.csv
python manage.py runserver
```

No API keys or accounts are needed anywhere in this stack. The commands
above work with zero configuration (safe dev defaults, no `.env` needed).
See **Production readiness** below for what a real deployment must set.

## Endpoint

`POST /api/route/`

```json
{"start": "Chicago, IL", "finish": "Dallas, TX"}
```

Accepts either `"City, ST"` or a full street address.

Response:

```json
{
  "start": {"input": "Chicago, IL", "resolved": "Chicago, IL", "latitude": 41.84, "longitude": -87.68},
  "finish": {"input": "Dallas, TX", "resolved": "Dallas, TX", "latitude": 32.79, "longitude": -96.77},
  "distance_miles": 919.2,
  "duration_hours": 16.4,
  "vehicle": {"range_miles": 500, "mpg": 10},
  "total_gallons": 91.92,
  "total_fuel_cost": 121.52,
  "fuel_stops": [
    {
      "sequence": 1,
      "name": "some station",
      "address": "...",
      "city": "Saint Louis", "state": "MO",
      "latitude": 38.63, "longitude": -90.25,
      "mile_marker": 294.4,
      "price_per_gallon": 2.899,
      "gallons_purchased": 29.44,
      "cost": 85.36
    }
  ],
  "route_geometry": [[41.837, -87.685], "... [lat, lon] pairs for plotting the route on a map ..."],
  "route_alternatives_considered": 2,
  "route_alternatives_feasible": 2
}
```

Errors return `4xx`/`5xx` with `{"error": "..."}` (e.g. a location that
can't be resolved, or a stretch of route with no reachable fuel station).

## Design

#### External calls: one per request

The only live network dependency is a single call to
[OSRM](https://project-osrm.org/)'s public routing API (free, no key) for
the route geometry and authoritative distance. Everything else, geocoding
`"City, ST"` input and matching ~7,500 fuel stations against the route
corridor, is resolved locally against data prepared ahead of time. Per-request
latency is dominated by that one OSRM round-trip (~1-2s), not by our own
computation (~0.3s for corridor matching against the full station table,
unoptimized).

#### Picking between route alternatives

OSRM's default pick for a route is fastest/shortest, which isn't necessarily
the cheapest to fuel: a road through a state with structurally cheaper gas
can lose out to a road that's merely a few minutes quicker. Rather than
accept that default, the one OSRM call asks for alternatives
(`alternatives=true`, still one HTTP call; OSRM returns every option in the
same response), runs the full corridor-match + optimizer pipeline against
each one locally, and returns whichever produces the lowest total fuel cost.
On Chicago→Dallas this actually changes the answer: OSRM's default route
runs through southern Illinois/Arkansas (961.5mi, 4 stops, $133.22); a
second option through Missouri is both shorter and cheaper (919.2mi, 2
stops, $121.52) and is what gets returned. This isn't a full "solve for the
cheapest possible road," though. OSRM typically offers at most 1-2 genuinely
different alternatives (sometimes none), so it's choosing the best of a few
real options, not searching the entire space of possible routes.

#### The fuel price CSV has no coordinates

Each of the ~8,000 rows only has a city/state. Rather than geocode 8,000
rows live against a rate-limited service on every server start,
`load_fuel_stations` resolves them once against an offline place table
built from the US Census Gazetteer + GeoNames (see `routing/data/README.md`)
and caches lat/lon into the `FuelStation` table. Start/finish input in API
requests is resolved the same way, for the same reason: a live geocoder
would add a fragile network dependency just to turn `"Chicago, IL"` into
coordinates when we already have a comprehensive, free, offline table for
exactly that. (A full street address that isn't in the offline table falls
back to the free Census geocoder.)

The offline table already resolves 100% of the current CSV's valid US rows
(verified), but it's still a static snapshot. If a future CSV update adds a
station in some town too obscure for it, `load_fuel_stations` won't error or
require a manual code change: it falls back to a one-time live geocode via
[Geoapify](https://www.geoapify.com/) (free, no credit card) for just that
residual, and caches results per city/state so a repeated town only costs
one call. Entirely optional: unset `GEOAPIFY_API_KEY` and unmatched rows
are just skipped and reported, exactly as before this existed.

#### Matching stations to the route

The route polyline is dense (thousands of vertices from OSRM's
`overview=full`). Fuel stations are projected onto their nearest point on
that polyline using a KD-tree over the earth as a sphere (not a flat
projection, so it stays accurate coast-to-coast), which gives both an
off-route distance and a "mile marker" (distance along the route) per
station in one query. Candidates within a buffer of the route are kept; the
buffer widens (5 → 15 → 30 miles) if a stretch of route has no nearby
stations, so a sparse rural corridor doesn't make the trip unsolvable.

#### Choosing where to stop

Classic capacitated-refuel optimization: the vehicle leaves with a full tank
(not purchased, it's free), and at each station visited, buys just enough
fuel to reach the next station that's both cheaper and within range; if no
such station exists, it fills up completely. This is the standard optimal
greedy for this problem (an exchange argument shows it's never better to
carry expensive fuel past a cheaper stop, or to buy less than a full tank at
a local price minimum). See `routing/optimizer.py`.

**Assumptions:**
- Vehicle starts with a full tank at no cost (so a trip under 500 miles
  costs $0 in fuel, since nothing needs to be bought).
- "Optimal" is read as cost-minimizing, not stop-minimizing.
- Fuel station coordinates are approximated to their city's centroid (no
  per-address geocoding), which is well within the tolerance needed to
  decide "is this station near the interstate corridor."

**Known limitations:**
- A station inside the corridor buffer (5/15/30mi, widening only if a
  stretch is otherwise infeasible) is treated as free to reach; one just
  outside it is invisible. In practice almost every real candidate is
  within a mile or two of the highway, so this rarely changes the outcome,
  but it's a hard cutoff rather than a cost (the detour's own fuel isn't
  charged against the plan).
- Alternatives are picked from what OSRM offers (see above), not a search
  over the full space of possible roads.

## Production readiness

Nothing here is hardcoded to a "just make it work" default that would be
unsafe to actually deploy.

- **No secrets in source, ever.** `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, and
  the throttle rate are all read from environment variables
  (`fuelroute/settings.py`), with safe dev-only defaults so the Setup
  commands above need zero configuration. There is nothing to leak because
  there's nothing hardcoded. See `.env.example` for exactly what a real
  deployment must set (`DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`,
  `DJANGO_ALLOWED_HOSTS`). No third-party API key exists anywhere in this
  project in the first place (OSRM and the Census geocoder are both
  keyless), so there's no credential to accidentally commit.
- **`python manage.py check --deploy` passes clean** once those three env
  vars are set to real production values (verified; see commit history).
  HSTS, secure cookies, and SSL redirect are all wired up behind `DEBUG=False`
  without breaking local `http://` development.
- **Every response is clean JSON, even for a bug.** A custom DRF exception
  handler (`routing/exceptions.py`) guarantees an unexpected exception never
  surfaces as Django's HTML debug page or a bare traceback. It's logged
  server-side and returned to the client as a generic `{"error": ...}` with
  no internal details, tested in `routing/tests.py`.
- **Throttled by default** (`DJANGO_ANON_THROTTLE_RATE`, 30/min out of the
  box), since this endpoint costs a real call to free upstream services on
  every request. It's rate-limited to protect both this server and
  OSRM/Census from being hammered through it.
- **The `/map/` demo page escapes everything it renders** before handing it
  to Leaflet's `bindPopup` (which treats its argument as raw HTML): station
  names/cities and geocoder-resolved addresses are escaped client-side, so a
  future data source or a weird geocoder response can't inject markup.
- **`db.sqlite3` and `.env` are gitignored**, so no data or config ever gets
  committed by accident.

## Stack

Django 6.1 (latest stable) + Django REST Framework, SQLite. `numpy`/`scipy`
for the corridor/nearest-neighbor math; no PostGIS needed at this data
scale (~7k stations).

## Tests

`routing/data/README.md` documents the offline place-lookup build. Sanity
end-to-end checks (short trip with zero stops, cross-country trip with
multiple stops, error handling for unresolvable locations) were run
manually against a local server. See the Loom walkthrough for a live demo.
