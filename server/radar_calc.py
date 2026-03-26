"""
Radar calculation engine — LapDistPct-based approach.

Uses CarIdxLapDistPct + TrackLength for longitudinal distance,
and CarLeftRight spotter data for lateral positioning with
persistent per-car side tracking to prevent lateral flips.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from .telemetry import (
    TelemetrySnapshot,
    LR_CAR_LEFT, LR_CAR_RIGHT, LR_CAR_LEFT_RIGHT,
    LR_2_CARS_LEFT, LR_2_CARS_RIGHT,
)
from .config import RadarConfig

ALONGSIDE_THRESHOLD = 12.0   # metres — spotter detection window
LATERAL_DECAY_DIST = 20.0    # metres — fade lateral offset to 0 over this distance


@dataclass
class RadarBlip:
    """A single car on the radar, in radar-local coordinates."""
    car_idx: int
    rx: float               # >0 = right of player (metres)
    ry: float               # >0 = ahead of player (metres)
    distance: float          # absolute distance in metres
    is_lapped: bool = False


class RadarState:
    """Persistent state across frames for per-car lateral tracking.

    Prevents the lateral-flip problem by remembering which side
    each car was assigned to, rather than re-computing from the
    global CarLeftRight spotter every frame.
    """

    def __init__(self):
        self.car_sides: Dict[int, float] = {}


def _assign_lateral(
    blips: List[RadarBlip],
    snap: TelemetrySnapshot,
    cfg: RadarConfig,
    state: RadarState,
) -> None:
    """Assign rx (lateral) positions using persistent per-car tracking.

    Phase 1 — Alongside cars that already have a persisted side keep it.
    Phase 2 — New alongside cars get assigned based on the spotter,
              but strictly capped: at most 1 car per side (2 if
              CLR = 2_CARS_LEFT / 2_CARS_RIGHT).  This prevents the
              accumulation bug where every nearby car gradually got
              assigned the same side across multiple frames.
    Phase 3 — Cars outside the alongside zone have their offset decayed
              smoothly toward zero so the transition looks natural.
    """
    has_left = snap.car_left_right in (
        LR_CAR_LEFT, LR_CAR_LEFT_RIGHT, LR_2_CARS_LEFT,
    )
    has_right = snap.car_left_right in (
        LR_CAR_RIGHT, LR_CAR_LEFT_RIGHT, LR_2_CARS_RIGHT,
    )

    alongside = sorted(
        [b for b in blips if abs(b.ry) < ALONGSIDE_THRESHOLD],
        key=lambda b: abs(b.ry),
    )
    alongside_ids = {b.car_idx for b in alongside}

    # Phase 1: keep existing assignments for alongside cars
    unassigned: List[RadarBlip] = []
    for b in alongside:
        if b.car_idx in state.car_sides:
            b.rx = state.car_sides[b.car_idx]
        else:
            unassigned.append(b)

    # Phase 2: assign new alongside cars from spotter.
    # Hard cap per side — spotter "car RIGHT" = ONE car alongside.
    max_left = 0
    max_right = 0
    if has_left:
        max_left = 2 if snap.car_left_right == LR_2_CARS_LEFT else 1
    if has_right:
        max_right = 2 if snap.car_left_right == LR_2_CARS_RIGHT else 1

    n_left = sum(1 for b in alongside
                 if b.car_idx in state.car_sides
                 and state.car_sides[b.car_idx] < -1.0)
    n_right = sum(1 for b in alongside
                  if b.car_idx in state.car_sides
                  and state.car_sides[b.car_idx] > 1.0)

    for b in unassigned:
        side = 0.0

        if has_left and not has_right and n_left < max_left:
            side = -cfg.lateral_estimate
        elif has_right and not has_left and n_right < max_right:
            side = cfg.lateral_estimate
        elif has_left and has_right:
            if n_left < max_left and n_right >= max_right:
                side = -cfg.lateral_estimate
            elif n_right < max_right and n_left >= max_left:
                side = cfg.lateral_estimate
            elif n_left < max_left and n_right < max_right:
                side = -cfg.lateral_estimate if n_left <= n_right else cfg.lateral_estimate

        if abs(side) > 0.1:
            b.rx = side
            state.car_sides[b.car_idx] = side
            if side < 0:
                n_left += 1
            else:
                n_right += 1
        else:
            b.rx = 0.0

    # Phase 3: non-alongside cars — decay lateral offset toward zero
    for b in blips:
        if b.car_idx in alongside_ids:
            continue
        prev = state.car_sides.get(b.car_idx)
        if prev is not None:
            overshoot = max(0.0, abs(b.ry) - ALONGSIDE_THRESHOLD)
            decay = max(0.0, 1.0 - overshoot / LATERAL_DECAY_DIST)
            b.rx = prev * decay
            if decay <= 0.0:
                del state.car_sides[b.car_idx]

    # Purge cars that left the radar entirely
    active = {b.car_idx for b in blips}
    for k in [k for k in state.car_sides if k not in active]:
        del state.car_sides[k]


def _resolve_overlaps(blips: List[RadarBlip], _cfg: RadarConfig) -> List[RadarBlip]:
    """Separate overlapping car blips for visual clarity.

    Self-car collision is intentionally NOT resolved — a car directly
    ahead or behind SHOULD overlap with the self-car icon; that overlap
    is informative, not a visual bug.

    Inter-car overlaps are resolved along the ry axis only, so cars
    that are following each other stay on the correct lateral line
    instead of being pushed left/right.
    """
    min_sep_y = 3.0
    min_sep_x = 2.5

    for _ in range(2):
        for i in range(len(blips)):
            for j in range(i + 1, len(blips)):
                a, b = blips[i], blips[j]
                dx = abs(a.rx - b.rx)
                dy = abs(a.ry - b.ry)
                if dx < min_sep_x and dy < min_sep_y:
                    push = (min_sep_y - dy) / 2.0
                    if a.ry <= b.ry:
                        a.ry -= push
                        b.ry += push
                    else:
                        a.ry += push
                        b.ry -= push

    return blips


def compute_radar(
    snap: TelemetrySnapshot,
    cfg: RadarConfig,
    state: Optional[RadarState] = None,
) -> List[RadarBlip]:
    """
    Compute radar blips using LapDistPct + TrackLength.

    Longitudinal distance = pct_diff * track_length (metres).
    Lateral position = estimated from CarLeftRight spotter with
    persistent per-car side tracking (requires a RadarState).
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

        is_lapped = abs(car.lap - snap.player_lap) >= 2

        blips.append(RadarBlip(
            car_idx=car.car_idx,
            rx=0.0,
            ry=ry,
            distance=abs_dist,
            is_lapped=is_lapped,
        ))

    if state is not None:
        _assign_lateral(blips, snap, cfg, state)

    for b in blips:
        b.distance = math.sqrt(b.rx * b.rx + b.ry * b.ry)

    blips = _resolve_overlaps(blips, cfg)
    return blips
