from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from routing import geo, optimizer, text
from routing.models import FuelStation
from routing.services import GeoPoint


class OptimizerTests(TestCase):
    def test_no_stops_needed_within_range(self):
        stops = optimizer.plan_fuel_stops(
            candidates=[optimizer.Candidate(1, 100, 3.00)],
            total_miles=400,
            tank_capacity_miles=500,
            mpg=10,
        )
        self.assertEqual(stops, [])

    def test_buys_minimum_to_reach_cheaper_station_in_range(self):
        # Cheaper station B is reachable from A within tank range, so at A
        # we should buy only enough to reach B, not fill the tank.
        candidates = [
            optimizer.Candidate(station_id=1, mile_marker=200, price_per_gallon=3.50),  # A
            optimizer.Candidate(station_id=2, mile_marker=600, price_per_gallon=2.50),  # B (cheaper)
        ]
        stops = optimizer.plan_fuel_stops(
            candidates, total_miles=700, tank_capacity_miles=500, mpg=10,
        )
        self.assertEqual(len(stops), 2)
        stop_a, stop_b = stops
        self.assertEqual(stop_a.station_id, 1)
        # tank at A = 500-200=300mi left; need 600-200=400mi to reach B -> buy 100mi worth = 10gal
        self.assertAlmostEqual(stop_a.gallons_purchased, 10.0)
        self.assertEqual(stop_b.station_id, 2)
        # at B, full_reach=600+500=1100 >= 700 (destination) and no cheaper station ahead -> top up to finish
        self.assertAlmostEqual(stop_b.gallons_purchased, (700 - 600) / 10)

    def test_fills_up_when_nothing_cheaper_ahead(self):
        candidates = [
            optimizer.Candidate(station_id=1, mile_marker=200, price_per_gallon=2.50),  # cheapest
            optimizer.Candidate(station_id=2, mile_marker=600, price_per_gallon=3.50),  # pricier
        ]
        stops = optimizer.plan_fuel_stops(
            candidates, total_miles=1000, tank_capacity_miles=500, mpg=10,
        )
        stop_a = stops[0]
        self.assertEqual(stop_a.station_id, 1)
        # no cheaper station within 500mi of A (mile 200-700) -> top off to a
        # full tank: arrived with 500-200=300mi left, cap is 500mi, so buy 200mi worth
        self.assertAlmostEqual(stop_a.gallons_purchased, 20.0)

    def test_infeasible_gap_raises(self):
        candidates = [optimizer.Candidate(1, 600, 3.00)]  # unreachable: > 500mi from start
        with self.assertRaises(optimizer.RouteInfeasible):
            optimizer.plan_fuel_stops(
                candidates, total_miles=1000, tank_capacity_miles=500, mpg=10,
            )

    def test_total_cost_matches_sum_of_stops(self):
        candidates = [
            optimizer.Candidate(1, 300, 3.00),
            optimizer.Candidate(2, 700, 2.80),
        ]
        stops = optimizer.plan_fuel_stops(
            candidates, total_miles=900, tank_capacity_miles=500, mpg=10,
        )
        for s in stops:
            self.assertAlmostEqual(s.cost, s.gallons_purchased * s.price_per_gallon)


class GeoTests(TestCase):
    def test_haversine_known_distance(self):
        # Chicago to Dallas, straight-line, is roughly 800-810 miles.
        d = geo.haversine_miles(41.8781, -87.6298, 32.7767, -96.7970)
        self.assertGreater(d, 780)
        self.assertLess(d, 820)

    def test_build_route_path_rescales_to_authoritative_distance(self):
        coords = [[-87.63, 41.88], [-90.0, 40.0], [-96.80, 32.78]]  # [lon, lat]
        path = geo.build_route_path(coords, total_miles=1000.0)
        self.assertAlmostEqual(path.cumulative_miles[-1], 1000.0, places=3)
        self.assertAlmostEqual(path.cumulative_miles[0], 0.0)

    def test_nearest_station_projection(self):
        coords = [[-87.63, 41.88], [-90.0, 40.0], [-96.80, 32.78]]
        path = geo.build_route_path(coords, total_miles=1000.0)
        # A station right at the first vertex should have ~0 offroute distance
        # and a mile marker near the start.
        offroute, mile_marker = path.nearest_station_projection([41.88], [-87.63])
        self.assertLess(offroute[0], 1.0)
        self.assertLess(mile_marker[0], 10.0)


class TextTests(TestCase):
    def test_normalize_expands_abbreviations_and_punctuation(self):
        self.assertEqual(text.normalize_city("St. Louis"), "saint louis")
        self.assertEqual(text.normalize_city("Winston-Salem"), "winston-salem")

    def test_normalize_does_not_mangle_real_city_names_ending_in_city(self):
        # regression: these must NOT be stripped down to "oklahoma"/"kansas"/etc -
        # "City" here is part of the actual place name, not a Census LSAD suffix.
        self.assertEqual(text.normalize_city("Oklahoma City"), "oklahoma city")
        self.assertEqual(text.normalize_city("Kansas City"), "kansas city")
        self.assertEqual(text.normalize_city("Rapid City"), "rapid city")

    def test_state_to_abbr_handles_full_name_and_abbr(self):
        self.assertEqual(text.state_to_abbr("Texas"), "TX")
        self.assertEqual(text.state_to_abbr("tx"), "TX")
        self.assertIsNone(text.state_to_abbr("Ontario"))


class RoutePlanViewTests(TestCase):
    """Exercises the full endpoint with the external services mocked out,
    so the test suite doesn't depend on network access or a live OSRM/CSV
    geocoding hit."""

    def setUp(self):
        self.client = APIClient()
        # A short synthetic corridor: two stations straddling the midpoint.
        FuelStation.objects.create(
            name="Cheap Gas", address="123 Rd", city="Midway", state="IL",
            price_per_gallon=2.50, latitude=40.0, longitude=-90.0,
        )
        FuelStation.objects.create(
            name="Pricier Gas", address="456 Rd", city="Farpoint", state="TX",
            price_per_gallon=3.50, latitude=38.0, longitude=-91.0,
        )

    @patch("routing.views.services.get_routes")
    @patch("routing.views.services.geocode")
    def test_short_trip_needs_no_stops(self, mock_geocode, mock_get_routes):
        mock_geocode.side_effect = [
            GeoPoint(41.88, -87.63, "Chicago, IL"),
            GeoPoint(41.5, -87.0, "Nearby, IL"),
        ]
        mock_get_routes.return_value = [{
            "geometry": [[-87.63, 41.88], [-87.0, 41.5]],
            "distance_miles": 50.0,
            "duration_seconds": 3600,
        }]
        resp = self.client.post("/api/route/", {"start": "Chicago, IL", "finish": "Nearby, IL"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["fuel_stops"], [])
        self.assertEqual(resp.data["total_fuel_cost"], 0)
        self.assertEqual(resp.data["route_alternatives_considered"], 1)

    @patch("routing.views.services.get_routes")
    @patch("routing.views.services.geocode")
    def test_picks_cheaper_route_alternative(self, mock_geocode, mock_get_routes):
        # Two feasible route options between the same two points, each
        # passing directly through a different one of the two stations set
        # up in setUp(). Both need exactly one stop (600mi > 500mi range).
        # OSRM lists the pricier-route first (mimicking its default
        # fastest/shortest ranking) - the view must still pick the cheaper
        # one on total fuel cost, not just take routes[0].
        mock_geocode.side_effect = [
            GeoPoint(41.88, -87.63, "Chicago, IL"),
            GeoPoint(33.0, -95.0, "Somewhere, TX"),
        ]
        pricier_route = {
            "geometry": [[-87.63, 41.88], [-91.0, 38.0], [-95.0, 33.0]],  # via Pricier Gas
            "distance_miles": 600.0,
            "duration_seconds": 36000,
        }
        cheaper_route = {
            "geometry": [[-87.63, 41.88], [-90.0, 40.0], [-95.0, 33.0]],  # via Cheap Gas
            "distance_miles": 600.0,
            "duration_seconds": 37000,
        }
        mock_get_routes.return_value = [pricier_route, cheaper_route]

        resp = self.client.post("/api/route/", {"start": "Chicago, IL", "finish": "Somewhere, TX"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["route_alternatives_considered"], 2)
        self.assertEqual(resp.data["route_alternatives_feasible"], 2)
        self.assertEqual(len(resp.data["fuel_stops"]), 1)
        self.assertEqual(resp.data["fuel_stops"][0]["name"], "Cheap Gas")

    def test_missing_fields_returns_400(self):
        resp = self.client.post("/api/route/", {"start": "Chicago, IL"}, format="json")
        self.assertEqual(resp.status_code, 400)

    @patch("routing.views.services.get_routes")
    @patch("routing.views.services.geocode")
    def test_works_with_a_session_cookie_present_no_csrf_enforced(self, mock_geocode, mock_get_routes):
        # Regression: this API has no notion of a logged-in user, but DRF's
        # default auth classes include SessionAuthentication, which would
        # enforce CSRF for any request that happens to carry a session
        # cookie - e.g. an admin logged into /admin/ in the same browser
        # also using the unauthenticated /map/ demo. DEFAULT_AUTHENTICATION_
        # CLASSES is emptied out in settings specifically to prevent that.
        #
        # APIClient disables CSRF enforcement by default (unlike a real
        # browser), so enforce_csrf_checks=True is required here or this
        # test would pass regardless of whether the fix is even in place.
        client = APIClient(enforce_csrf_checks=True)
        user = User.objects.create_user(username="admin", password="pw")
        client.force_login(user)
        mock_geocode.side_effect = [
            GeoPoint(41.88, -87.63, "Chicago, IL"),
            GeoPoint(41.5, -87.0, "Nearby, IL"),
        ]
        mock_get_routes.return_value = [{
            "geometry": [[-87.63, 41.88], [-87.0, 41.5]],
            "distance_miles": 50.0,
            "duration_seconds": 3600,
        }]
        resp = client.post("/api/route/", {"start": "Chicago, IL", "finish": "Nearby, IL"}, format="json")
        self.assertEqual(resp.status_code, 200)

    @patch("routing.views.services.geocode")
    def test_unexpected_exception_returns_clean_json_not_a_traceback(self, mock_geocode):
        # Simulate a genuine bug (not one of our known Geocoding/Routing/
        # RouteInfeasible exceptions) and confirm the custom exception
        # handler still returns clean JSON with no leaked internals, instead
        # of Django's HTML error page or a raw traceback.
        mock_geocode.side_effect = RuntimeError("something broke unexpectedly")
        resp = self.client.post("/api/route/", {"start": "Chicago, IL", "finish": "Dallas, TX"}, format="json")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.data, {"error": "Internal server error."})
        self.assertNotIn("something broke unexpectedly", str(resp.content))
