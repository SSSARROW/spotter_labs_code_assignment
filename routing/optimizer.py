"""Chooses which fuel stations to stop at and how much to buy at each.

Model: the vehicle leaves with a full tank (free - we don't charge for it),
has a fixed range R (miles) per tank, and burns fuel proportionally to miles
driven. Given candidate stations (mile marker + price) along the route, pick
a set of stops and purchase amounts that gets the vehicle to the destination
without ever running out, minimizing total dollars spent.

Greedy rule, applied every time the vehicle is sitting at a station (or the
start): drive to the cheapest station reachable on the fuel currently in the
tank. Once there, buy just enough to reach the next station that's cheaper
and still within a full tank's range; if no such station exists, fill the
tank completely. This is the standard optimal strategy for "cap-limited,
price-varying refueling" problems - buying at a price only pays off once you
can't coast to somewhere cheaper, and you should always leave a cheap stop
with as much cheap fuel as you can carry.
"""
from dataclasses import dataclass


class RouteInfeasible(Exception):
    """Raised when a stretch of the route has no reachable fuel station."""


@dataclass
class Candidate:
    station_id: int
    mile_marker: float
    price_per_gallon: float


@dataclass
class FuelStop:
    station_id: int
    mile_marker: float
    price_per_gallon: float
    gallons_purchased: float
    cost: float


def plan_fuel_stops(candidates, total_miles, tank_capacity_miles, mpg):
    """candidates: iterable of Candidate, unsorted, may include duplicates/
    stations beyond the destination (filtered out here)."""
    stations = sorted(
        (c for c in candidates if 0 < c.mile_marker < total_miles),
        key=lambda c: c.mile_marker,
    )

    if total_miles <= tank_capacity_miles:
        return []  # destination reachable on the starting tank, no stops needed

    position = 0.0
    fuel_miles = tank_capacity_miles  # starting tank, free
    stops = []

    while True:
        reach = position + fuel_miles
        if reach >= total_miles:
            break

        window = [s for s in stations if position < s.mile_marker <= reach]
        if not window:
            raise RouteInfeasible(
                f"No fuel station within range between mile {position:.1f} "
                f"and {reach:.1f} of {total_miles:.1f}."
            )

        target = min(window, key=lambda s: s.price_per_gallon)
        fuel_miles -= (target.mile_marker - position)
        position = target.mile_marker

        full_reach = position + tank_capacity_miles
        cheaper_ahead = [
            s for s in stations
            if position < s.mile_marker <= min(full_reach, total_miles)
            and s.price_per_gallon < target.price_per_gallon
        ]

        if cheaper_ahead:
            needed = min(s.mile_marker for s in cheaper_ahead) - position
        elif full_reach >= total_miles:
            needed = total_miles - position
        else:
            needed = tank_capacity_miles

        buy_miles = max(0.0, needed - fuel_miles)
        if buy_miles > 1e-6:
            gallons = buy_miles / mpg
            stops.append(FuelStop(
                station_id=target.station_id,
                mile_marker=target.mile_marker,
                price_per_gallon=target.price_per_gallon,
                gallons_purchased=gallons,
                cost=gallons * target.price_per_gallon,
            ))
            fuel_miles += buy_miles

    return stops
