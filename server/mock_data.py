"""
Mock telemetry generator for development on macOS (without iRacing).

Simulates multiple cars circling a simple oval/road-course shaped track
so the radar UI can be fully tested.
"""

from __future__ import annotations

import math
import time
import random
from dataclasses import field
from typing import List, Tuple

from .telemetry import (
    TelemetrySnapshot, CarState,
    LR_CLEAR, LR_CAR_LEFT, LR_CAR_RIGHT, LR_CAR_LEFT_RIGHT,
)


# ---------------------------------------------------------------------------
#  Simple track generator — figure-8-ish shape for testing
# ---------------------------------------------------------------------------

def _generate_mock_track(
    num_points: int = 1000,
    track_length: float = 4500.0,
) -> List[Tuple[float, float]]:
    """
    Return a list of (x, y) in metres forming a closed loop.
    The shape loosely resembles a road course.
    """
    points = []
    for i in range(num_points):
        t = 2.0 * math.pi * i / num_points
        # parametric curves that make a somewhat interesting shape
        x = 600 * math.sin(t) + 150 * math.sin(3 * t)
        y = 400 * math.cos(t) + 100 * math.cos(2 * t)
        points.append((x, y))
    return points


def _track_heading(
    points: List[Tuple[float, float]], idx: int
) -> float:
    """Return heading (yaw) at index in radians."""
    n = len(points)
    x0, y0 = points[idx]
    x1, y1 = points[(idx + 1) % n]
    return math.atan2(x1 - x0, y1 - y0)


# ---------------------------------------------------------------------------
#  Mock car state
# ---------------------------------------------------------------------------

class _MockCar:
    def __init__(self, car_idx: int, pct: float, speed_variation: float = 1.0):
        self.car_idx = car_idx
        self.pct = pct % 1.0
        self.speed = speed_variation  # multiplier
        self.lap = 1
        self.on_track = True
        # slight random lateral offset for realism
        self.lateral_offset = random.uniform(-3.0, 3.0)


# ---------------------------------------------------------------------------
#  MockTelemetrySource
# ---------------------------------------------------------------------------

class MockTelemetrySource:
    """
    Drop-in replacement for TelemetryReader that generates fake data.
    Produces TelemetrySnapshot objects identical in shape to the real reader.
    """

    TRACK_NAME = 'Mock Speedway'
    TRACK_LENGTH = 4500.0  # metres

    def __init__(self, num_opponents: int = 8):
        self._track_points = _generate_mock_track(
            num_points=2000, track_length=self.TRACK_LENGTH
        )
        self._t0 = time.time()

        # player starts at 0 %
        self._player_pct = 0.0
        self._player_speed = 1.0  # multiplier

        # create opponents — some near player, rest spread out
        self._opponents: List[_MockCar] = []
        for i in range(num_opponents):
            if i < num_opponents // 3:
                # nearby cars: within ~0.5‒2% of player (≈ 20‒90 metres on a 4.5 km track)
                offset = random.uniform(-0.02, 0.02)
                pct = (self._player_pct + offset) % 1.0
                speed_var = random.uniform(0.97, 1.03)
            elif i < num_opponents * 2 // 3:
                # medium distance
                offset = random.choice([-1, 1]) * random.uniform(0.02, 0.08)
                pct = (self._player_pct + offset) % 1.0
                speed_var = random.uniform(0.94, 1.06)
            else:
                pct = random.uniform(0.0, 1.0)
                speed_var = random.uniform(0.92, 1.08)
            self._opponents.append(_MockCar(car_idx=i + 1, pct=pct, speed_variation=speed_var))

        self._base_speed = 0.00035  # fraction of track per tick (~60 m/s equivalent)

    def tick(self) -> TelemetrySnapshot:
        dt = 1.0 / 30.0  # simulate ~30 fps ticks

        # advance player
        self._player_pct = (self._player_pct + self._base_speed * self._player_speed) % 1.0
        player_idx = int(self._player_pct * len(self._track_points)) % len(self._track_points)
        px, py = self._track_points[player_idx]
        player_yaw = _track_heading(self._track_points, player_idx)

        # advance opponents
        for opp in self._opponents:
            opp.pct = (opp.pct + self._base_speed * opp.speed) % 1.0
            if opp.pct < self._base_speed:
                opp.lap += 1

        # build snapshot
        snap = TelemetrySnapshot(
            connected=True,
            player_car_idx=0,
            player_lat=py * 1e-5,   # scale to pseudo-lat/lon
            player_lon=px * 1e-5,
            player_alt=0.0,
            player_yaw=player_yaw,
            player_speed=self._base_speed * self.TRACK_LENGTH / dt,
            player_lap_dist_pct=self._player_pct,
            player_lap_dist=self._player_pct * self.TRACK_LENGTH,
            player_lap=1 + int(self._player_pct * 0),
            car_left_right=self._compute_car_left_right(),
            track_length=self.TRACK_LENGTH,
            track_name=self.TRACK_NAME,
            track_config='',
            session_type='Race',
        )

        # player as car 0
        snap.cars.append(CarState(
            car_idx=0,
            lap_dist_pct=self._player_pct,
            lap=1,
            on_track=True,
        ))

        for opp in self._opponents:
            snap.cars.append(CarState(
                car_idx=opp.car_idx,
                lap_dist_pct=opp.pct,
                lap=opp.lap,
                on_track=opp.on_track,
            ))

        return snap

    def _compute_car_left_right(self) -> int:
        """Simulate CarLeftRight based on nearby opponents."""
        left = False
        right = False
        for opp in self._opponents:
            delta = opp.pct - self._player_pct
            if delta > 0.5:
                delta -= 1.0
            elif delta < -0.5:
                delta += 1.0
            dist = abs(delta) * self.TRACK_LENGTH
            if dist < 12.0:  # alongside
                if opp.lateral_offset < 0:
                    left = True
                else:
                    right = True
        if left and right:
            return LR_CAR_LEFT_RIGHT
        if left:
            return LR_CAR_LEFT
        if right:
            return LR_CAR_RIGHT
        return LR_CLEAR
