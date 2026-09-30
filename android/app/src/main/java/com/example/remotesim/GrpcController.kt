package com.example.remotesim

import android.os.SystemClock
import android.util.Log
import io.grpc.ManagedChannel
import io.grpc.ManagedChannelBuilder
import io.grpc.Metadata
import io.grpc.StatusException
import io.grpc.StatusRuntimeException
import io.grpc.stub.MetadataUtils
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import remotesim.ControlInput
import remotesim.Empty
import remotesim.RemoteSimGrpcKt
import remotesim.ServerEvent
import remotesim.ServerInfo
import remotesim.SimState

/**
 * gRPC 控制器：channel（Application scope）＋ bidirectional stream ＋ 自動重連。
 *
 * 關鍵設計（by-agent/decisions.md）：
 * - channel 在此物件建立並存活整個 app 生命週期（不綁 Activity，旋轉不重連）
 * - ControlStream 上行來自 tilt StateFlow：重連後立即以最新傾角恢復（3d 情境②）
 * - 下行事件放進 SharedFlow，UI 收集
 * - 自動重連：指數退避 1s→2s→4s→cap 10s；連線穩定超過 5s 後重置退避
 */
class GrpcController(host: String, port: Int) {

    companion object {
        private const val TAG = "GrpcController"
        private const val MIN_BACKOFF_MS = 1_000L
        private const val MAX_BACKOFF_MS = 10_000L
        private const val STABLE_RESET_MS = 5_000L
        private const val DEFAULT_DEADLINE_S = 2L
    }

    // channel 層級 metadata：所有 RPC 都帶 client 資訊（GetInfo demo 用）
    private val channel: ManagedChannel = ManagedChannelBuilder
        .forAddress(host, port)
        .usePlaintext()
        .intercept(MetadataUtils.newAttachHeadersInterceptor(defaultMetadata()))
        .build()

    private val stub = RemoteSimGrpcKt.RemoteSimCoroutineStub(channel)

    private val _status = MutableStateFlow<ConnectionStatus>(ConnectionStatus.Disconnected)
    val status: StateFlow<ConnectionStatus> = _status.asStateFlow()

    private val _events = MutableSharedFlow<ServerEvent>(extraBufferCapacity = 128)
    val events: SharedFlow<ServerEvent> = _events.asSharedFlow()

    /** 最新傾角（弧度），StateFlow：stream 建立時立即收到最新值 */
    private val _tilt = MutableStateFlow(ControlInput.getDefaultInstance())

    /** 由 TiltSensor 呼叫（主執行緒即可） */
    fun setTilt(roll: Float, pitch: Float) {
        _tilt.value = ControlInput.newBuilder().setRoll(roll).setPitch(pitch).build()
    }

    /**
     * 重連迴圈：唯一入口，掛在 lifecycleScope。
     * 正常情況下只會在 scope 取消時結束。
     */
    suspend fun run() {
        var backoffMs = MIN_BACKOFF_MS
        var stableSince = 0L
        try {
            while (true) {
                _status.value = ConnectionStatus.Connecting
                try {
                    stableSince = SystemClock.elapsedRealtime()
                    stub.controlStream(_tilt).collect { ev ->
                        _events.emit(ev)
                    }
                    // stream 正常收尾：伺服器關閉 → 視為斷線
                    _status.value = ConnectionStatus.Reconnecting(backoffMs)
                } catch (ce: CancellationException) {
                    throw ce
                } catch (e: StatusRuntimeException) {
                    Log.w(TAG, "stream error: ${e.status}")   // 情境① UNAVAILABLE / DEADLINE_EXCEEDED
                    _status.value = ConnectionStatus.Reconnecting(backoffMs)
                } catch (e: Exception) {
                    Log.w(TAG, "stream error", e)
                    _status.value = ConnectionStatus.Reconnecting(backoffMs)
                }

                // 連線撐超過 STABLE_RESET_MS 才算「穩」，重設退避
                if (SystemClock.elapsedRealtime() - stableSince > STABLE_RESET_MS) {
                    backoffMs = MIN_BACKOFF_MS
                }
                delay(backoffMs)
                backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
            }
        } finally {
            channel.shutdown()
        }
    }

    /** unary：伺服器資訊（metadata 已隨 channel 送出） */
    suspend fun getInfo(): ServerInfo =
        stub.withDeadlineAfter(DEFAULT_DEADLINE_S, TimeUnit.SECONDS)
            .getInfo(Empty.getDefaultInstance())

    /** unary：重置模擬。以 1.5s deadline 示範 DEADLINE_EXCEEDED 錯誤碼 */
    suspend fun resetSim(): SimState =
        stub.withDeadlineAfter(1500, TimeUnit.MILLISECONDS)
            .resetSim(Empty.getDefaultInstance())

    /** 把 StatusException 轉成給 UI 顯示的訊息 */
    fun describeError(e: Exception): String = when (e) {
        is StatusRuntimeException -> "gRPC ${e.status.code}：${e.status.description ?: "（無描述）"}"
        is StatusException -> "gRPC ${e.status.code}：${e.status.description ?: "（無描述）"}"
        else -> "${e.javaClass.simpleName}：${e.message ?: e.toString()}"
    }

    private fun defaultMetadata(): Metadata {
        val md = Metadata()
        md.put(
            Metadata.Key.of("client-version", Metadata.ASCII_STRING_MARSHALLER),
            "android-0.1.0",
        )
        md.put(
            Metadata.Key.of("device-model", Metadata.ASCII_STRING_MARSHALLER),
            android.os.Build.MODEL,
        )
        return md
    }
}