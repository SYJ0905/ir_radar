"""
iRadar configuration — all tunable parameters in one place.
"""

from dataclasses import dataclass
from typing import Tuple
import json
import os
import sys


def _app_dir() -> str:
    """Return the directory where the exe (or main.py) lives."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


CONFIG_FILE = os.path.join(_app_dir(), 'config.json')


@dataclass
class RadarConfig:
    # --- Window ---
    # position_preset: 'center-top', 'center', 'top-left', 'top-right', 'custom'
    # When not 'custom', window_x/y are auto-calculated on startup.
    position_preset: str = 'center-top'
    window_x: int = -1  # -1 = auto from preset
    window_y: int = -1
    radar_size: int = 280

    # --- Radar range ---
    # How many metres ahead / behind / sideways to show on the radar
    range_metres: float = 40.0

    # --- Opacity ---
    active_opacity: float = 0.92      # when cars are nearby
    inactive_opacity: float = 0.25    # when no cars nearby
    fade_speed: float = 0.08          # opacity change per frame (0‒1)

    # --- Colours (RGBA 0‒255) ---
    bg_colour: Tuple[int, ...] = (10, 10, 10, 180)
    ring_colour: Tuple[int, ...] = (80, 80, 80, 160)
    self_colour: Tuple[int, ...] = (0, 200, 255, 255)
    car_colour_far: Tuple[int, ...] = (255, 180, 0, 220)    # orange
    car_colour_near: Tuple[int, ...] = (255, 40, 40, 255)    # red
    car_colour_lapped: Tuple[int, ...] = (80, 80, 255, 200)  # blue (lapped car)

    # --- Thresholds ---
    danger_distance: float = 8.0     # metres — switch to red
    nearby_distance: float = 30.0    # metres — considered "nearby" for opacity
    lateral_estimate: float = 3.5    # estimated lateral offset when CarLeftRight fires

    # --- Car drawing ---
    self_car_length: float = 4.5     # metres (real size, used for collision)
    self_car_width: float = 1.9
    other_car_length: float = 4.5
    other_car_width: float = 1.9
    car_visual_scale: float = 2.2    # multiplier for drawing only (not collision)

    # --- Smoothing ---
    smooth_factor: float = 0.5      # lerp factor per frame (higher = snappier)

    # --- Update ---
    update_fps: int = 60

    def __post_init__(self):
        # convert plain lists back to tuples after JSON deserialisation
        for attr in ('bg_colour', 'ring_colour', 'self_colour',
                     'car_colour_far', 'car_colour_near', 'car_colour_lapped'):
            val = getattr(self, attr)
            if isinstance(val, list):
                setattr(self, attr, tuple(val))

    # --- Persistence ---
    def save(self):
        data = {}
        for k, v in self.__dict__.items():
            data[k] = list(v) if isinstance(v, tuple) else v
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def compute_position(self, screen_width: int, screen_height: int):
        """Resolve window_x/y from position_preset if not 'custom'."""
        if self.position_preset == 'custom' and self.window_x >= 0:
            return
        half = self.radar_size // 2
        presets = {
            'center-top':  (screen_width // 2 - half, int(screen_height * 0.28)),
            'center':      (screen_width // 2 - half, screen_height // 2 - half),
            'top-left':    (20, 20),
            'top-right':   (screen_width - self.radar_size - 20, 20),
            'bottom-left': (20, screen_height - self.radar_size - 20),
            'bottom-right':(screen_width - self.radar_size - 20,
                            screen_height - self.radar_size - 20),
        }
        pos = presets.get(self.position_preset, presets['center-top'])
        self.window_x, self.window_y = pos

    @classmethod
    def load(cls) -> 'RadarConfig':
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return cls(**data)
        return cls()
