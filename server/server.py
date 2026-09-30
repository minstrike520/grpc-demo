"""gRPC 模擬伺服器（Python / grpcio）。

架構（by-agent/decisions.md）：
- 單一全域 SimWorld（一個世界、一台小車）：ticker 以 50Hz 推進物理
- 每條 ControlStream 是一個 Session：attach 接管控制權、detach 時控制權歸零（失控保護）
- 碰撞事件廣播給所有已附接的 stream（demo 只有一台手機，廣播即正確）
- 斷線偵測：server 下行寫入失敗 / request_iterator 結束 → 該 stream 視為死去 → detach → 小車漸停
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import grpc

import remote_sim_pb2 as pb
import remote_sim_pb2_grpc as pb_grpc
from sim_core import SimCore

PHYSICS_HZ = 50
DT = 1.0 / PHYSICS_HZ
STATE_HZ = 10          # 週期狀態推播頻率
STATE_EVERY = PHYSICS_HZ // STATE_HZ
PORT = 50051
SERVER_NAME = "omnibot-sim"
SERVER_VERSION = "0.1.0"
SCENE = "arena-10x10"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("server")


class SimWorld:
    """全域模擬世界：唯一一台萬向輪小車 + ticker + 廣播。

    控制權（controller）語意：
    - 最新一個發過控制更新的 Session 為 controller
    - controller detach（斷線/結束）→ 控制權歸零 => 小車低通漸停（失控保護，3d）
    """

    def __init__(self) -> None:
        self.core = SimCore()
        self._lock = threading.Lock()
        self._queues: set[queue.Queue] = set()
        self._controller: object | None = None
        self._control = (0.0, 0.0)
        self._fps = 0.0
        self._fps_anchor = time.monotonic()
        self._fps_count = 0
        self._running = True
        threading.Thread(target=self._tick_loop, name="sim-ticker", daemon=True).start()

    # ---- Session 管理（由 gRPC handler 呼叫） ----

    def attach(self, session_q: queue.Queue) -> None:
        with self._lock:
            self._queues.add(session_q)
            attached = list(self._queues)  # 是否需要讓快照只送給自己？demo 一台手機，廣播即可
        # 附接即同步：先送目前狀態快照 + SimStatus（重連自動同步，3d 情境②）
        self._broadcast([self._state_event(), self._status_event()], only=session_q)

    def detach(self, session_q: queue.Queue, controller: object) -> None:
        with self._lock:
            self._queues.discard(session_q)
            if self._controller is controller:
                self._controller = None
                self._control = (0.0, 0.0)  # 失控保護：控制權歸零
        self._broadcast([self._status_event()])

    def update_control(self, controller: object, roll: float, pitch: float) -> None:
        with self._lock:
            prev = self._controller
            self._controller = controller
            self._control = (roll, pitch)
        if prev is None:
            self._broadcast([self._status_event()])  # 接管：HALTED → RUNNING

    # ---- ticker ----

    def _tick_loop(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            with self._lock:
                if self._controller is not None:
                    roll, pitch = self._control
                else:
                    roll, pitch = 0.0, 0.0
            try:
                _st, cols = self.core.step(roll, pitch, DT)
            except Exception:
                log.exception("step 失敗")
                time.sleep(DT)
                continue

            evts: list = [pb.ServerEvent(collision=pb.CollisionEvent(magnitude=m)) for m in cols]
            if self.core.step_count % STATE_EVERY == 0:
                evts.append(self._state_event())
            self._broadcast(evts)

            self._fps_count += 1
            now = time.monotonic()
            if now - self._fps_anchor >= 1.0:
                self._fps = self._fps_count / (now - self._fps_anchor)
                self._fps_anchor, self._fps_count = now, 0

            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, DT - elapsed))

    # ---- 事件建構 ----

    def _state_event(self) -> pb.ServerEvent:
        r = self.core.robot
        return pb.ServerEvent(
            state=pb.RobotState(x=r.x, y=r.y, vx=r.vx, vy=r.vy, heading=r.heading)
        )

    def _status_event(self) -> pb.ServerEvent:
        with self._lock:
            running = self._controller is not None
        phase = "RUNNING" if running else "HALTED"
        return pb.ServerEvent(
            status=pb.SimStatus(
                running=running,
                step_count=self.core.step_count,
                fps=self._fps,
                phase=phase,
            )
        )

    def _broadcast(self, evts: list, only: queue.Queue | None = None) -> None:
        if not evts:
            return
        with self._lock:
            targets = [only] if only is not None else list(self._queues)
        for q in targets:
            try:
                for evt in evts:
                    q.put_nowait(evt)
            except queue.Full:
                pass  # 慢 stream 丟棄事件；寫入失敗/滿佇列就是斷線徵兆

    def make_snapshot(self) -> pb.SimState:
        r, obs = self.core.snapshot()
        with self._lock:
            running = self._controller is not None
        return pb.SimState(
            robot=pb.RobotState(x=r.x, y=r.y, vx=r.vx, vy=r.vy, heading=r.heading),
            obstacles=[pb.Obstacle(x=x, y=y, radius=rad) for x, y, rad in obs],
            running=running,
            arena_size=self.core.cfg.arena_size,
        )


class RemoteSimServicer(pb_grpc.RemoteSimServicer):
    def __init__(self, world: SimWorld) -> None:
        self.world = world

    # ---- 主通道：bidirectional streaming（Q1 定案） ----

    def ControlStream(self, request_iterator, context: grpc.ServicerContext):
        client_id = self._client_id(context)
        session_q: queue.Queue = queue.Queue(maxsize=512)
        self.world.attach(session_q)
        context.send_initial_metadata((("session-id", client_id),))
        log.info("session 開始 %s", client_id)

        stop = threading.Event()
        controller_active = [False]  # 是否接管過控制權（決定停送=斷線的語意）

        def _read_control() -> None:
            try:
                for req in request_iterator:
                    self.world.update_control(_self_token, req.roll, req.pitch)
                    controller_active[0] = True
            except StopIteration:
                pass  # 客戶端結束送資料（half-close）
            except Exception as e:
                log.info("request iterator 例外 %s: %r", client_id, e)
            finally:
                # 只有接管過控制的 stream 停送才代表「控制器消失」→ 結束 session（halt）
                # 純監聽（watch-only）stream 保持開著，直到客戶端真正取消/網路死亡
                if controller_active[0]:
                    stop.set()

        # 用物件身份當 controller token（值 identity 即可）
        _self_token = object()
        reader = threading.Thread(target=_read_control, name=f"read-{client_id}", daemon=True)
        reader.start()

        try:
            while not stop.is_set():
                try:
                    evt = session_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                yield evt  # 客戶端死掉時，此 yield 會拋錯 → finally 清理
        except grpc.RpcError:
            log.info("session 斷線（寫入失敗）%s", client_id)
        finally:
            stop.set()
            self.world.detach(session_q, _self_token)
            log.info("session 結束 %s", client_id)

    # ---- unary ----

    def GetInfo(self, request, context: grpc.ServicerContext):
        md = {k: v for k, v in context.invocation_metadata()}
        client_version = md.get("client-version", "unknown")
        client_model = md.get("device-model", "unknown")
        context.send_initial_metadata((("server-version", SERVER_VERSION),))
        log.info("GetInfo <- client-version=%s device=%s", client_version, client_model)
        return pb.ServerInfo(
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
            physics_hz=PHYSICS_HZ,
            max_speed=self.world.core.cfg.max_speed,
            scene=SCENE,
            obstacle_count=len(self.world.core.cfg.obstacles),
            description=(
                f"輕量 2D 物理核心（Mujoco 可替換彩蛋）｜gRPC demo｜"
                f"收到 client-version={client_version} device={client_model}"
            ),
        )

    def ResetSim(self, request, context: grpc.ServicerContext):
        log.info("ResetSim <- %s", self._client_id(context))
        self.world.core.reset()
        return self.world.make_snapshot()

    @staticmethod
    def _client_id(context: grpc.ServicerContext) -> str:
        md = {k: v for k, v in context.invocation_metadata()}
        return md.get("client-id", "anonymous") + "-" + uuid.uuid4().hex[:6]


def serve() -> None:
    world = SimWorld()
    server = grpc.server(thread_pool=ThreadPoolExecutor(max_workers=8))
    pb_grpc.add_RemoteSimServicer_to_server(RemoteSimServicer(world), server)
    server.add_insecure_port(f"0.0.0.0:{PORT}")
    server.start()
    print(f"[gRPC-demo] {SERVER_NAME} v{SERVER_VERSION} 監聽 :{PORT}（物理 {PHYSICS_HZ}Hz）")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()