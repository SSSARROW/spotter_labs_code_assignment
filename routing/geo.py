"""Route-geometry helpers: decode an OSRM route, project fuel stations onto
it, and figure out each candidate's distance-along-route ("mile marker").

Everything here is local computation (numpy/scipy) - no network calls. This
is what lets the view touch the routing API exactly once per request while
still matching against ~8k fuel stations.
"""
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles between two points (scalars, in degrees)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def _to_ecef(lat_deg, lon_deg):
    """Project lat/lon (degrees, arrays) onto a earth-radius sphere in
    Cartesian XYZ. Euclidean distance in this space closely approximates
    great-circle distance at the scale we care about (tens of miles), and
    lets us use a plain cKDTree instead of a haversine-aware index."""
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    x = EARTH_RADIUS_MILES * np.cos(lat) * np.cos(lon)
    y = EARTH_RADIUS_MILES * np.cos(lat) * np.sin(lon)
    z = EARTH_RADIUS_MILES * np.sin(lat)
    return np.column_stack([x, y, z])


@dataclass
class RoutePath:
    coords: np.ndarray          # (N, 2) array of [lat, lon] along the route, in order
    cumulative_miles: np.ndarray  # (N,) cumulative distance at each vertex
    total_miles: float          # authoritative total distance (from the routing API)

    def nearest_station_projection(self, lats, lons):
        """For arrays of station lat/lon, return (offroute_miles, mile_marker)
        for each station's nearest point on the route polyline."""
        tree = cKDTree(_to_ecef(self.coords[:, 0], self.coords[:, 1]))
        station_xyz = _to_ecef(np.asarray(lats), np.asarray(lons))
        chord_dist, idx = tree.query(station_xyz)
        # Convert chord length (straight line through the earth) back to an
        # arc-length approximation; negligible difference at this scale.
        offroute_miles = 2 * EARTH_RADIUS_MILES * np.arcsin(
            np.clip(chord_dist / (2 * EARTH_RADIUS_MILES), 0, 1)
        )
        mile_marker = self.cumulative_miles[idx]
        return offroute_miles, mile_marker


def build_route_path(geojson_coords, total_miles):
    """geojson_coords: list of [lon, lat] pairs as returned by OSRM
    (overview=full, geometries=geojson)."""
    coords = np.array([[lat, lon] for lon, lat in geojson_coords], dtype=float)
    seg_miles = haversine_miles(
        coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1]
    )
    cumulative = np.concatenate([[0.0], np.cumsum(seg_miles)])
    # Rescale so the polyline's own cumulative sum lines up with the
    # authoritative road distance OSRM reports (polyline vertices undercut
    # the true driving distance slightly).
    if cumulative[-1] > 0:
        cumulative = cumulative * (total_miles / cumulative[-1])
    return RoutePath(coords=coords, cumulative_miles=cumulative, total_miles=total_miles)
