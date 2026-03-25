"""
Live telemetry reader — wraps pyirsdk to expose a clean data interface.

On Windows with iRacing running it reads real data.
Returns a TelemetrySnapshot dataclass every tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

try:
    import irsdk
    HAS_IRSDK = True
except ImportError:
    HAS_IRSDK = False


@dataclass
class CarState:
    car_idx: int = -1
    lap_dist_pct: float = 0.0
    lap: int = 0
    on_track: bool = False
    on_pit_road: bool = False
    class_position: int = 0
    est_time: float = 0.0


@dataclass
class TelemetrySnapshot:
    connected: bool = False

    # player
    player_car_idx: int = -1
    player_lat: float = 0.0
    player_lon: float = 0.0
    player_alt: float = 0.0
    player_yaw: float = 0.0           # rad
    player_speed: float = 0.0         # m/s
    player_lap_dist_pct: float = 0.0
    player_lap_dist: float = 0.0      # metres from S/F
    player_lap: int = 0
    car_left_right: int = 0           # irsdk_CarLeftRight enum

    # session
    track_length: float = 0.0         # metres
    track_name: str = ''
    track_config: str = ''
    session_type: str = ''

    # all cars
    cars: List[CarState] = field(default_factory=list)


# CarLeftRight enum values (mirrors irsdk_defines.h)
LR_OFF = 0
LR_CLEAR = 1
LR_CAR_LEFT = 2
LR_CAR_RIGHT = 3
LR_CAR_LEFT_RIGHT = 4
LR_2_CARS_LEFT = 5
LR_2_CARS_RIGHT = 6


class TelemetryReader:
    """Reads live iRacing telemetry via pyirsdk."""

    def __init__(self):
        if not HAS_IRSDK:
            raise RuntimeError(
                'pyirsdk is not installed. '
                'Install it with: pip install pyirsdk'
            )
        self._ir = irsdk.IRSDK()
        self._connected = False
        self._track_length = 0.0
        self._track_name = ''
        self._track_config = ''

    def startup(self, test_file: Optional[str] = None) -> bool:
        if test_file:
            ok = self._ir.startup(test_file=test_file)
        else:
            ok = self._ir.startup()
        if ok:
            self._connected = True
            self._parse_session_info()
        return ok

    def shutdown(self):
        self._ir.shutdown()
        self._connected = False

    @property
    def connected(self) -> bool:
        if not self._ir.is_connected:
            self._connected = False
        return self._connected

    def tick(self) -> Optional[TelemetrySnapshot]:
        if not self.connected:
            if not self.startup():
                return None
            if not self.connected:
                return None

        self._ir.freeze_var_buffer_latest()

        # re-parse session info if track name is still unknown
        if not self._track_name:
            self._parse_session_info()

        snap = TelemetrySnapshot(connected=True)

        # session
        snap.track_length = self._track_length
        snap.track_name = self._track_name
        snap.track_config = self._track_config

        # player basics
        snap.player_car_idx = self._ir['PlayerCarIdx'] or 0
        snap.player_lat = self._ir['Lat'] or 0.0
        snap.player_lon = self._ir['Lon'] or 0.0
        snap.player_yaw = self._ir['Yaw'] or 0.0
        snap.player_speed = self._ir['Speed'] or 0.0
        snap.player_lap_dist_pct = self._ir['LapDistPct'] or 0.0
        snap.player_lap_dist = self._ir['LapDist'] or 0.0
        snap.player_lap = self._ir['Lap'] or 0
        snap.car_left_right = self._ir['CarLeftRight'] or 0

        try:
            snap.player_alt = self._ir['Alt'] or 0.0
        except Exception:
            snap.player_alt = 0.0

        # all cars
        lap_dist_pcts = self._ir['CarIdxLapDistPct'] or []
        laps = self._ir['CarIdxLap'] or []
        surfaces = self._ir['CarIdxTrackSurface'] or []
        on_pit = self._ir['CarIdxOnPitRoad'] or []
        positions = self._ir['CarIdxClassPosition'] or []
        est_times = self._ir['CarIdxEstTime'] or []

        num_cars = len(lap_dist_pcts)
        for i in range(num_cars):
            on_track = False
            if i < len(surfaces):
                # irsdk_TrkLoc: -1=NotInWorld, 0=OffTrack, 1=InPitStall,
                # 2=AproachingPits, 3=OnTrack
                # Accept anything >= 0 (car exists in world)
                on_track = surfaces[i] >= 0

            car = CarState(
                car_idx=i,
                lap_dist_pct=lap_dist_pcts[i] if i < len(lap_dist_pcts) else 0.0,
                lap=laps[i] if i < len(laps) else 0,
                on_track=on_track,
                on_pit_road=bool(on_pit[i]) if i < len(on_pit) else False,
                class_position=positions[i] if i < len(positions) else 0,
                est_time=est_times[i] if i < len(est_times) else 0.0,
            )
            snap.cars.append(car)

        return snap

    def _parse_session_info(self):
        """Extract track info from session YAML (called once on connect)."""
        try:
            weekend = self._ir['WeekendInfo']
            if weekend:
                track_len_str = weekend.get('TrackLength', '0 km')
                km_str = track_len_str.replace(' km', '').replace(' mi', '').strip()
                self._track_length = float(km_str) * 1000.0
                self._track_name = weekend.get('TrackDisplayName', '') or ''
                self._track_config = weekend.get('TrackConfigName', '') or ''
        except Exception:
            self._track_name = self._track_name or 'unknown'
            self._track_config = self._track_config or ''
