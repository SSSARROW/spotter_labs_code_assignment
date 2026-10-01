import numpy as np
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from routing import geo, optimizer, services
from routing.models import FuelStation
from routing.serializers import RouteRequestSerializer


def _dedupe_candidates(candidates, bucket_miles=2.0):
    """Keep only the cheapest candidate per small stretch of route - shrinks
    the optimizer's input without changing its decisions (it always prefers
    the cheapest option in a given reachable window anyway)."""
    buckets = {}
    for c in candidates:
        key = int(c.mile_marker // bucket_miles)
        existing = buckets.get(key)
        if existing is None or c.price_per_gallon < existing.price_per_gallon:
            buckets[key] = c
    return list(buckets.values())


def _plan_for_route(route, all_stations):
    """Build the route path for one OSRM route option and try to find a
    feasible, cost-minimizing set of fuel stops for it, widening the
    corridor buffer if a stretch of it has no nearby stations.

    Returns (route_path, stops). Raises optimizer.RouteInfeasible if no
    buffer stage makes this particular route option feasible."""
    route_path = geo.build_route_path(route["geometry"], route["distance_miles"])

    station_lats = np.array([s.latitude for s in all_stations])
    station_lons = np.array([s.longitude for s in all_stations])
    offroute_miles, mile_marker = route_path.nearest_station_projection(station_lats, station_lons)

    last_error = None
    for buffer_miles in settings.CORRIDOR_BUFFER_STAGES_MILES:
        # The `0 < mile_marker < total_miles` bound matches optimizer.
        # plan_fuel_stops' own filter exactly - it must be applied here too,
        # *before* dedup: otherwise a station whose nearest point happens to
        # land right at mile 0 or the destination (which the optimizer would
        # exclude anyway, since there's no reason to "stop" exactly at either
        # end) could still win its 2-mile bucket on price and silently push
        # out a legitimately usable candidate just past that boundary.
        mask = (
            (offroute_miles <= buffer_miles)
            & (mile_marker > 0)
            & (mile_marker < route_path.total_miles)
        )
        candidates = [
            optimizer.Candidate(
                station_id=all_stations[i].id,
                mile_marker=float(mile_marker[i]),
                price_per_gallon=float(all_stations[i].price_per_gallon),
            )
            for i in np.where(mask)[0]
        ]
        candidates = _dedupe_candidates(candidates)
        try:
            stops = optimizer.plan_fuel_stops(
                candidates,
                total_miles=route_path.total_miles,
                tank_capacity_miles=settings.VEHICLE_RANGE_MILES,
                mpg=settings.VEHICLE_MPG,
            )
            return route_path, stops
        except optimizer.RouteInfeasible as exc:
            last_error = exc

    raise last_error


class RoutePlanView(APIView):
    """POST {"start": "City, ST", "finish": "City, ST"} ->
    route geometry, chosen fuel stops, and total trip fuel cost.

    Touches exactly one external API (OSRM routing) per request; start/finish
    are resolved via an offline place table first (see services.geocode).
    OSRM is asked for route alternatives in that same call, and whichever
    alternative yields the lowest total fuel cost is returned - OSRM's
    default pick is fastest/shortest, not necessarily cheapest to fuel.
    """

    def post(self, request):
        req = RouteRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        start_raw = req.validated_data["start"]
        finish_raw = req.validated_data["finish"]

        try:
            start_point = services.geocode(start_raw)
            finish_point = services.geocode(finish_raw)
        except services.GeocodingUnavailableError as exc:
            # The geocoder itself failed/timed out - an upstream problem,
            # not something wrong with the input. Same status as a routing
            # failure below, not the 422 used for "couldn't resolve this".
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except services.GeocodingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        try:
            route_options = services.get_routes(start_point, finish_point)
        except services.RoutingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        all_stations = list(FuelStation.objects.all())
        if not all_stations:
            return Response(
                {"error": "No fuel station data loaded on the server."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        feasible = []
        last_error = None
        for route in route_options:
            try:
                route_path, stops = _plan_for_route(route, all_stations)
                feasible.append((route, route_path, stops))
            except optimizer.RouteInfeasible as exc:
                last_error = exc

        if not feasible:
            return Response(
                {"error": f"Could not find a feasible fuel plan on any route option: {last_error}"},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        route, route_path, stops = min(
            feasible, key=lambda item: (sum(s.cost for s in item[2]), item[1].total_miles)
        )

        station_by_id = {s.id: s for s in all_stations}
        stop_payload = []
        for seq, stop in enumerate(stops, start=1):
            station = station_by_id[stop.station_id]
            stop_payload.append({
                "sequence": seq,
                "name": station.name,
                "address": station.address,
                "city": station.city,
                "state": station.state,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "mile_marker": round(stop.mile_marker, 1),
                "price_per_gallon": round(stop.price_per_gallon, 3),
                "gallons_purchased": round(stop.gallons_purchased, 2),
                "cost": round(stop.cost, 2),
            })

        total_gallons = route_path.total_miles / settings.VEHICLE_MPG
        total_cost = sum(s.cost for s in stops)

        return Response({
            "start": {
                "input": start_raw,
                "resolved": start_point.display_name,
                "latitude": start_point.latitude,
                "longitude": start_point.longitude,
            },
            "finish": {
                "input": finish_raw,
                "resolved": finish_point.display_name,
                "latitude": finish_point.latitude,
                "longitude": finish_point.longitude,
            },
            "distance_miles": round(route_path.total_miles, 1),
            "duration_hours": round(route["duration_seconds"] / 3600, 1),
            "vehicle": {
                "range_miles": settings.VEHICLE_RANGE_MILES,
                "mpg": settings.VEHICLE_MPG,
            },
            "total_gallons": round(total_gallons, 2),
            "total_fuel_cost": round(total_cost, 2),
            "fuel_stops": stop_payload,
            "route_geometry": [[lat, lon] for lat, lon in route_path.coords.tolist()],
            "route_alternatives_considered": len(route_options),
            "route_alternatives_feasible": len(feasible),
        })


class PlaceSearchView(APIView):
    """GET /api/places/?q=kansas -> ["Kansas City, KS", "Kansas City, MO", ...]

    Autocomplete for the /map/ demo page only - not part of the actual
    route-planning flow, and not required by it. Exists so a demo user
    doesn't have to guess which state disambiguates a city name (the real
    /api/route/ endpoint already handles disambiguation correctly by
    requiring one; this just makes it faster to type one in)."""

    def get(self, request):
        query = request.query_params.get("q", "")
        return Response(services.search_places(query))
