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
from server.track_spline import (
    load_spline, create_mock_spline,
    SplineRecorder, TrackSpline,
)

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
        self._current_track: str = ''

        self._logged_connect = False
        self._diag_counter = 0

        # if mock, generate spline immediately
        if mock:
            self.spline = create_mock_spline()

        # Qt app + overlay
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.window = OverlayWindow(cfg)
        self.window.show()

        # main timer
        interval_ms = max(1, int(1000 / cfg.update_fps))
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(interval_ms)

        # periodically re-raise window to stay above iRacing
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

    # ------------------------------------------------------------------
    #  Main tick
    # ------------------------------------------------------------------

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

        # detect track change (e.g., user left one session and joined another)
        track_key = f'{snap.track_name}_{snap.track_config}'
        if snap.track_name and track_key != self._current_track:
            if self._current_track:
                log.info('Track changed: %s -> %s', self._current_track, track_key)
                self.spline = None
                self.recorder = None
                self._recording_lap = -1
            self._current_track = track_key
            self._logged_connect = False

        # log connection once
        if not self._logged_connect:
            log.info('Connected! track=%s config=%s track_len=%.0fm '
                     'player_idx=%d lat=%.6f lon=%.6f',
                     snap.track_name, snap.track_config, snap.track_length,
                     snap.player_car_idx, snap.player_lat, snap.player_lon)
            total_cars = len(snap.cars)
            on_track = sum(1 for c in snap.cars if c.on_track and not c.on_pit_road)
            log.info('Cars in session: total=%d on_track=%d', total_cars, on_track)
            self._logged_connect = True

        # periodic diagnostics (every 5 seconds)
        self._diag_counter += 1
        if self._diag_counter % (self.cfg.update_fps * 5) == 0:
            on_track_cars = [(c.car_idx, c.lap_dist_pct, c.lap)
                             for c in snap.cars
                             if c.on_track and not c.on_pit_road
                             and c.car_idx != snap.player_car_idx]
            blips = self.window.radar.blips
            log.info('DIAG lap=%d pct=%.4f lat=%.6f lon=%.6f yaw=%.3f '
                     'cars_on_track=%d spline=%s blips=%d',
                     snap.player_lap, snap.player_lap_dist_pct,
                     snap.player_lat, snap.player_lon, snap.player_yaw,
                     len(on_track_cars),
                     'ready(%.0fm)' % self.spline.spatial_extent() if self.spline else 'none',
                     len(blips))
            if on_track_cars:
                for idx, pct, lap in on_track_cars[:5]:
                    log.info('  car[%d] pct=%.4f lap=%d', idx, pct, lap)
            if blips:
                for b in blips[:5]:
                    log.info('  blip[%d] rx=%.1f ry=%.1f dist=%.1f lapped=%s',
                             b.car_idx, b.rx, b.ry, b.distance, b.is_lapped)

        # ensure we have a track spline
        if self.spline is None:
            self._handle_spline_acquisition(snap)
            self.window.radar.set_blips([])
            return

        self.window.radar.spline_ready = True
        self.window.radar.recording_progress = -1

        if self.recorder and not self.recorder.complete:
            self.recorder.feed(
                snap.player_lap_dist_pct,
                snap.player_lat,
                snap.player_lon,
            )

        blips = compute_radar(snap, self.spline, self.cfg)
        self.window.radar.set_blips(blips)

    # ------------------------------------------------------------------
    #  Spline acquisition (first time on a track)
    # ------------------------------------------------------------------

    def _handle_spline_acquisition(self, snap):
        from server.track_spline import spline_path
        import os as _os
        cached = load_spline(self.cfg, snap.track_name, snap.track_config)
        if cached is not None:
            extent = cached.spatial_extent()
            log.info('Loaded cached spline: %s %s — %d points, extent=%.1fm',
                     snap.track_name, snap.track_config,
                     len(cached.pcts), extent)
            if cached.is_valid():
                self.spline = cached
                print(f'  Loaded track spline: {snap.track_name} {snap.track_config}')
                return
            else:
                bad_path = spline_path(self.cfg, snap.track_name, snap.track_config)
                log.warning('Cached spline INVALID (extent=%.1fm) — deleting %s',
                            extent, bad_path)
                try:
                    _os.remove(bad_path)
                except OSError:
                    pass

        if self.recorder is None:
            sp = spline_path(self.cfg, snap.track_name, snap.track_config)
            log.info('No cached spline at %s — starting recording. '
                     'track=%s config=%s player_lap=%d',
                     sp, snap.track_name, snap.track_config, snap.player_lap)
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

        # log recording progress periodically
        if self.recorder.sample_count in (1, 10, 50):
            log.info('Recording sample #%d: pct=%.4f lat=%.6f lon=%.6f',
                     self.recorder.sample_count, snap.player_lap_dist_pct,
                     snap.player_lat, snap.player_lon)

        # detect lap crossing (S/F line)
        if snap.player_lap > self._recording_lap and self._recording_lap >= 0:
            self.recorder.mark_lap_complete()
            log.info('Lap crossing detected: lap %d -> %d, samples=%d',
                     self._recording_lap, snap.player_lap, self.recorder.sample_count)

        self._last_lap_pct = snap.player_lap_dist_pct

        # update UI
        self.window.radar.recording_progress = self.recorder.progress
        self.window.radar.spline_ready = False

        if self.recorder.complete:
            self.spline = self.recorder.build_and_save(
                self.cfg, snap.track_name, snap.track_config,
            )
            if self.spline:
                ext = self.spline.spatial_extent()
                log.info('Track spline saved! %d points, extent=%.1fm, valid=%s',
                         self.recorder.sample_count, ext, self.spline.is_valid())
                print(f'  Track spline saved! ({self.recorder.sample_count} points, {ext:.0f}m extent)')
            else:
                log.warning('Failed to build spline, restarting recording...')
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

    try:
        app = RadarApp(cfg, mock=args.mock, num_opponents=args.num_opponents)
        sys.exit(app.run())
    except Exception:
        log.critical('Fatal error:\n%s', traceback.format_exc())
        raise


if __name__ == '__main__':
    main()
