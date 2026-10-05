"""Workspace of the bridge (GET /workspaces/<name>): its worlds, each with an origin and a safety area.

The fleet does not say which world it runs in, only its safety area (GET /safety-area/borders). A world
is active when its safety area is that area; several worlds of a workspace may share one.
"""
from dataclasses import dataclass
from typing import Any

LatLon = tuple[float, float]

# Vertices further apart than this [deg] (~0.5 m) are different; the areas come from the same world files.
AREA_TOLERANCE_DEG = 5e-6

LATLON_FRAME_ID = 1  # frame_id of /safety-area/borders whose points are x = lat, y = lon


@dataclass(frozen=True)
class World:
    name: str
    origin: LatLon
    safety_area: tuple[LatLon, ...]
    min_z: float | None = None
    max_z: float | None = None

    @staticmethod
    def from_json(body: dict[str, Any]) -> "World":
        area = tuple((float(p["lat"]), float(p["lon"])) for p in body.get("safety_area") or [])
        origin = body.get("origin")
        if origin is not None:
            center = (float(origin["lat"]), float(origin["lon"]))
        elif area:
            # A world without an origin: the middle of its safety area.
            lats, lons = zip(*area)
            center = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
        else:
            raise ValueError(f"world {body.get('name')!r} has neither an origin nor a safety area")
        return World(
            name=str(body["name"]),
            origin=center,
            safety_area=area,
            min_z=body.get("min_z"),
            max_z=body.get("max_z"),
        )


@dataclass(frozen=True)
class Workspace:
    name: str
    description: str
    worlds: tuple[World, ...]

    @staticmethod
    def from_json(body: dict[str, Any]) -> "Workspace":
        return Workspace(
            name=str(body["name"]),
            description=str(body.get("description") or ""),
            worlds=tuple(World.from_json(w) for w in body.get("worlds") or []),
        )

    def world(self, name: str) -> World | None:
        return next((w for w in self.worlds if w.name == name), None)

    def active_worlds(self, border: list[LatLon] | None) -> list[str]:
        """Names of the worlds whose safety area is the fleet's border."""
        if not border:
            return []
        return [w.name for w in self.worlds if same_area(w.safety_area, border)]


def border_latlon(body: dict[str, Any]) -> list[LatLon] | None:
    """Points of a /safety-area/borders response as (lat, lon); None when they are not in lat/lon."""
    if body.get("frame_id") != LATLON_FRAME_ID:
        return None
    return [(float(p["x"]), float(p["y"])) for p in body.get("points") or []]


def same_area(a: tuple[LatLon, ...] | list[LatLon], b: tuple[LatLon, ...] | list[LatLon],
              tolerance: float = AREA_TOLERANCE_DEG) -> bool:
    """Same polygon: the same vertices in the same cyclic order, starting anywhere and in either direction."""
    a, b = _open_ring(a), _open_ring(b)
    if len(a) < 3 or len(a) != len(b):
        return False

    def close(p: LatLon, q: LatLon) -> bool:
        return abs(p[0] - q[0]) <= tolerance and abs(p[1] - q[1]) <= tolerance

    n = len(a)
    for candidate in (b, b[::-1]):
        for shift in range(n):
            if all(close(a[i], candidate[(i + shift) % n]) for i in range(n)):
                return True
    return False


def _open_ring(points) -> list[LatLon]:
    """The polygon without a repeated closing vertex."""
    points = [tuple(p) for p in points]
    if len(points) > 1 and abs(points[0][0] - points[-1][0]) <= AREA_TOLERANCE_DEG \
            and abs(points[0][1] - points[-1][1]) <= AREA_TOLERANCE_DEG:
        points.pop()
    return points
