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
import logging
import os
import sys
import traceback

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from server.config import RadarConfig
from server.overlay import OverlayWindow
from server.radar_calc import compute_radar

def _app_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(_app_dir(), 'iradar.log')
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger('iRadar')


class RadarApp:
    """Main application loop: telemetry → radar calc → overlay draw."""

    def __init__(self, cfg: RadarConfig, mock: bool = False, num_opponents: int = 8):
        self.cfg = cfg
        self.mock = mock

        if mock:
            from server.mock_data import MockTelemetrySource
            self.source = MockTelemetrySource(num_opponents=num_opponents)
        else:
            from server.telemetry import TelemetryReader
            self.source = TelemetryReader()

        self._logged_connect = False
        self._diag_counter = 0

        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.window = OverlayWindow(cfg)
        self.window.show()

        interval_ms = max(1, int(1000 / cfg.update_fps))
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(interval_ms)

        self._raise_timer = QTimer()
        self._raise_timer.timeout.connect(self.window.ensure_on_top)
        self._raise_timer.start(2000)

    def run(self) -> int:
        mode = 'MOCK' if self.mock else 'LIVE'
        log.info('iRadar started. Mode=%s  Size=%dpx  Range=%.0fm  FPS=%d',
                 mode, self.cfg.radar_size, self.cfg.range_metres, self.cfg.update_fps)
        print('iRadar started.')
        if self.mock:
            print('  Mode: MOCK (simulated data)')
        else:
            print('  Mode: LIVE (waiting for iRacing...)')
        print(f'  Radar size: {self.cfg.radar_size}px')
        print(f'  Range: {self.cfg.range_metres}m')
        print(f'  FPS: {self.cfg.update_fps}')
        print(f'  Log file: {LOG_FILE}')
        print()
        print('  Alt+Click to drag the overlay.')
        print('  Right-click tray icon to quit.')
        return self.app.exec_()

    def _tick(self):
        try:
            self._tick_inner()
        except Exception:
            log.error('Tick error:\n%s', traceback.format_exc())
            self.window.radar.connected = False
            self.window.radar.set_blips([])

    def _tick_inner(self):
        snap = self.source.tick()

        if snap is None or not snap.connected:
            self.window.radar.connected = False
            self.window.radar.set_blips([])
            if self._logged_connect:
                log.info('Disconnected from iRacing')
                self._logged_connect = False
            return

        self.window.radar.connected = True
        self.window.radar.track_name = snap.track_name
        self.window.radar.spline_ready = True
        self.window.radar.recording_progress = -1

        if not self._logged_connect:
            on_track = sum(1 for c in snap.cars if c.on_track and not c.on_pit_road)
            log.info('Connected! track=%s config=%s track_len=%.0fm '
                     'player_idx=%d cars_on_track=%d',
                     snap.track_name, snap.track_config, snap.track_length,
                     snap.player_car_idx, on_track)
            self._logged_connect = True

        # periodic diagnostics
        self._diag_counter += 1
        if self._diag_counter % (self.cfg.update_fps * 5) == 0:
            on_track_cars = [(c.car_idx, c.lap_dist_pct, c.lap)
                             for c in snap.cars
                             if c.on_track and not c.on_pit_road
                             and c.car_idx != snap.player_car_idx]
            blips = self.window.radar.blips
            log.info('DIAG lap=%d pct=%.4f track_len=%.0fm '
                     'cars_on_track=%d blips=%d',
                     snap.player_lap, snap.player_lap_dist_pct,
                     snap.track_length,
                     len(on_track_cars), len(blips))
            for b in blips[:5]:
                log.info('  blip[%d] rx=%.1f ry=%.1f dist=%.1f lapped=%s',
                         b.car_idx, b.rx, b.ry, b.distance, b.is_lapped)

        blips = compute_radar(snap, self.cfg)
        self.window.radar.set_blips(blips)


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

    try:
        app = RadarApp(cfg, mock=args.mock, num_opponents=args.num_opponents)
        sys.exit(app.run())
    except Exception:
        log.critical('Fatal error:\n%s', traceback.format_exc())
        raise


if __name__ == '__main__':
    main()
