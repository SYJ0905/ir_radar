"""
Track Spline — record and replay the mapping:

    LapDistPct  →  (x, y)  world coordinates

Approach (proven by Joel Real Timing, iRon minimap, Open Racer):
  1. While driving, record (LapDistPct, Lat, Lon) at regular intervals.
  2. Convert GPS to a local (x, y) metre-based coordinate system.
  3. Save to JSON so subsequent sessions on the same track skip recording.
  4. At runtime, look up any car's LapDistPct → (x, y).
"""

from __future__ import annotations

import json
import math
import os
from typing import List, Optional, Tuple

import numpy as np

from .config import RadarConfig


# ---------------------------------------------------------------------------
#  GPS → local X/Y conversion (Mercator-esque, good enough for ≤10 km)
# ---------------------------------------------------------------------------

_EARTH_R = 6_371_000.0  # metres


def _gps_to_local(
    lat: float, lon: float,
    ref_lat: float, ref_lon: float,
) -> Tuple[float, float]:
    """Convert (lat, lon) degrees to (x, y) metres relative to a reference."""
    d_lat = math.radians(lat - ref_lat)
    d_lon = math.radians(lon - ref_lon)
    x = d_lon * _EARTH_R * math.cos(math.radians(ref_lat))
    y = d_lat * _EARTH_R
    return x, y


# ---------------------------------------------------------------------------
#  TrackSpline
# ---------------------------------------------------------------------------

class TrackSpline:
    """
    Holds a dense array of (x, y) positions indexed by LapDistPct.
    """

    def __init__(self, xs: np.ndarray, ys: np.ndarray, pcts: np.ndarray):
        # sorted arrays; pcts in [0, 1)
        order = np.argsort(pcts)
        self.pcts = pcts[order]
        self.xs = xs[order]
        self.ys = ys[order]

    def lookup(self, lap_dist_pct: float) -> Tuple[float, float]:
        """Return (x, y) for a given LapDistPct using linear interpolation."""
        pct = lap_dist_pct % 1.0
        idx = np.searchsorted(self.pcts, pct)
        n = len(self.pcts)

        if idx == 0:
            i0, i1 = n - 1, 0
        elif idx >= n:
            i0, i1 = n - 1, 0
        else:
            i0, i1 = idx - 1, idx

        p0, p1 = self.pcts[i0], self.pcts[i1]
        dp = p1 - p0
        if dp <= 0:
            dp += 1.0  # wrap-around
        t = (pct - p0) / dp if dp > 1e-9 else 0.0
        if t < 0:
            t += 1.0

        x = self.xs[i0] + t * (self.xs[i1] - self.xs[i0])
        y = self.ys[i0] + t * (self.ys[i1] - self.ys[i0])
        return float(x), float(y)

    # -----------------------------------------------------------------------
    #  Heading at a point (tangent direction)
    # -----------------------------------------------------------------------

    def heading_at(self, lap_dist_pct: float) -> float:
        """Return track heading (radians) at the given LapDistPct."""
        eps = 0.0005
        x0, y0 = self.lookup(lap_dist_pct - eps)
        x1, y1 = self.lookup(lap_dist_pct + eps)
        return math.atan2(x1 - x0, y1 - y0)

    # -----------------------------------------------------------------------
    #  Persistence
    # -----------------------------------------------------------------------

    def save(self, path: str):
        data = {
            'pcts': self.pcts.tolist(),
            'xs': self.xs.tolist(),
            'ys': self.ys.tolist(),
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    @classmethod
    def load(cls, path: str) -> 'TrackSpline':
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls(
            xs=np.array(data['xs'], dtype=np.float64),
            ys=np.array(data['ys'], dtype=np.float64),
            pcts=np.array(data['pcts'], dtype=np.float64),
        )


# ---------------------------------------------------------------------------
#  Spline file manager
# ---------------------------------------------------------------------------

def spline_path(cfg: RadarConfig, track_name: str, track_config: str) -> str:
    safe = (track_name + '_' + track_config).strip('_').replace(' ', '_').replace('/', '_')
    return os.path.join(cfg.spline_dir, f'{safe}.json')


def load_spline(
    cfg: RadarConfig, track_name: str, track_config: str,
) -> Optional[TrackSpline]:
    path = spline_path(cfg, track_name, track_config)
    if os.path.exists(path):
        try:
            return TrackSpline.load(path)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
#  Spline recorder — accumulates points during a lap, then builds spline
# ---------------------------------------------------------------------------

class SplineRecorder:
    """
    Feed GPS samples while driving; after crossing S/F, call build() to
    create a TrackSpline.
    """

    def __init__(self, sample_interval: float = 0.001):
        self._interval = sample_interval
        self._samples: List[Tuple[float, float, float]] = []  # (pct, lat, lon)
        self._last_pct: Optional[float] = None
        self._ref_lat: Optional[float] = None
        self._ref_lon: Optional[float] = None
        self.complete = False

    def feed(self, lap_dist_pct: float, lat: float, lon: float):
        if self.complete:
            return

        if self._ref_lat is None:
            self._ref_lat = lat
            self._ref_lon = lon

        # only sample at intervals
        if self._last_pct is not None:
            delta = lap_dist_pct - self._last_pct
            if 0 < delta < self._interval:
                return

        self._samples.append((lap_dist_pct, lat, lon))
        self._last_pct = lap_dist_pct

    def mark_lap_complete(self):
        if len(self._samples) > 50:
            self.complete = True

    @property
    def progress(self) -> float:
        if not self._samples:
            return 0.0
        return max(s[0] for s in self._samples)

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def build(self) -> Optional[TrackSpline]:
        if len(self._samples) < 50:
            return None

        ref_lat = self._ref_lat or 0.0
        ref_lon = self._ref_lon or 0.0

        pcts = []
        xs = []
        ys = []
        for pct, lat, lon in self._samples:
            x, y = _gps_to_local(lat, lon, ref_lat, ref_lon)
            pcts.append(pct)
            xs.append(x)
            ys.append(y)

        return TrackSpline(
            xs=np.array(xs, dtype=np.float64),
            ys=np.array(ys, dtype=np.float64),
            pcts=np.array(pcts, dtype=np.float64),
        )

    def build_and_save(self, cfg: RadarConfig, track_name: str, track_config: str) -> Optional[TrackSpline]:
        spline = self.build()
        if spline:
            path = spline_path(cfg, track_name, track_config)
            spline.save(path)
        return spline


# ---------------------------------------------------------------------------
#  Mock spline — for development without real track data
# ---------------------------------------------------------------------------

def create_mock_spline(num_points: int = 2000) -> TrackSpline:
    """Generate a synthetic spline matching MockTelemetrySource's track shape."""
    pcts = []
    xs = []
    ys = []
    for i in range(num_points):
        t = 2.0 * math.pi * i / num_points
        x = 600 * math.sin(t) + 150 * math.sin(3 * t)
        y = 400 * math.cos(t) + 100 * math.cos(2 * t)
        pcts.append(i / num_points)
        xs.append(x)
        ys.append(y)
    return TrackSpline(
        xs=np.array(xs, dtype=np.float64),
        ys=np.array(ys, dtype=np.float64),
        pcts=np.array(pcts, dtype=np.float64),
    )
