package com.example.remotesim

import android.app.Application
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Application scope：持有 gRPC channel 與重連迴圈（3d 硬性規定）。
 * channel 在這裡建立並存活整個 app 生命週期——螢幕旋轉/Activity 重建不會重連。
 */
class RemoteSimApp : Application() {

    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    val controller: GrpcController by lazy {
        GrpcController(SERVER_HOST, SERVER_PORT).also {
            appScope.launch { it.run() }   // 自動重連迴圈（1s→2s→4s→cap 10s）
        }
    }

    companion object {
        // Tailscale：改用你 Macbook 的 MagicDNS 主機名（勿寫死 IP，3d 情境④）
        // TODO: 換成你的 Macbook 主機名
        private const val SERVER_HOST = "macbook.tail544a05.ts.net"
        private const val SERVER_PORT = 50051
    }
}