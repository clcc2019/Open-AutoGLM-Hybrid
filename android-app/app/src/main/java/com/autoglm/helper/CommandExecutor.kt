package com.autoglm.helper

import android.accessibilityservice.AccessibilityService
import android.util.Log
import org.json.JSONObject

/**
 * 接收服务器下发的指令，调用无障碍服务执行，并回传结果。
 */
class CommandExecutor(
    private val context: android.content.Context,
    private val wsClient: WebSocketClient,
) {

    companion object {
        private const val TAG = "AutoGLM-Executor"
    }

    fun execute(command: JSONObject) {
        val type = command.optString("type", "")
        val requestId = command.optString("request_id", "")

        Thread {
            try {
                when (type) {
                    "screenshot_request" -> handleScreenshot(requestId)
                    "tap" -> handleTap(command, requestId)
                    "swipe" -> handleSwipe(command, requestId)
                    "input" -> handleInput(command, requestId)
                    "back" -> handleBack(requestId)
                    "home" -> handleHome(requestId)
                    "launch_app" -> handleLaunchApp(command, requestId)
                    "task_started" -> Log.i(TAG, "任务开始: ${command.optString("task")}")
                    "task_completed" -> Log.i(TAG, "任务完成: ${command.optString("summary")}")
                    "task_failed" -> Log.i(TAG, "任务失败: ${command.optString("reason")}")
                    "error" -> Log.e(TAG, "服务器错误: ${command.optString("message")}")
                    else -> Log.w(TAG, "未知指令: $type")
                }
            } catch (e: Exception) {
                Log.e(TAG, "执行指令异常: $type", e)
                sendActionResult(requestId, false, e.message ?: "未知异常")
            }
        }.start()
    }

    private fun handleScreenshot(requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendScreenshotResult(requestId, false, error = "无障碍服务未启动")
            return
        }

        val base64 = service.takeScreenshotBase64()
        if (base64 != null) {
            sendScreenshotResult(requestId, true, image = base64)
        } else {
            sendScreenshotResult(requestId, false, error = "截图失败")
        }
    }

    private fun handleTap(command: JSONObject, requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val x = command.getInt("x")
        val y = command.getInt("y")
        val success = service.performTap(x, y)
        sendActionResult(requestId, success)
    }

    private fun handleSwipe(command: JSONObject, requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val x1 = command.getInt("x1")
        val y1 = command.getInt("y1")
        val x2 = command.getInt("x2")
        val y2 = command.getInt("y2")
        val duration = command.optInt("duration", 300)
        val success = service.performSwipe(x1, y1, x2, y2, duration)
        sendActionResult(requestId, success)
    }

    private fun handleInput(command: JSONObject, requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val text = command.getString("text")
        val success = service.performInput(text)
        sendActionResult(requestId, success)
    }

    private fun handleBack(requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val success = service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
        sendActionResult(requestId, success)
    }

    private fun handleHome(requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val success = service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
        sendActionResult(requestId, success)
    }

    private fun handleLaunchApp(command: JSONObject, requestId: String) {
        val packageName = command.getString("package_name")
        try {
            val intent = context.packageManager.getLaunchIntentForPackage(packageName)
            if (intent != null) {
                intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
                sendActionResult(requestId, true)
            } else {
                sendActionResult(requestId, false, "应用未安装: $packageName")
            }
        } catch (e: Exception) {
            sendActionResult(requestId, false, "启动应用失败: ${e.message}")
        }
    }

    private fun sendScreenshotResult(requestId: String, success: Boolean, image: String = "", error: String = "") {
        val json = JSONObject().apply {
            put("type", "screenshot_result")
            put("request_id", requestId)
            put("success", success)
            if (image.isNotEmpty()) put("image", image)
            if (error.isNotEmpty()) put("error", error)
        }
        wsClient.send(json)
    }

    private fun sendActionResult(requestId: String, success: Boolean, error: String = "") {
        val json = JSONObject().apply {
            put("type", "action_result")
            put("request_id", requestId)
            put("success", success)
            if (error.isNotEmpty()) put("error", error)
        }
        wsClient.send(json)
    }

}
