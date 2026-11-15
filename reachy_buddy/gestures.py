"""Expressive gesture library for Reachy Mini.

Conventions used throughout this file (all in the robot's fixed base frame):
  * distances in millimetres, angles in degrees
  * +x forward, +y to the robot's left, +z up
  * +pitch looks down, +yaw turns left, +roll tilts the head
  * antennas(left, right) in degrees: 0 = straight up, + spreads outward (120 droops),
    - leans inward; equal values move both antennas symmetrically

Every gesture ends in the neutral pose, so gestures can be chained in any order.
Between gestures, a background "idle" loop makes the robot breathe gently so it
never looks frozen.
"""

from __future__ import annotations

import functools
import math
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, Sequence

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.reachy_mini import (
    SLEEP_ANTENNAS_JOINT_POSITIONS,
    SLEEP_HEAD_POSE,
)
from reachy_mini.utils import create_head_pose

try:
    from reachy_mini.io.protocol import MoveBusyError
except ImportError:  # older SDKs
    MoveBusyError = RuntimeError  # type: ignore[misc,assignment]

HEAD_Z = 0.177  # metres: neutral head origin above the robot base
REST = 10.0  # degrees: resting antenna angle (matches the SDK's init pose)

# Objects on the table in the simulator's "minimal" scene (metres, base frame).
SCENE_OBJECTS: dict[str, tuple[float, float, float]] = {
    "duck": (0.60, 0.30, 0.03),
    "croissant": (0.60, 0.10, 0.04),
    "apple": (0.60, -0.20, 0.04),
}


def head(x: float = 0, y: float = 0, z: float = 0,
         roll: float = 0, pitch: float = 0, yaw: float = 0) -> np.ndarray:
    """4x4 head pose from millimetres and degrees."""
    return create_head_pose(x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw,
                            mm=True, degrees=True)


def antennas(left: float, right: Optional[float] = None) -> list[float]:
    """Antenna targets in the SDK's [right, left] radian order, from mirrored degrees."""
    if right is None:
        right = left
    return [math.radians(-right), math.radians(left)]


def fade(t: float, total: float, ramp: float = 0.35) -> float:
    """Smooth 0 -> 1 -> 0 envelope so streamed motions start and stop exactly at rest."""
    r = min(ramp, total / 2)
    if r <= 0:
        return 0.0
    x = min(1.0, max(0.0, min(t / r, (total - t) / r)))
    return x * x * (3 - 2 * x)  # smoothstep


def _sin(freq_hz: float, t: float, phase: float = 0.0) -> float:
    return math.sin(2 * math.pi * freq_hz * t + phase)


StreamFn = Callable[[float], tuple[np.ndarray, Sequence[float], Optional[float]]]

GESTURES: dict[str, str] = {}  # name -> description, filled by @gesture


def gesture(description: str):
    """Register a method as a named gesture that owns the robot while it runs."""

    def decorator(fn):
        GESTURES[fn.__name__] = description

        @functools.wraps(fn)
        def wrapper(self: "Body", *args, **kwargs):
            with self._busy(fn.__name__):
                return fn(self, *args, **kwargs)

        return wrapper

    return decorator


class Body:
    """All of Reachy Mini's motion: named gestures plus idle breathing in between."""

    def __init__(self, mini: ReachyMini, sounds: bool = True) -> None:
        self.mini = mini
        self.sounds = sounds
        self.asleep = self._looks_asleep()
        self.current: Optional[str] = None  # name of the running gesture
        self.on_gesture: Optional[Callable[[Optional[str]], None]] = None

        self._lock = threading.RLock()
        self._depth = 0
        self._idle_enabled = False
        self._idle_t0 = time.monotonic()
        self._stop = threading.Event()
        self._idle_thread = threading.Thread(target=self._idle_loop, name="idle", daemon=True)
        self._idle_thread.start()

    # ------------------------------------------------------------------ plumbing
    def _looks_asleep(self) -> bool:
        """True if the head is currently resting in the sleep pose."""
        try:
            pose = self.mini.get_current_head_pose()
        except Exception:
            return False
        # The sleep pose sits ~44 mm below neutral with the head pitched ~25 deg down.
        dpos = np.linalg.norm(pose[:3, 3] - SLEEP_HEAD_POSE[:3, 3])
        cos = (np.trace(pose[:3, :3].T @ SLEEP_HEAD_POSE[:3, :3]) - 1) / 2
        drot = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        return bool(dpos < 0.015 and drot < 15)

    def close(self) -> None:
        self._stop.set()
        self._idle_thread.join(timeout=1)

    def idle(self, enabled: bool = True) -> None:
        """Turn the between-gesture breathing on or off."""
        self._idle_t0 = time.monotonic()
        self._idle_enabled = enabled

    @contextmanager
    def _busy(self, name: str) -> Iterator[None]:
        with self._lock:
            self._depth += 1
            outer = self._depth == 1
            if outer:
                self.current = name
                if self.on_gesture:
                    self.on_gesture(name)
            try:
                yield
            finally:
                self._depth -= 1
                if outer:
                    self.current = None
                    self._idle_t0 = time.monotonic()  # breathing restarts from rest
                    if self.on_gesture:
                        self.on_gesture(None)

    def _idle_loop(self) -> None:
        while not self._stop.is_set():
            if self._idle_enabled and not self.asleep and self._lock.acquire(blocking=False):
                try:
                    t = time.monotonic() - self._idle_t0
                    ease = min(1.0, t / 1.5)
                    breath = _sin(0.22, t)
                    pose = head(z=2.5 * ease * breath, pitch=-1.5 * ease * breath,
                                yaw=5 * ease * _sin(0.07, t))
                    a = REST + 5 * ease * breath
                    self.mini.set_target(head=pose, antennas=antennas(a, a))
                except Exception:
                    pass  # a dropped idle frame is harmless
                finally:
                    self._lock.release()
            time.sleep(0.02)

    def goto(self, pose: Optional[np.ndarray] = None, ants: Optional[Sequence[float]] = None,
             duration: float = 0.5, method: str = "minjerk", body_yaw: Optional[float] = 0.0) -> None:
        """Interpolated move (blocking). Retries briefly if another move is still running."""
        for _ in range(50):
            try:
                self.mini.goto_target(head=pose, antennas=ants, duration=duration,
                                      method=method, body_yaw=body_yaw)
                return
            except MoveBusyError:
                time.sleep(0.1)
        raise RuntimeError("The robot stayed busy with another move for 5 s")

    def stream(self, fn: StreamFn, duration: float, hz: float = 50.0) -> None:
        """Drive the robot from a function of time with set_target (for rhythmic motion)."""
        t0 = time.monotonic()
        while (t := time.monotonic() - t0) < duration:
            pose, ants, yaw = fn(t)
            self.mini.set_target(head=pose, antennas=list(ants), body_yaw=yaw)
            time.sleep(1.0 / hz)
        pose, ants, yaw = fn(duration)
        self.mini.set_target(head=pose, antennas=list(ants), body_yaw=yaw)

    def play(self, sound: str) -> None:
        """Play one of the SDK's built-in sounds (silently skipped without audio)."""
        if not self.sounds:
            return
        try:
            self.mini.media.play_sound(sound)
        except Exception:
            pass

    def perform(self, name: str, *args) -> None:
        if name not in GESTURES:
            raise KeyError(f"Unknown gesture {name!r}")
        getattr(self, name)(*args)

    # ------------------------------------------------------------------ gestures
    @gesture("Return to the neutral pose")
    def neutral(self, duration: float = 0.8) -> None:
        self.goto(head(), antennas(REST), duration)

    @gesture("Quietly slump into the sleep pose (no sound)")
    def rest_pose(self) -> None:
        if self.asleep:
            return
        self.goto(head(), antennas(REST), 1.0)
        self.goto(SLEEP_HEAD_POSE, list(SLEEP_ANTENNAS_JOINT_POSITIONS), 1.8)
        self.asleep = True

    @gesture("Wake up and stretch")
    def wake_up(self) -> None:
        self.goto(head(), antennas(REST), 2.0)
        self.asleep = False
        self.play("wake_up.wav")
        self.goto(head(roll=20), duration=0.2)
        self.goto(head(), duration=0.2)
        self.goto(head(z=12, pitch=-12), antennas(0), 0.9, "ease_in_out")  # stretch
        time.sleep(0.3)
        self.goto(head(), antennas(REST), 0.6)

    @gesture("Yawn, then go to sleep")
    def sleep(self) -> None:
        self.goto(head(z=10, pitch=-20), antennas(0), 0.9, "ease_in_out")  # yawn
        time.sleep(0.6)
        self.goto(head(), antennas(REST), 0.6)
        if self.sounds:
            self.mini.goto_sleep()  # SDK move: plays the "go to sleep" sound
        else:
            self.goto(SLEEP_HEAD_POSE, list(SLEEP_ANTENNAS_JOINT_POSITIONS), 2.0)
        self.asleep = True

    @gesture("Friendly hello: head tilts and antennas wave")
    def hello(self) -> None:
        self.goto(head(z=8, roll=-12, pitch=-6), antennas(-25, 45), 0.35, "cartoon")
        for _ in range(2):
            self.goto(head(z=8, roll=12, pitch=-6), antennas(45, -25), 0.32)
            self.goto(head(z=8, roll=-12, pitch=-6), antennas(-25, 45), 0.32)
        self.neutral(0.5)

    @gesture("Nod yes")
    def nod_yes(self, times: int = 2) -> None:
        for _ in range(times):
            self.goto(head(z=-3, pitch=18), antennas(25), 0.22)
            self.goto(head(z=2, pitch=-6), antennas(5), 0.22)
        self.neutral(0.35)

    @gesture("Shake head no")
    def shake_no(self, times: int = 2) -> None:
        for _ in range(times):
            self.goto(head(yaw=24, pitch=3), antennas(30, 0), 0.26)
            self.goto(head(yaw=-24, pitch=3), antennas(0, 30), 0.26)
        self.neutral(0.35)

    @gesture("Happy: bouncy wiggle with flapping antennas")
    def happy(self, duration: float = 2.2) -> None:
        def f(t):
            e = fade(t, duration)
            pose = head(z=7 * e * abs(_sin(1.5, t)), roll=8 * e * _sin(1.5, t), pitch=-6 * e)
            a = 40 * e * _sin(3.0, t)
            return pose, antennas(REST + a, REST - a), 0.0
        self.stream(f, duration)
        self.neutral(0.4)

    @gesture("Laugh: head back, little shaking giggles")
    def laugh(self, duration: float = 1.8) -> None:
        def f(t):
            e = fade(t, duration)
            pose = head(z=6 * e + 3 * e * _sin(6, t), pitch=-14 * e, roll=4 * e * _sin(3, t))
            a = REST + 15 * e * _sin(6, t)
            return pose, antennas(a), 0.0
        self.stream(f, duration)
        self.neutral(0.4)

    @gesture("Sad: droop down, antennas hang low")
    def sad(self) -> None:
        self.goto(head(z=-10, pitch=24, roll=6), antennas(120), 1.8, "ease_in_out")
        time.sleep(0.6)
        self.goto(head(z=-12, pitch=28, roll=-5), antennas(130, 110), 1.2, "ease_in_out")  # sigh
        time.sleep(0.4)
        self.neutral(1.2)

    @gesture("Comfort: soft tilt and slow understanding nods")
    def comfort(self) -> None:
        self.goto(head(roll=-14, pitch=6, x=6), antennas(45), 0.9, "ease_in_out")
        for _ in range(2):
            self.goto(head(roll=-14, pitch=14, x=6), antennas(50), 0.55, "ease_in_out")
            self.goto(head(roll=-14, pitch=4, x=6), antennas(40), 0.55, "ease_in_out")
        self.neutral(0.9)

    @gesture("Surprised: jump back, antennas shoot up")
    def surprised(self) -> None:
        self.goto(head(x=-8, z=15, pitch=-14), antennas(0), 0.18, "cartoon")
        time.sleep(0.7)
        self.goto(ants=antennas(30), duration=0.15, body_yaw=None)
        self.goto(ants=antennas(0), duration=0.15, body_yaw=None)
        time.sleep(0.3)
        self.neutral(0.8)

    @gesture("Curious: tilt the head one way, then the other")
    def curious(self) -> None:
        self.goto(head(x=8, roll=-20, pitch=-5, yaw=10), antennas(0, 45), 0.6)
        time.sleep(0.5)
        self.goto(head(x=8, roll=16, pitch=-5, yaw=-8), antennas(45, 0), 0.7)
        time.sleep(0.4)
        self.neutral(0.6)

    @gesture("Thinking: look up and to the side, antennas 'process'")
    def thinking(self, duration: float = 2.0) -> None:
        pose = head(pitch=-16, yaw=20, roll=-8)
        self.goto(pose, antennas(REST), 0.8)

        def f(t):
            a = 30 * fade(t, duration) * _sin(1.0, t)
            return pose, antennas(REST + a, REST - a), 0.0
        self.stream(f, duration)
        self.neutral(0.7)

    @gesture("Confused: head tilts side to side")
    def confused(self) -> None:
        self.play("confused1.wav")
        self.goto(head(roll=22, yaw=8), antennas(60, -10), 0.5)
        time.sleep(0.35)
        self.goto(head(roll=-22, yaw=-8), antennas(-10, 60), 0.6)
        time.sleep(0.35)
        self.neutral(0.6)

    @gesture("Shy: look down and away, antennas fold")
    def shy(self) -> None:
        self.goto(head(z=-4, pitch=16, roll=16, yaw=-18), antennas(70), 0.9, "ease_in_out")

        def f(t):
            e = fade(t, 1.6)
            return head(z=-4, pitch=16, roll=16 + 5 * e * _sin(1.2, t), yaw=-18), antennas(70), 0.0
        self.stream(f, 1.6)
        self.neutral(0.8)

    @gesture("Scan the room left to right, body turning too")
    def look_around(self) -> None:
        self.goto(head(yaw=45, pitch=8), antennas(25, 0), 1.1, body_yaw=math.radians(20))
        time.sleep(0.4)
        self.goto(head(yaw=-40, pitch=8), antennas(0, 25), 1.8, body_yaw=math.radians(-18))
        time.sleep(0.4)
        self.neutral(1.0)

    @gesture("Look at an object on the table: duck, croissant or apple")
    def look_at(self, name: str = "duck", hold: float = 1.6) -> None:
        x, y, z = SCENE_OBJECTS[name]
        pose = self.mini.look_at_world(x, y, z - HEAD_Z, perform_movement=False)
        body = 0.5 * math.atan2(y, x)  # turn the body halfway, the head does the rest
        self.goto(pose, antennas(0), 0.9, body_yaw=body)

        def f(t):
            a = 25 * fade(t, hold) * _sin(2.5, t)
            return pose, antennas(a, -a), body
        self.stream(f, hold)
        self.neutral(0.9)

    @gesture("Dance to the beat: bob, sway, twist and flap")
    def dance(self, beats: int = 16, bpm: float = 112) -> None:
        self.play("dance1.wav")
        period = 60.0 / bpm
        total = beats * period

        def f(t):
            b = t / period  # time in beats
            e = fade(t, total, ramp=0.6)
            twist = 18 * e * math.sin(math.pi * b / 2)  # body turns every 2 beats
            pose = head(z=8 * e * math.sin(2 * math.pi * b),
                        roll=12 * e * math.sin(math.pi * b),
                        yaw=twist * 1.6)
            a = 40 * e * math.sin(2 * math.pi * b)
            return pose, antennas(REST + a, REST - a), math.radians(twist)
        self.stream(f, total)
        self.neutral(0.6)

    @gesture("Wave goodbye with a little bow")
    def goodbye(self) -> None:
        self.hello()
        self.goto(head(z=-8, pitch=28), antennas(80), 0.9, "ease_in_out")
        time.sleep(0.5)
        self.neutral(0.8)
