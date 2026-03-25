"""
Radar calculation engine — LapDistPct-based approach.

Uses CarIdxLapDistPct + TrackLength for longitudinal distance,
and CarLeftRight spotter data for lateral positioning.
No GPS/spline needed — works immediately on any track.
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
from .config import RadarConfig


@dataclass
class RadarBlip:
    """A single car on the radar, in radar-local coordinates."""
    car_idx: int
    rx: float               # >0 = right of player (metres)
    ry: float               # >0 = ahead of player (metres)
    distance: float          # absolute distance in metres
    is_lapped: bool = False


def _resolve_overlaps(blips: List[RadarBlip], cfg: RadarConfig) -> List[RadarBlip]:
    vs = cfg.car_visual_scale
    car_w = cfg.other_car_width * vs
    car_h = cfg.other_car_length * vs
    self_w = cfg.self_car_width * vs
    self_h = cfg.self_car_length * vs
    min_gap = 0.3

    effective_w = car_w + min_gap
    effective_h = car_h + min_gap

    for _ in range(3):
        for b in blips:
            ox = (effective_w + self_w + min_gap) / 2.0 - abs(b.rx)
            oy = (effective_h + self_h + min_gap) / 2.0 - abs(b.ry)
            if ox > 0 and oy > 0:
                if ox < oy:
                    sign = 1.0 if b.rx >= 0 else -1.0
                    b.rx += sign * ox
                else:
                    sign = 1.0 if b.ry >= 0 else -1.0
                    b.ry += sign * oy

        for i in range(len(blips)):
            for j in range(i + 1, len(blips)):
                a, b = blips[i], blips[j]
                dx = abs(a.rx - b.rx)
                dy = abs(a.ry - b.ry)
                ox = effective_w - dx
                oy = effective_h - dy
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
    cfg: RadarConfig,
) -> List[RadarBlip]:
    """
    Compute radar blips using LapDistPct + TrackLength.

    Longitudinal distance = pct_diff * track_length (metres).
    Lateral position = estimated from CarLeftRight spotter for nearby cars.
    """
    if not snap.connected or not snap.cars:
        return []
    if snap.track_length <= 0:
        return []

    track_len = snap.track_length
    player_pct = snap.player_lap_dist_pct

    blips: List[RadarBlip] = []

    for car in snap.cars:
        if car.car_idx == snap.player_car_idx:
            continue
        if not car.on_track or car.on_pit_road:
            continue
        if car.lap_dist_pct < 0:
            continue

        pct_diff = car.lap_dist_pct - player_pct
        if pct_diff > 0.5:
            pct_diff -= 1.0
        elif pct_diff < -0.5:
            pct_diff += 1.0

        ry = pct_diff * track_len
        abs_dist = abs(ry)

        if abs_dist > cfg.range_metres:
            continue

        # Only mark as "lapped" when the lap difference is >= 2.
        # A 1-lap diff is common near the S/F line or in practice where
        # AI starts on lap 0 while the player is on lap 1.
        is_lapped = abs(car.lap - snap.player_lap) >= 2

        blips.append(RadarBlip(
            car_idx=car.car_idx,
            rx=0.0,
            ry=ry,
            distance=abs_dist,
            is_lapped=is_lapped,
        ))

    # --- Lateral assignment using CarLeftRight spotter ---
    # The spotter detects cars roughly alongside the player (~10m window).
    # Assign the closest alongside cars to left/right based on spotter state.
    alongside_threshold = 12.0  # metres
    alongside = sorted(
        [b for b in blips if abs(b.ry) < alongside_threshold],
        key=lambda b: abs(b.ry),
    )

    has_left = snap.car_left_right in (
        LR_CAR_LEFT, LR_CAR_LEFT_RIGHT, LR_2_CARS_LEFT,
    )
    has_right = snap.car_left_right in (
        LR_CAR_RIGHT, LR_CAR_LEFT_RIGHT, LR_2_CARS_RIGHT,
    )
    two_left = snap.car_left_right == LR_2_CARS_LEFT
    two_right = snap.car_left_right == LR_2_CARS_RIGHT

    assigned = set()

    if has_left and has_right and len(alongside) >= 2:
        alongside[0].rx = -cfg.lateral_estimate
        assigned.add(alongside[0].car_idx)
        alongside[1].rx = cfg.lateral_estimate
        assigned.add(alongside[1].car_idx)
        if two_left and len(alongside) >= 3:
            alongside[2].rx = -cfg.lateral_estimate * 1.8
            assigned.add(alongside[2].car_idx)
        if two_right and len(alongside) >= 3:
            idx = 3 if alongside[2].car_idx in assigned else 2
            if idx < len(alongside):
                alongside[idx].rx = cfg.lateral_estimate * 1.8
                assigned.add(alongside[idx].car_idx)
    elif has_left:
        if alongside:
            alongside[0].rx = -cfg.lateral_estimate
            assigned.add(alongside[0].car_idx)
        if two_left and len(alongside) >= 2:
            alongside[1].rx = -cfg.lateral_estimate * 1.8
            assigned.add(alongside[1].car_idx)
    elif has_right:
        if alongside:
            alongside[0].rx = cfg.lateral_estimate
            assigned.add(alongside[0].car_idx)
        if two_right and len(alongside) >= 2:
            alongside[1].rx = cfg.lateral_estimate * 1.8
            assigned.add(alongside[1].car_idx)

    # Unassigned alongside cars: spread slightly for visibility
    for b in alongside:
        if b.car_idx not in assigned:
            b.rx = ((b.car_idx * 7) % 5 - 2) * 0.4

    # Recalculate distance including lateral component
    for b in blips:
        b.distance = math.sqrt(b.rx * b.rx + b.ry * b.ry)

    blips = _resolve_overlaps(blips, cfg)
    return blips
