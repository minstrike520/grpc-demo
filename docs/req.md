# gRPC demo with android and macbook

objective: 展現 gRPC 能力與特性的 demo。主角是 gRPC——機器人與模擬只是彩蛋。
使用手機陀螺儀與振動器。手機使用 Android 原生應用 Kotlin。往前後左右傾斜時，
遙控到電腦端模擬伺服器裡面的萬向輪小車。

- 控制：手機以 roll/pitch 傾角映射為小車線速度 (vx, vy)，不做旋轉
- 碰撞：場地中有障礙物，碰到時手機即時振動，強度隨碰撞力度縮放（去抖防連響）
- 通訊：一條 bidirectional stream（控制上行／碰撞與狀態下行）＋ unary（資訊、重置）
- 連線：Tailscale 內網（已解決）；斷線自動重連（指數退避），斷線期間小車停止

- 手機：Android + Kotlin 原生應用（grpc-kotlin）
- 電腦：Macbook，Python server（grpcio）；第一版模擬核心用輕量 2D 物理，
  Mujoco + Gymnasium 為後續彩蛋（透過核心介面解耦，gRPC 層不變即可替換）