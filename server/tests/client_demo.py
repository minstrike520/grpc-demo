"""端到端測試客戶端：驗證 server 的三大 RPC 與行為。

用法：先啟動 server（python server.py），再跑本檔：
    .venv/bin/python tests/client_demo.py [host] [port]

驗證項目：
1. ControlStream 雙向：傾角上行 → 碰撞事件（含 magnitude）與狀態下行
2. 失控保護：stream 斷開後，由第二條 stream 觀察小車速度衰減到 0
3. GetInfo（metadata 往返）＋ ResetSim（deadline）
"""
from __future__ import annotations

import queue
import sys
import threading
import time

import grpc

sys.path.insert(0, ".")
import remote_sim_pb2 as pb
import remote_sim_pb2_grpc as pb_grpc

HOST = "127.0.0.1"
PORT = 50051

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {name} {detail}")
    else:
        FAIL += 1
        print(f"  ✘ {name} {detail}")


def make_channel(host: str, port: int) -> grpc.Channel:
    return grpc.insecure_channel(f"{host}:{port}")


def test_control_stream(ch) -> None:
    print("\n[1] ControlStream 雙向流：全速前進撞牆/障礙 → 碰撞 magnitude")
    md = (
        ("client-id", "test-suite"),
        ("client-version", "py-0.1"),
        ("device-model", "test-runner"),
    )
    stub = pb_grpc.RemoteSimStub(ch)

    def gen():
        end = time.time() + 3.0
        while time.time() < end:
            yield pb.ControlInput(roll=0.0, pitch=0.5)  # 全速向前(+y)
            time.sleep(0.03)  # ~33Hz

    events = []
    for evt in stub.ControlStream(gen(), metadata=md, timeout=10):
        events.append(evt)
        if evt.HasField("collision"):
            print(f"    collision magnitude={evt.collision.magnitude:.2f}")

    cols = [e for e in events if e.HasField("collision")]
    states = [e for e in events if e.HasField("state")]
    check("收到碰撞事件（去抖後至少 1 次）", len(cols) >= 1, f"count={len(cols)}")
    check("碰撞 magnitude 在 0..1", all(0.0 <= c.collision.magnitude <= 1.0 for c in cols))
    check("收到週期狀態", len(states) >= 5, f"count={len(states)}")

    # 驗證狀態前進：pitch>0 應往 +y 移動
    if states:
        s0, s1 = states[0].state, states[-1].state
        check("傾角→速度映射：+pitch 確實往 +y 移動", s1.y > s0.y, f"y: {s0.y:.2f} → {s1.y:.2f}")
        # max_speed 夾制
        v = (s1.vx**2 + s1.vy**2) ** 0.5
        check("速度被夾在 max_speed=2.0", v <= 2.0 + 1e-6, f"|v|={v:.3f}")


def test_halt_on_disconnect(ch) -> None:
    print("\n[2] 失控保護：第一條 stream 突然斷開 → 小車速度衰減到 0")
    stub = pb_grpc.RemoteSimStub(ch)

    def gen():
        end = time.time() + 1.0
        while time.time() < end:
            yield pb.ControlInput(roll=0.0, pitch=0.5)
            time.sleep(0.02)

    t1 = threading.Thread(target=lambda: [e for e in stub.ControlStream(gen())])
    t1.start()
    time.sleep(0.5)
    print("  controller 已送出 0.5s 控制，接管控制權並移動中")

    observer_q = queue.Queue()

    def observe():
        try:
            for evt in stub.ControlStream(iter(())):
                observer_q.put(evt)
        except Exception:
            pass

    t2 = threading.Thread(target=observe)
    t2.start()
    t1.join()
    print("  controller stream 已結束（斷線）")
    time.sleep(0.8)

    speeds = []
    drain_until = time.monotonic() + 0.8
    while time.monotonic() < drain_until:
        try:
            evt = observer_q.get(timeout=0.2)
        except queue.Empty:
            continue
        if evt.HasField("state"):
            speeds.append((evt.state.vx**2 + evt.state.vy**2) ** 0.5)

    if len(speeds) >= 2:
        check(
            "斷線後速度衰減（漸停）",
            speeds[-1] < speeds[0],
            f"|v|: {speeds[0]:.3f} → {speeds[-1]:.3f}",
        )
        check("斷線後完全停住", speeds[-1] < 0.05, f"|v|={speeds[-1]:.3f}")
    else:
        check("斷線後速度衰減（漸停）", False, f"樣本不足 n={len(speeds)}")


def test_unary(ch) -> None:
    print("\n[3] GetInfo（metadata 往返）＋ ResetSim（deadline）")
    stub = pb_grpc.RemoteSimStub(ch)

    md = (("client-version", "py-0.1"), ("device-model", "test-runner"))
    info = stub.GetInfo(pb.Empty(), metadata=md)
    check("GetInfo 回覆 server 資訊", info.server_name == "omnibot-sim")
    check("metadata 被 server 收到並帶回", "client-version=py-0.1" in info.description)

    sim_state = stub.ResetSim(
        pb.Empty(), timeout=2.0
    )  # 2s deadline（server 秒回，示範 deadline 參數）
    check("ResetSim 回傳快照", sim_state.running is True or sim_state.running is False)
    check("快照含障礙物清單", len(sim_state.obstacles) == 5, f"n={len(sim_state.obstacles)}")
    check(
        "Reset 後小車回原點",
        abs(sim_state.robot.x) < 1e-6 and abs(sim_state.robot.y) < 1e-6,
        f"({sim_state.robot.x:.2f}, {sim_state.robot.y:.2f})",
    )


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT
    print(f"連接 {host}:{port} ...")
    with make_channel(host, port) as ch:
        test_control_stream(ch)
        test_halt_on_disconnect(ch)
        test_unary(ch)
    print(f"\n結果：PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()