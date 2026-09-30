package com.example.remotesim

/** 連線狀態（3d 定案：UI 狀態徽章用） */
sealed interface ConnectionStatus {
    /** 正在（重新）建立 stream */
    data object Connecting : ConnectionStatus

    /** stream 已建立 */
    data object Connected : ConnectionStatus

    /** 斷線，重連中（指數退避） */
    data class Reconnecting(val backoffMs: Long) : ConnectionStatus

    /** 從未連線成功 */
    data object Disconnected : ConnectionStatus
}