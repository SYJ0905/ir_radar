"""
Radar calculation engine.

Takes a TelemetrySnapshot + TrackSpline and produces a list of
RadarBlip objects ready for the UI to draw.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from .telemetry import (
    TelemetrySnapshot,
    LR_CAR_LEFT, LR_CAR_RIGHT, LR_CAR_LEFT_RIGHT,
    LR_2_CARS_LEFT, LR_2_CARS_RIGHT,
)
from .track_spline import TrackSpline
from .config import RadarConfig


@dataclass
class RadarBlip:
    """A single car on the radar, in radar-local coordinates."""
    car_idx: int
    # position relative to player, in metres, radar-oriented
    #   rx > 0 = to the RIGHT of player
    #   ry > 0 = AHEAD of player
    rx: float
    ry: float
    distance: float          # Euclidean distance in metres
    is_lapped: bool = False  # car is on a different lap
    lateral_side: int = 0    # -1 = left, 0 = unknown, 1 = right


def _rect_overlap(
    ax: float, ay: float, aw: float, ah: float,
    bx: float, by: float, bw: float, bh: float,
) -> tuple:
    """
    Check AABB overlap between two car rectangles centred at (ax,ay) and (bx,by).
    Returns (overlap_x, overlap_y) — the penetration depth on each axis.
    Both > 0 means they overlap.
    """
    dx = abs(ax - bx)
    dy = abs(ay - by)
    overlap_x = (aw + bw) / 2.0 - dx
    overlap_y = (ah + bh) / 2.0 - dy
    return overlap_x, overlap_y


def _resolve_overlaps(blips: List[RadarBlip], cfg: RadarConfig) -> List[RadarBlip]:
    """
    Push overlapping car rectangles apart so no two cars share the same space.
    Also prevents any car from overlapping with the self-car at (0, 0).
    Uses iterative AABB separation (3 passes).
    """
    vs = cfg.car_visual_scale
    car_w = cfg.other_car_width * vs
    car_h = cfg.other_car_length * vs
    self_w = cfg.self_car_width * vs
    self_h = cfg.self_car_length * vs
    min_gap = 0.3  # metres of minimum gap between cars

    effective_w = car_w + min_gap
    effective_h = car_h + min_gap

    for _ in range(3):  # iterate to handle chain collisions
        # check each blip against self-car at (0, 0)
        for b in blips:
            ox, oy = _rect_overlap(
                b.rx, b.ry, effective_w, effective_h,
                0.0, 0.0, self_w + min_gap, self_h + min_gap,
            )
            if ox > 0 and oy > 0:
                # push along the axis of least penetration
                if ox < oy:
                    sign = 1.0 if b.rx >= 0 else -1.0
                    b.rx += sign * ox
                else:
                    sign = 1.0 if b.ry >= 0 else -1.0
                    b.ry += sign * oy

        # check each pair of blips against each other
        for i in range(len(blips)):
            for j in range(i + 1, len(blips)):
                a, b = blips[i], blips[j]
                ox, oy = _rect_overlap(
                    a.rx, a.ry, effective_w, effective_h,
                    b.rx, b.ry, effective_w, effective_h,
                )
                if ox > 0 and oy > 0:
                    if ox < oy:
                        half = ox / 2.0
                        if a.rx <= b.rx:
                            a.rx -= half
                            b.rx += half
                        else:
                            a.rx += half
                            b.rx -= half
                    else:
                        half = oy / 2.0
                        if a.ry <= b.ry:
                            a.ry -= half
                            b.ry += half
                        else:
                            a.ry += half
                            b.ry -= half

    return blips


def compute_radar(
    snap: TelemetrySnapshot,
    spline: TrackSpline,
    cfg: RadarConfig,
) -> List[RadarBlip]:
    """
    Compute radar blips for all nearby cars.

    Steps:
      1. Look up player (x, y) from spline using player's LapDistPct.
      2. For each other car on track, look up its (x, y).
      3. Compute relative vector, rotate by player's yaw so "ahead" = +Y.
      4. Apply lateral correction when CarLeftRight data is available.
      5. Filter to cars within range_metres.
    """
    if not snap.connected or not snap.cars:
        return []

    # player world position and heading from spline
    # (must use spline heading, not iRacing Yaw, because the spline
    #  is in GPS coordinates while Yaw is in iRacing's game world)
    player_x, player_y = spline.lookup(snap.player_lap_dist_pct)
    player_yaw = spline.heading_at(snap.player_lap_dist_pct)

    # determine which cars are on the player's left / right via spotter
    left_side = snap.car_left_right in (LR_CAR_LEFT, LR_CAR_LEFT_RIGHT, LR_2_CARS_LEFT)
    right_side = snap.car_left_right in (LR_CAR_RIGHT, LR_CAR_LEFT_RIGHT, LR_2_CARS_RIGHT)

    blips: List[RadarBlip] = []

    for car in snap.cars:
        if car.car_idx == snap.player_car_idx:
            continue
        if not car.on_track or car.on_pit_road:
            continue
        if car.lap_dist_pct < 0:
            continue

        # world position from spline
        cx, cy = spline.lookup(car.lap_dist_pct)

        # relative to player
        dx = cx - player_x
        dy = cy - player_y

        # rotate into player's reference frame
        # rx > 0 = right of player, ry > 0 = ahead of player
        sin_y = math.sin(player_yaw)
        cos_y = math.cos(player_yaw)
        rx = dx * cos_y - dy * sin_y
        ry = dx * sin_y + dy * cos_y

        distance = math.sqrt(rx * rx + ry * ry)

        if distance > cfg.range_metres:
            continue

        # lap difference
        is_lapped = car.lap != snap.player_lap

        # lateral correction for very close cars
        # if a car is within ~15 m longitudinally and spotter says left/right,
        # nudge the blip sideways so it doesn't sit on top of the player dot
        lateral_side = 0
        if abs(ry) < 15.0 and distance < 15.0:
            if abs(rx) < 2.0:
                # car overlaps on the centreline — use spotter to fix
                if left_side and not right_side:
                    rx = -cfg.lateral_estimate
                    lateral_side = -1
                elif right_side and not left_side:
                    rx = cfg.lateral_estimate
                    lateral_side = 1
                elif left_side and right_side:
                    # both sides occupied — use trackwise heuristic:
                    # if this car is slightly to the left on spline, keep left
                    lateral_side = -1 if rx <= 0 else 1
                    rx = cfg.lateral_estimate * lateral_side
            else:
                lateral_side = -1 if rx < 0 else 1

        blips.append(RadarBlip(
            car_idx=car.car_idx,
            rx=rx,
            ry=ry,
            distance=distance,
            is_lapped=is_lapped,
            lateral_side=lateral_side,
        ))

    # prevent car rectangles from overlapping (overlapping = crash, not realistic)
    blips = _resolve_overlaps(blips, cfg)

    return blips
