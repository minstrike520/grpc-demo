package com.example.remotesim

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager

/**
 * 陀螺儀/旋轉向量感應器 → roll/pitch 傾角（弧度）。
 *
 * 3a 定案：用 rotation vector 換算「傾角」而非角速度（角速度積分會飄）。
 * getOrientation 回傳: orientation[0]=azimuth, [1]=pitch(前後), [2]=roll(左右)
 */
class TiltSensor(
    context: Context,
    private val onTilt: (roll: Float, pitch: Float) -> Unit,
) {
    private val sensorManager =
        context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val rotationVector =
        sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)

    private val rotationMatrix = FloatArray(9)
    private val orientation = FloatArray(3)

    private val listener = object : SensorEventListener {
        override fun onSensorChanged(event: SensorEvent?) {
            if (event?.sensor?.type != Sensor.TYPE_ROTATION_VECTOR) return
            SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)
            SensorManager.getOrientation(rotationMatrix, orientation)
            val roll = orientation[2]
            val pitch = orientation[1]
            onTilt(roll, pitch)
        }

        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
    }

    val isAvailable: Boolean get() = rotationVector != null

    fun start() {
        rotationVector?.let {
            sensorManager.registerListener(
                listener, it,
                SensorManager.SENSOR_DELAY_GAME  // ~20Hz 以上，對齊控制頻率
            )
        }
    }

    fun stop() {
        sensorManager.unregisterListener(listener)
    }
}