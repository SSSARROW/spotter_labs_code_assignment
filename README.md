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

No API keys or accounts are needed anywhere in this stack.

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
  "distance_miles": 961.5,
  "duration_hours": 17.0,
  "vehicle": {"range_miles": 500, "mpg": 10},
  "total_gallons": 96.15,
  "total_fuel_cost": 133.22,
  "fuel_stops": [
    {
      "sequence": 1,
      "name": "HUCKS FOOD & FUEL #379",
      "address": "I-57, EXIT 53",
      "city": "Marion", "state": "IL",
      "latitude": 37.73, "longitude": -88.94,
      "mile_marker": 313.2,
      "price_per_gallon": 2.929,
      "gallons_purchased": 28.89,
      "cost": 84.63
    }
  ],
  "route_geometry": [[41.837, -87.685], "... [lat, lon] pairs for plotting the route on a map ..."]
}
```

Errors return `4xx`/`5xx` with `{"error": "..."}` (e.g. a location that
can't be resolved, or a stretch of route with no reachable fuel station).

## Design

**External calls: one per request.** The only live network dependency is a
single call to [OSRM](https://project-osrm.org/)'s public routing API
(free, no key) for the route geometry and authoritative distance. Everything
else — geocoding `"City, ST"` input and matching ~7,500 fuel stations against
the route corridor — is resolved locally against data prepared ahead of time,
so per-request latency is dominated by that one OSRM round-trip (~1-2s),
not by our own computation (~0.3s for corridor matching against the full
station table, unoptimized).

**The fuel price CSV has no coordinates.** Each of the ~8,000 rows only has
a city/state. Rather than geocode 8,000 rows live against a rate-limited
service on every server start, `load_fuel_stations` resolves them once
against an offline place table built from the US Census Gazetteer + GeoNames
(see `routing/data/README.md`) and caches lat/lon into the `FuelStation`
table. Start/finish input in API requests is resolved the same way, for
the same reason — a live geocoder would add a fragile network dependency
just to turn `"Chicago, IL"` into coordinates when we already have a
comprehensive, free, offline table for exactly that. (A full street address
that isn't in the offline table falls back to the free Census geocoder.)

**Matching stations to the route.** The route polyline is dense (thousands
of vertices from OSRM's `overview=full`). Fuel stations are projected onto
their nearest point on that polyline using a KD-tree over the earth as a
sphere (not a flat projection, so it stays accurate coast-to-coast), which
gives both an off-route distance and a "mile marker" (distance along the
route) per station in one query. Candidates within a buffer of the route are
kept; the buffer widens (5 → 15 → 30 miles) if a stretch of route has no
nearby stations, so a sparse rural corridor doesn't make the trip
unsolvable.

**Choosing where to stop.** Classic capacitated-refuel optimization: the
vehicle leaves with a full tank (not purchased — it's free), and at each
station visited, buys just enough fuel to reach the next station that's
both cheaper and within range; if no such station exists, it fills up
completely. This is the standard optimal greedy for this problem (an
exchange argument shows it's never better to carry expensive fuel past a
cheaper stop, or to buy less than a full tank at a local price minimum). See
`routing/optimizer.py`.

**Assumptions:**
- Vehicle starts with a full tank at no cost (so a trip under 500 miles
  costs $0 in fuel — nothing needed to be bought).
- "Optimal" is read as cost-minimizing, not stop-minimizing.
- Fuel station coordinates are approximated to their city's centroid (no
  per-address geocoding), which is well within the tolerance needed to
  decide "is this station near the interstate corridor."

## Stack

Django 6.1 (latest stable) + Django REST Framework, SQLite. `numpy`/`scipy`
for the corridor/nearest-neighbor math — no PostGIS needed at this data
scale (~7k stations).

## Tests

`routing/data/README.md` documents the offline place-lookup build. Sanity
end-to-end checks (short trip with zero stops, cross-country trip with
multiple stops, error handling for unresolvable locations) were run
manually against a local server — see the Loom walkthrough for a live demo.
