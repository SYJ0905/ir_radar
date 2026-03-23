#!/usr/bin/env python3
"""
iRadar — 360° proximity radar overlay for iRacing.

Usage:
    python main.py            # Windows — connect to iRacing
    python main.py --mock     # Mac/dev — use simulated data
    python main.py --mock -n 12  # Mock with 12 opponents
"""

from __future__ import annotations

import argparse
import sys

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from server.config import RadarConfig
from server.overlay import OverlayWindow
from server.radar_calc import compute_radar
from server.track_spline import (
    load_spline, create_mock_spline,
    SplineRecorder, TrackSpline,
)


class RadarApp:
    """Main application loop: telemetry → radar calc → overlay draw."""

    def __init__(self, cfg: RadarConfig, mock: bool = False, num_opponents: int = 8):
        self.cfg = cfg
        self.mock = mock

        # telemetry source
        if mock:
            from server.mock_data import MockTelemetrySource
            self.source = MockTelemetrySource(num_opponents=num_opponents)
        else:
            from server.telemetry import TelemetryReader
            self.source = TelemetryReader()

        # track spline
        self.spline: TrackSpline | None = None
        self.recorder: SplineRecorder | None = None
        self._last_lap_pct: float = -1.0
        self._recording_lap: int = -1

        # if mock, generate spline immediately
        if mock:
            self.spline = create_mock_spline()

        # Qt app + overlay
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(True)
        self.window = OverlayWindow(cfg)
        self.window.show()

        # main timer
        interval_ms = max(1, int(1000 / cfg.update_fps))
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(interval_ms)

    def run(self) -> int:
        print('iRadar started.')
        if self.mock:
            print('  Mode: MOCK (simulated data)')
        else:
            print('  Mode: LIVE (waiting for iRacing...)')
        print(f'  Radar size: {self.cfg.radar_size}px')
        print(f'  Range: {self.cfg.range_metres}m')
        print(f'  FPS: {self.cfg.update_fps}')
        print()
        print('  Alt+Click to drag the overlay.')
        print('  Press Ctrl+C in this console to quit.')
        return self.app.exec_()

    # ------------------------------------------------------------------
    #  Main tick
    # ------------------------------------------------------------------

    def _tick(self):
        # 1. read telemetry
        if self.mock:
            snap = self.source.tick()
        else:
            snap = self.source.tick()

        if snap is None or not snap.connected:
            self.window.radar.connected = False
            self.window.radar.set_blips([])
            return

        self.window.radar.connected = True
        self.window.radar.track_name = snap.track_name

        # 2. ensure we have a track spline
        if self.spline is None:
            self._handle_spline_acquisition(snap)
            self.window.radar.set_blips([])
            return

        self.window.radar.spline_ready = True
        self.window.radar.recording_progress = -1

        # 3. keep recording spline data for improving accuracy
        if self.recorder and not self.recorder.complete:
            self.recorder.feed(
                snap.player_lap_dist_pct,
                snap.player_lat,
                snap.player_lon,
            )

        # 4. compute radar blips
        blips = compute_radar(snap, self.spline, self.cfg)

        # 5. send to overlay
        self.window.radar.set_blips(blips)

    # ------------------------------------------------------------------
    #  Spline acquisition (first time on a track)
    # ------------------------------------------------------------------

    def _handle_spline_acquisition(self, snap):
        # try loading from cache
        cached = load_spline(self.cfg, snap.track_name, snap.track_config)
        if cached is not None:
            self.spline = cached
            print(f'  Loaded track spline: {snap.track_name} {snap.track_config}')
            return

        # start recording
        if self.recorder is None:
            print(f'  Recording track: {snap.track_name} {snap.track_config}')
            print('  Please drive one full lap...')
            self.recorder = SplineRecorder(
                sample_interval=self.cfg.spline_sample_interval,
            )
            self._recording_lap = snap.player_lap
            self._last_lap_pct = snap.player_lap_dist_pct

        # feed data
        self.recorder.feed(
            snap.player_lap_dist_pct,
            snap.player_lat,
            snap.player_lon,
        )

        # detect lap crossing
        if snap.player_lap > self._recording_lap and self._recording_lap > 0:
            self.recorder.mark_lap_complete()

        self._last_lap_pct = snap.player_lap_dist_pct

        # update UI
        self.window.radar.recording_progress = self.recorder.progress
        self.window.radar.spline_ready = False

        if self.recorder.complete:
            self.spline = self.recorder.build_and_save(
                self.cfg, snap.track_name, snap.track_config,
            )
            if self.spline:
                print(f'  Track spline saved! ({self.recorder.sample_count} points)')
            else:
                print('  WARNING: failed to build spline, restarting recording...')
                self.recorder = None


# ======================================================================
#  Entry point
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description='iRadar — 360° proximity radar for iRacing')
    parser.add_argument('--mock', action='store_true', help='Use simulated data (for development)')
    parser.add_argument('-n', '--num-opponents', type=int, default=8, help='Number of mock opponents')
    parser.add_argument('--size', type=int, default=None, help='Radar size in pixels')
    parser.add_argument('--range', type=float, default=None, help='Radar range in metres')
    parser.add_argument('--fps', type=int, default=None, help='Update rate')
    args = parser.parse_args()

    cfg = RadarConfig.load()
    if args.size is not None:
        cfg.radar_size = args.size
    if args.range is not None:
        cfg.range_metres = args.range
    if args.fps is not None:
        cfg.update_fps = args.fps

    app = RadarApp(cfg, mock=args.mock, num_opponents=args.num_opponents)
    sys.exit(app.run())


if __name__ == '__main__':
    main()
