package com.example.remotesim

import android.app.Activity
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.WindowManager
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import remotesim.ServerEvent

/**
 * 主畫面：傾斜控制 → gRPC stream；碰撞 → 依力度縮放的振動（3c 定案）。
 */
class MainActivity : ComponentActivity() {

    companion object {
        private const val TAG = "MainActivity"
        // 依 magnitude 縮放振動：最低有感 35% 強度/150ms，全速碰撞 100%/450ms
        private const val MIN_INTENSITY = 0.35f
        private const val VIB_DURATION_MS_MIN = 150f
        private const val VIB_DURATION_MS_MAX = 450f
    }

    /** controller/channel 在 Application scope（3d 硬性規定） */
    private val controller: GrpcController
        get() = (application as RemoteSimApp).controller

    private lateinit var tiltSensor: TiltSensor
    private lateinit var vibrator: Vibrator

    // runtime views
    private lateinit var statusText: TextView
    private lateinit var phaseText: TextView
    private lateinit var positionText: TextView
    private lateinit var velocityText: TextView
    private lateinit var collisionText: TextView
    private lateinit var tiltText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        statusText = findViewById(R.id.statusText)
        phaseText = findViewById(R.id.phaseText)
        positionText = findViewById(R.id.positionText)
        velocityText = findViewById(R.id.velocityText)
        collisionText = findViewById(R.id.collisionText)
        tiltText = findViewById(R.id.tiltText)
        vibrator = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator

        if (!vibrator.hasVibrator()) {
            Toast.makeText(this, "此裝置沒有振動器", Toast.LENGTH_LONG).show()
        }

        // 感應器 → 傾角直送 controller（server 端做速度映射與夾制）
        tiltSensor = TiltSensor(this) { roll, pitch ->
            controller.setTilt(roll, pitch)
            tiltText.text = getString(R.string.tilt_format, roll, pitch)
        }
        if (!tiltSensor.isAvailable) {
            Toast.makeText(this, "此裝置無旋轉向量感應器", Toast.LENGTH_LONG).show()
        }

        // 連線狀態徽章
        lifecycleScope.launch {
            controller.status.collect { status ->
                statusText.text = when (status) {
                    ConnectionStatus.Connecting -> getString(R.string.status_connecting)
                    ConnectionStatus.Connected -> getString(R.string.status_connected)
                    is ConnectionStatus.Reconnecting ->
                        getString(R.string.status_reconnecting, status.backoffMs / 1000)
                    ConnectionStatus.Disconnected -> getString(R.string.status_disconnected)
                }
            }
        }

        // 下行事件：碰撞 → 振動；狀態 → 數字；模擬狀態 → phase
        lifecycleScope.launch {
            controller.events.collect { ev -> handleEvent(ev) }
        }

        // 主迴圈（自動重連）在 Application scope 由 RemoteSimApp 啟動，這裡不需要

        findViewById<android.widget.Button>(R.id.resetButton).setOnClickListener {
            lifecycleScope.launch {
                withContext(Dispatchers.IO) {
                    runCatching { controller.resetSim() }   // 1.5s deadline demo
                        .onSuccess {
                            withContext(Dispatchers.Main) {
                                Toast.makeText(
                                    this@MainActivity,
                                    "模擬已重置：障礙物 ${it.obstaclesCount} 顆",
                                    Toast.LENGTH_SHORT,
                                ).show()
                            }
                        }
                        .onFailure { e ->
                            withContext(Dispatchers.Main) {
                                Toast.makeText(
                                    this@MainActivity,
                                    "重置失敗（deadline 錯誤碼演示）：${controller.describeError(e as Exception)}",
                                    Toast.LENGTH_LONG,
                                ).show()
                            }
                        }
                }
            }
        }

        findViewById<android.widget.Button>(R.id.infoButton).setOnClickListener {
            lifecycleScope.launch {
                withContext(Dispatchers.IO) {
                    runCatching { controller.getInfo() }
                        .onSuccess { info ->
                            withContext(Dispatchers.Main) {
                                Toast.makeText(
                                    this@MainActivity,
                                    getString(
                                        R.string.server_info_format,
                                        info.serverName, info.serverVersion,
                                        info.physicsHz, info.maxSpeed,
                                        info.description,
                                    ),
                                    Toast.LENGTH_LONG,
                                ).show()
                            }
                        }
                        .onFailure { e ->
                            withContext(Dispatchers.Main) {
                                Toast.makeText(
                                    this@MainActivity,
                                    "取得資訊失敗：${controller.describeError(e as Exception)}",
                                    Toast.LENGTH_LONG,
                                ).show()
                            }
                        }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        tiltSensor.start()
    }

    override fun onPause() {
        tiltSensor.stop()
        super.onPause()
    }

    override fun onDestroy() {
        super.onDestroy()
        // lifecycleScope 取消 ⇒ run() 的 finally 會 shutdown channel
    }

    private fun handleEvent(ev: ServerEvent) {
        when {
            ev.hasCollision() -> vibrateByMagnitude(ev.collision.magnitude)
            ev.hasState() -> {
                val s = ev.state
                positionText.text = getString(R.string.position_format, s.x, s.y)
                velocityText.text = getString(R.string.velocity_format, s.vx, s.vy)
            }
            ev.hasStatus() -> {
                val st = ev.status
                phaseText.text = getString(
                    if (st.running) R.string.phase_running else R.string.phase_halted,
                    st.phase, st.fps,
                )
            }
        }
    }

    /** 3c 定案：振動強度/時長隨碰撞力度（magnitude 0..1）縮放 */
    private fun vibrateByMagnitude(magnitude: Float) {
        val m = magnitude.coerceIn(0f, 1f)
        val intensity = MIN_INTENSITY + (1f - MIN_INTENSITY) * m
        val amplitude = (intensity * 255).toInt().coerceIn(64, 255)
        val durationMs = (VIB_DURATION_MS_MIN + intensity * (VIB_DURATION_MS_MAX - VIB_DURATION_MS_MIN))
            .toLong()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {  // minSdk 26 恆真，保平安
            vibrator.vibrate(VibrationEffect.createOneShot(durationMs, amplitude))
        } else {
            @Suppress("DEPRECATION")
            vibrator.vibrate(durationMs)
        }
        collisionText.text = getString(R.string.collision_format, magnitude, amplitude, durationMs)
    }
}