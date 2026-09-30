# gRPC Demo：手機遙控模擬萬向輪小車

主角是 **gRPC**。Android 手機傾斜控制 Macbook 上的模擬小車，撞到障礙物時手機依碰撞力度即時振動。Mujoco 是未來的彩蛋（見 `by-agent/decisions.md`）。

```
┌─────────────┐  gRPC（Tailscale 內網）   ┌──────────────┐   介面    ┌─────────────┐
│   Android   │ ◄──────────────────────► │  gRPC Server │ ────────► │  SimCore    │
│  (Kotlin)   │   bidirectional stream    │    (Python)  │           │  (輕量 2D)  │
└─────────────┘    + unary × 2            └──────────────┘           └─────────────┘
                                                                          ▲
                                                      未來可換 Mujoco（彩蛋）
```

## 目錄

| 路徑 | 內容 |
|---|---|
| `proto/remote_sim.proto` | gRPC 契約（唯一事實來源） |
| `server/` | Python server（grpcio）＋輕量物理＋測試 |
| `android/` | Android 原生 App（Kotlin、grpc-kotlin） |
| `by-agent/decisions.md` | 設計決策紀錄（grill-me 產出） |

## gRPC 契約

```proto
service RemoteSim {
  rpc ControlStream(stream ControlInput) returns (stream ServerEvent);  // 雙向：控制上行 / 碰撞+狀態下行
  rpc GetInfo(Empty) returns (ServerInfo);                             // unary：資訊＋metadata
  rpc ResetSim(Empty) returns (SimState);                              // unary：重置＋deadline 演示
}
```

- 碰撞事件已去抖（300ms），帶 `magnitude`（碰撞力度，正規化 0..1）
- 手機斷線 → 伺服器控制權歸零 → 小車漸停（失控保護）
- 純監聽 stream（不送控制）可當 watch-only 端

## Server（Macbook 上跑）

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py            # 監聽 :50051，物理 50Hz
.venv/bin/python tests/client_demo.py # 端到端測試（12 項全過）
```

修改 proto 後重新生成 stub：
```bash
.venv/bin/python -m grpc_tools.protoc -I../proto --python_out=. --grpc_python_out=. ../proto/remote_sim.proto
```

## Android App

```bash
cd android
JAVA_HOME=/usr/lib/jvm/java-17-openjdk ./gradlew :app:assembleDebug
# APK 輸出：app/build/outputs/apk/debug/app-debug.apk
```

用 Android Studio（或 `adb install`）裝到手機。**安裝前必改**：

1. `app/src/main/java/com/example/remotesim/RemoteSimApp.kt` → `SERVER_HOST` 改成你 Macbook 的
   Tailscale MagicDNS 主機名（例如 `macbook.tailxxxxxx.ts.net`；不要寫死 IP）
2. 確認兩端都在同一 Tailscale 網路（req：已解決）

### 操作

- 手持手機前後左右傾斜 → 遙控小車（傾角 → 線速度，不做旋轉）
- 撞到障礙物 → 手機振動（強度/時長隨碰撞力度縮放）
- 上方按鈕：`重置模擬`（1.5s deadline 演示）、`伺服器資訊`（metadata 演示）
- 斷線自動重連（1s→2s→4s→cap 10s），UI 顯示狀態徽章

## 已知事項

- `android/app/build.gradle.kts` 手動註冊了 proto 生成碼至 debug/release source set
  （protobuf-gradle-plugin 與 AGP 8.7/Kotlin 2.0 的整合缺口）
- release 建置未驗證（demo 用 debug）
- 振動的 amplitude/duration↔magnitude 映射曲線在 `MainActivity.vibrateByMagnitude()`，可直接調