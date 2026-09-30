# gRPC Demo 決策紀錄

> 來源：`docs/req.md` × grill-me 審問 session（2025/10 起）
> 狀態：📌 已定案。實作時若需修改，請先更新本檔。

---

## 專案定位（最重要的一條）

**主角是 gRPC。** 目標是「展現 gRPC 能力與特性」。機器人（萬向輪小車）、Mujoco、虛實整合都只是加分彩蛋，用來呼應後續「gRPC 應用於機器人訓練虛實整合環境」的主題。所有工程決策以此為準：夠用就好、把 gRPC 的故事講漂亮。

## Q1｜gRPC 通訊模式

### 決策：Bi-directional streaming × 1 + unary × 2

**主通道 `ControlStream`（bidirectional）**
- 手機 → 伺服器：連續送傾角控制輸入（上行，20~30Hz）
- 伺服器 → 手機：瞬間推送 `CollisionEvent`（即時振動的必要條件）、`RobotState`、`SimStatus`

**unary**
- `GetInfo(Empty) → ServerInfo`：連線資訊、server 端資訊，搭配 **metadata** 展示（例如 client version、session id）
- `ResetSim(Empty) → SimState`：重置模擬，展示 **deadline、錯誤碼、重試** 等 gRPC 特性

### proto 草稿（方向性，欄位待實作細修）

```proto
service RemoteSim {
  rpc ControlStream(stream ControlInput) returns (stream ServerEvent);
  rpc GetInfo(Empty) returns (ServerInfo);
  rpc ResetSim(Empty) returns (SimState);
}

message ControlInput {
  // roll/pitch 傾角（rad），非角速度
  float roll = 1;
  float pitch = 2;
}

message ServerEvent {
  oneof event {
    CollisionEvent collision = 1;  // 去抖後推送
    RobotState state = 2;          // 週期性狀態同步
    SimStatus status = 3;          // 模擬狀態變更
  }
}

message CollisionEvent {
  float magnitude = 1;  // 碰撞力度（正規化 ~0..1）
}

message ServerInfo { ... }
message SimState { ... }
message RobotState { ... }
message SimStatus { ... }
```

### 理由
- 雙向 streaming 是 gRPC 最亮眼的特性之一，最值得展
- 碰撞需要「即時」送到手機 → server-push 才有真即時，polling 做不到
- Android 端 grpc-kotlin coroutines 對双向 stream 支援好，`Flow` 收事件寫起來乾淨
- 多兩條 unary 補齊 gRPC 其他特性（metadata、deadline、錯誤碼）的展示面

---

## Q2｜模擬伺服器整合深度

### 決策：模擬核心與 gRPC 層解耦，第一版用輕量物理

| 項目 | 決策 |
|---|---|
| 模擬核心 | **自寫輕量 2D 物理**（圓盤小車＋圓形障礙物、碰撞偵測），先打通 gRPC 全鏈路 |
| Mujoco | **不接第一版**。僅保留核心介面抽象，未來套真實 Mujoco 時 gRPC / proto 層完全不動 |
| Server 語言 | **Python（grpcio）**：與未來 Mujoco/Gymnasium 生態同語言，原型最快 |
| 物理步進 | 伺服器 **50Hz**，自行插值 |
| 手機控制頻率 | 上行 **20~30Hz**，不需要跟上物理頻率 |

### 核心介面抽象（重要架構決策）

```
┌─────────────┐   gRPC proto   ┌──────────────┐   介面    ┌─────────────┐
│   Android   │ ◄────────────► │  gRPC Server │ ────────► │  SimCore    │
│  (Kotlin)   │                │   (Python)   │  Acquisition │ (輕量 2D) │
└─────────────┘                └──────────────┘           └─────────────┘
                                                              ▲
                                             未來可換 Real Mujoco（彩蛋）
```

- `SimCore` 只暴露：`step(control) → (state, collision_events[])` 之類的最小介面
- `CollisionEvent.magnitude` 現在送「碰撞當下相對速度正規化值」，換 Mujoco 後送真實接觸力——**欄位不變**

### demo 故事（簡報可講）
「gRPC 層不變、把核心換成 Mujoco」正是為後續虛實整合（sim-to-real）預留的接縫，這本身就是這支 demo 想傳達的架構故事。

---

## Q3｜控制映射與振動/斷線語意

### 3a｜傾角 → 速度映射 ✔（採納建議）

- 用 `SensorManager` 的 **rotation vector** 換算 roll/pitch **傾角**（角度，非角速度——角速度積分會飄）
- roll/pitch → 線速度 `(vx, vy)`，**線性映射並夾在 max speed**
- **不做旋轉軸（ωz）**：只有平移，控制面最少、demo 最好懂

### 3b｜碰撞去抖 ✔（採納建議）

- 碰撞事件 **debounce 300ms**：重複碰撞 ≥300ms 才再發，避免振動連響

### 3c｜振動強度 ✏（依使用者調整：隨碰撞力度縮放）

- **固定強度 → 改為：強度隨 `CollisionEvent.magnitude` 縮放**
- 手機端依 magnitude 同時縮放 `VibrationEffect` 的 **amplitude 與 duration**（例如 magnitude 0→1 對應 100ms/低強度 → 400ms/全強度，確切映射待實作實驗）
- 因此 `CollisionEvent` 必須帶 magnitude 欄位（見 Q1 proto 草稿）——這是 3c 改動對契約的唯一影響

### 3d｜斷線與自動重連 ✔（採納建議）

四種斷線情境與處理（完整情境表）：

| 情境 | 手機端 | 伺服器端 | 觀感 |
|---|---|---|---|
| ① Tailscale/WiFi 短暫抖動 | 收到 `StatusRuntimeException`（`UNAVAILABLE`/`DEADLINE_EXCEEDED`）→ 關舊 stream → 退避重連 | 下一次寫入失敗（`onError`）才察覺；斷線期間小車 **halt**（失控保護） | 斷 1 秒、小車自己停、自己回來 |
| ② 伺服器重啟（demo 高潮） | 退避加深（1s→2s→4s→cap 10s）直到 server 回來 | 起來後送 `SimState` 快照，手機自動重新同步 | kill server 再重啟，手機自己爬回來 |
| ③ 手機被砍/鎖屏（伺服器視角的鬼） | 可能不自知；回前台自動歸位 | 藉寫失敗/keepalive 數秒內偵測 → **halt 小車**（防止帶最後指令永遠衝下去） | UI 顯示「已斷線，重連中」 |
| ④ Tailscale IP 變動 | 不寫死 IP，連 **MagicDNS 主機名**（`macbook.tail──.ts.net`），重連時重新解析 | — | 無感 |

**實作硬性規定（2 條）**
1. **gRPC channel 綁 Application scope，不綁 Activity**——防止螢幕旋轉就斷 stream（那種「重連」不想秀）
2. 指數退避 1s→2s→4s→cap 10s；UI 顯示連線狀態徽章（連線中／已斷線，重連中）

---

## 技術棧總覽（定案）

| 層 | 技術 |
|---|---|
| 手機 | Android 原生、Kotlin、grpc-kotlin（coroutines Flow） |
| 伺服器 | Macbook、Python、grpcio、輕量 2D 物理核心（介面抽象，可換 Mujoco） |
| 網路 | Tailscale 內網、MagicDNS 主機名（已解決連線，勿寫死 IP） |
| 反饋 | `VibrationEffect` 依 magnitude 縮放 amplitude+duration |

## 尚未定案（實作時再決）

- 確切的振動 amplitude/duration ↔ magnitude 映射曲線 → 已實作於 `MainActivity.vibrateByMagnitude()`（最低 35%/150ms → 全強度/450ms），可調
- proto 欄位細部、`SimState`/`RobotState`/`SimStatus` 內容 → 已定於 `proto/remote_sim.proto`
- 手機 UI 配置 → 已實作（狀態徽章＋數值＋兩顆 unary 按鈕）

---

## 實作狀態（2026/10）

- ✅ proto 契約（`java_multiple_files=true` 確保安卓端訊息為頂層類別）
- ✅ Python server：SimWorld（全域小車＋50Hz ticker）＋ SimCore（輕量 2D 物理、碰撞去抖、失控保護）
- ✅ 端到端測試 12/12 通過（`server/tests/client_demo.py`）：碰撞 magnitude、傾角→速度映射、max_speed 夾制、斷線漸停、metadata 往返、ResetSim 快照
- ✅ Android App：grpc-kotlin 雙向 stream、Application-scoped channel＋自動重連（`RemoteSimApp`）、傾角感應、依力度縮放振動、unary 按鈕（deadline/metadata 演示）
- ✅ Android build 通過（AGP 8.7.3 / Gradle 8.10.2 / JDK 17 / compileSdk 35），APK 5.4MB
- ⚠️ 實作中發現並補上的設計細節：
  - **watch-only 語意**：只有「接管過控制」的 stream 停送才視為斷線；純監聽 stream 保持開啟（允許多一支裝置看 telemetry）
  - **碰撞 magnitude 去抖於 SimCore 內**（避免連響）
- ⏳ 待辦：真機連線實測（本機無 Android SDK 模擬器環境實體機）、Mujoco 換裝（彩蛋）
- ⚠️ 若在別台機器 build：`server/` 已生成 `remote_sim_pb2*.py`（Python 3.14）；`android/local.properties` 指向本機 SDK（gitignore 或各機自設）