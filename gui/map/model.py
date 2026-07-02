from dataclasses import dataclass,field
from math import cos, sin, floor, log10, pi

from data.mission import Mission
from data.session import Session

Point = tuple[float,float]

@dataclass 
class Map:
    mission: Mission
    session: Session
    meters_per_pixel: float = 0.5
    min_meters_per_pixel: float = 0.03
    max_meters_per_pixel: float = 20.
    meters_per_degree_lat: float = 111_320.0
    origin_lat: float = 49.361912
    origin_lon: float =  14.260508
    pan_px: list[float] = field(default_factory=lambda: [0.0, 0.0])
    width: float = 0.0
    height: float = 0.0

    def origin_canvas(self, width: int, height: int) -> Point:
        return width / 2 + self.pan_px[0], height / 2 + self.pan_px[1]

    def latlon_to_world(self, lat: float, lon: float) -> Point:
        meters_per_degree_lon = self.meters_per_degree_lat * cos(self.origin_lat * pi / 180)
        return (
            (lon - self.origin_lon) * meters_per_degree_lon,
            (lat - self.origin_lat) * self.meters_per_degree_lat,
        )

    def world_to_latlon(self, point_m: Point) -> tuple[float, float]:
        meters_per_degree_lon = self.meters_per_degree_lat * cos(self.origin_lat * pi / 180)
        return (
            self.origin_lat + point_m[1] / self.meters_per_degree_lat,
            self.origin_lon + point_m[0] / meters_per_degree_lon,
        )
    
    def grid_cell_meters(self) -> float:
        target_px = 48
        raw_m = target_px * self.meters_per_pixel
        exponent = floor(log10(raw_m)) if raw_m > 0 else 0
        base = 10**exponent

        for multiplier in (1, 2, 5, 10):
            cell = multiplier * base
            if cell / self.meters_per_pixel >= target_px:
                return cell

        return 10 * base

    def world_to_canvas(self, point_m: Point, origin: Point|None = None) -> Point:
        if not origin:
            origin = self.origin_canvas(self.width, self.height)
        return (
            origin[0] + point_m[0] / self.meters_per_pixel,
            origin[1] - point_m[1] / self.meters_per_pixel,
        )

    def canvas_to_world(self, point_px: Point, origin: Point) -> Point:
        return (
            (point_px[0] - origin[0]) * self.meters_per_pixel,
            (origin[1] - point_px[1]) * self.meters_per_pixel,
        )
    