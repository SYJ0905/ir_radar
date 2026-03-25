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
    Phase 2 — New alongside cars get assigned based on the spotter.
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

    # Phase 1: keep existing assignments
    unassigned: List[RadarBlip] = []
    for b in alongside:
        if b.car_idx in state.car_sides:
            b.rx = state.car_sides[b.car_idx]
        else:
            unassigned.append(b)

    # Phase 2: assign new alongside cars from spotter
    for b in unassigned:
        if has_left and not has_right:
            b.rx = -cfg.lateral_estimate
        elif has_right and not has_left:
            b.rx = cfg.lateral_estimate
        elif has_left and has_right:
            left_taken = any(
                state.car_sides.get(c, 0) < -1.0
                for c in alongside_ids if c != b.car_idx
            )
            right_taken = any(
                state.car_sides.get(c, 0) > 1.0
                for c in alongside_ids if c != b.car_idx
            )
            if left_taken and not right_taken:
                b.rx = cfg.lateral_estimate
            elif right_taken and not left_taken:
                b.rx = -cfg.lateral_estimate
            else:
                b.rx = -cfg.lateral_estimate
        else:
            b.rx = 0.0

        if abs(b.rx) > 0.1:
            state.car_sides[b.car_idx] = b.rx

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
