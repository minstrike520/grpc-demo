"""輕量 2D 物理核心 — SimCore（與 gRPC 層完全解耦）。

設計契約（by-agent/decisions.md Q2）：
- 主角是 gRPC；本核心只是打通全鏈路的最小實作
- 未來換 Mujoco 時，只需提供相同介面的模組（心跳替換），gRPC/proto 層完全不動
- 介面 = step() / reset() / snapshot()
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Tuple

MAX_SPEED = 2.0          # m/s
ARENA_SIZE = 10.0        # 正方形場地邊長
ROBOT_RADIUS = 0.3
OBSTACLE_RADIUS = 0.5
DEBOUNCE = 0.30          # 碰撞去抖（3b 定案）
MAX_TILT_RAD = 0.5       # 滿傾角(約 28.6°) 對應 max_speed
VELOCITY_LOWPASS = 0.30  # 速度低通係數（手感）

# 固定場景：5 顆圓形障礙物（對稱放置）
DEFAULT_OBSTACLES: List[Tuple[float, float]] = [
    (-3.0, 0.0),
    ( 3.0, 0.0),
    ( 0.0, 3.0),
    ( 0.0, -3.0),
    (-2.0, 2.5),
]


@dataclass
class RobotState:
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    heading: float = 0.0  # atan2(vy, vx)，弧度


@dataclass
class SimConfig:
    max_speed: float = MAX_SPEED
    arena_size: float = ARENA_SIZE
    robot_radius: float = ROBOT_RADIUS
    obstacle_radius: float = OBSTACLE_RADIUS
    debounce: float = DEBOUNCE
    max_tilt_rad: float = MAX_TILT_RAD
    velocity_lowpass: float = VELOCITY_LOWPASS
    obstacles: List[Tuple[float, float]] = field(
        default_factory=lambda: list(DEFAULT_OBSTACLES)
    )


class SimCore:
    """圓盤小車 + 圓形障礙物 + 場地牆壁的輕量物理。

    step() 回傳 (state, collision_magnitudes[])：
    - collision magnitude 正規化 0..1（碰撞當下速度/ max_speed）
    - 碰撞已去抖（debounce 秒內只回報一次）
    """

    def __init__(self, config: SimConfig | None = None):
        self.cfg = config or SimConfig()
        self.robot = RobotState()
        self.step_count = 0
        self._last_collision_at = -1e9  # wall-clock，用於去抖

    # ---- 介面：gRPC 層唯一會呼叫的三個方法 ----

    def step(self, roll: float, pitch: float, dt: float) -> Tuple[RobotState, List[float]]:
        """輸入傾角（弧度），推進 dt 秒。回傳 (狀態, 碰撞力度列表)。"""
        # 傾角 → 目標速度：線性映射並夾在 max_speed（3a 定案：不做旋轉）
        k = self.cfg.max_speed / self.cfg.max_tilt_rad
        tx = roll * k
        ty = pitch * k
        speed = math.hypot(tx, ty)
        if speed > self.cfg.max_speed:
            tx *= self.cfg.max_speed / speed
            ty *= self.cfg.max_speed / speed

        # 速度低通（手感、也讓斷線 halt 是「漸停」而非急煞）
        a = self.cfg.velocity_lowpass
        self.robot.vx += (tx - self.robot.vx) * a
        self.robot.vy += (ty - self.robot.vy) * a

        self.robot.x += self.robot.vx * dt
        self.robot.y += self.robot.vy * dt
        if self.robot.vx != 0.0 or self.robot.vy != 0.0:
            self.robot.heading = math.atan2(self.robot.vy, self.robot.vx)

        self.step_count += 1
        collisions: List[float] = []

        def emit(impact_speed: float) -> None:
            now = time.monotonic()
            if now - self._last_collision_at < self.cfg.debounce:
                return
            norm = min(1.0, impact_speed / self.cfg.max_speed)
            if norm >= 0.05:  # 蠕行貼邊不算碰撞
                collisions.append(norm)
                self._last_collision_at = now

        # 障礙物碰撞：圓-圓，彈性反射
        r = self.cfg.robot_radius
        rr = r + self.cfg.obstacle_radius
        for ox, oy in self.cfg.obstacles:
            dx = self.robot.x - ox
            dy = self.robot.y - oy
            d = math.hypot(dx, dy)
            if d >= rr or d < 1e-9:
                continue
            nx, ny = dx / d, dy / d
            # 把車推出接觸點（避免黏住）
            self.robot.x, self.robot.y = ox + nx * rr, oy + ny * rr
            vn = self.robot.vx * nx + self.robot.vy * ny
            if vn < 0.0:
                self.robot.vx -= 2.0 * vn * nx
                self.robot.vy -= 2.0 * vn * ny
                emit(-vn)

        # 場地牆壁：夾住 + 反射
        half = self.cfg.arena_size / 2.0 - r
        for axis, (pos, vel) in enumerate(
            ((self.robot.x, self.robot.vx), (self.robot.y, self.robot.vy))
        ):
            if pos < -half:
                self.robot.x, self.robot.y = self._set_axis(axis, -half)
                if vel < 0.0:
                    self._reflect_axis(axis, 2)
                    emit(-vel)
            elif pos > half:
                self.robot.x, self.robot.y = self._set_axis(axis, half)
                if vel > 0.0:
                    self._reflect_axis(axis, 2)
                    emit(vel)

        return self.robot, collisions

    def reset(self) -> None:
        """重置模擬（ResestSim unary 用）。"""
        self.robot = RobotState()
        self.step_count = 0
        self._last_collision_at = -1e9

    def snapshot(self) -> Tuple[RobotState, List[Tuple[float, float, float]]]:
        """回傳 (機器人狀態, 障礙物清單[(x, y, radius)])。"""
        obs = [(x, y, self.cfg.obstacle_radius) for x, y in self.cfg.obstacles]
        return self.robot, obs

    # ---- 內部小工具 ----

    def _set_axis(self, axis: int, value: float) -> Tuple[float, float]:
        if axis == 0:
            self.robot.x = value
        else:
            self.robot.y = value
        return self.robot.x, self.robot.y

    def _reflect_axis(self, axis: int, factor: float) -> None:
        if axis == 0:
            self.robot.vx = -self.robot.vx * factor
        else:
            self.robot.vy = -self.robot.vy * factor