package com.autoglm.helper

import android.accessibilityservice.AccessibilityService
import android.util.Log
import org.json.JSONObject

/**
 * 接收服务器下发的指令，调用无障碍服务执行，并回传结果。
 * 坐标已由服务器从 0-999 相对坐标转换为实际像素坐标。
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
                    "long_press" -> handleLongPress(command, requestId)
                    "double_tap" -> handleDoubleTap(command, requestId)
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

        val data = service.takeScreenshotWithSize()
        if (data != null) {
            sendScreenshotResult(requestId, true, image = data.base64, width = data.width, height = data.height)
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
        Log.i(TAG, "执行点击: ($x, $y)")
        val success = service.performTap(x, y)
        sendActionResult(requestId, success, if (!success) "点击失败: ($x, $y)" else "")
    }

    private fun handleLongPress(command: JSONObject, requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val x = command.getInt("x")
        val y = command.getInt("y")
        val duration = command.optInt("duration", 1000)
        Log.i(TAG, "执行长按: ($x, $y) duration=$duration")
        val success = service.performLongPress(x, y, duration)
        sendActionResult(requestId, success, if (!success) "长按失败" else "")
    }

    private fun handleDoubleTap(command: JSONObject, requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val x = command.getInt("x")
        val y = command.getInt("y")
        Log.i(TAG, "执行双击: ($x, $y)")
        val success = service.performDoubleTap(x, y)
        sendActionResult(requestId, success, if (!success) "双击失败" else "")
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
        val duration = command.optInt("duration", 500)
        Log.i(TAG, "执行滑动: ($x1,$y1)->($x2,$y2) duration=$duration")
        val success = service.performSwipe(x1, y1, x2, y2, duration)
        sendActionResult(requestId, success, if (!success) "滑动失败" else "")
    }

    private fun handleInput(command: JSONObject, requestId: String) {
        val service = AutoGLMAccessibilityService.getInstance()
        if (service == null) {
            sendActionResult(requestId, false, "无障碍服务未启动")
            return
        }
        val text = command.getString("text")
        Log.i(TAG, "执行输入: '$text'")
        val success = service.performInput(text)
        sendActionResult(requestId, success, if (!success) "输入失败，未找到可编辑的输入框" else "")
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
        val packageName = command.optString("package_name", "")
        val appName = command.optString("app_name", "")

        try {
            // Strategy 1: launch by package name
            if (packageName.isNotEmpty()) {
                if (tryLaunchPackage(packageName)) {
                    Log.i(TAG, "通过包名启动: $packageName")
                    sendActionResult(requestId, true)
                    return
                }
            }

            // Strategy 2: search launcher apps by label
            if (appName.isNotEmpty()) {
                val pm = context.packageManager
                val mainIntent = android.content.Intent(android.content.Intent.ACTION_MAIN, null)
                mainIntent.addCategory(android.content.Intent.CATEGORY_LAUNCHER)
                val resolveInfos = pm.queryIntentActivities(mainIntent, 0)

                // 2a) exact match
                for (info in resolveInfos) {
                    val label = info.loadLabel(pm).toString()
                    if (label == appName) {
                        if (tryLaunchPackage(info.activityInfo.packageName)) {
                            Log.i(TAG, "精确匹配 '$appName' -> ${info.activityInfo.packageName}")
                            sendActionResult(requestId, true)
                            return
                        }
                    }
                }

                // 2b) fuzzy match
                for (info in resolveInfos) {
                    val label = info.loadLabel(pm).toString()
                    if (label.contains(appName) || appName.contains(label)) {
                        if (tryLaunchPackage(info.activityInfo.packageName)) {
                            Log.i(TAG, "模糊匹配 '$appName' -> $label (${info.activityInfo.packageName})")
                            sendActionResult(requestId, true)
                            return
                        }
                    }
                }

                // 2c) case-insensitive
                val lowerName = appName.lowercase()
                for (info in resolveInfos) {
                    val label = info.loadLabel(pm).toString().lowercase()
                    if (label.contains(lowerName) || lowerName.contains(label)) {
                        if (tryLaunchPackage(info.activityInfo.packageName)) {
                            Log.i(TAG, "忽略大小写匹配 '$appName' -> ${info.activityInfo.packageName}")
                            sendActionResult(requestId, true)
                            return
                        }
                    }
                }

                // Strategy 3: search all installed apps
                val allApps = pm.getInstalledApplications(0)
                for (appInfo in allApps) {
                    val label = pm.getApplicationLabel(appInfo).toString()
                    if (label == appName || label.contains(appName) || appName.contains(label)) {
                        if (tryLaunchPackage(appInfo.packageName)) {
                            Log.i(TAG, "全量搜索匹配 '$appName' -> $label (${appInfo.packageName})")
                            sendActionResult(requestId, true)
                            return
                        }
                    }
                }

                val installed = resolveInfos.map { it.loadLabel(pm).toString() }.sorted()
                Log.w(TAG, "找不到应用 '$appName'，已安装: ${installed.joinToString()}")
                sendActionResult(requestId, false, "找不到应用: $appName")
            } else {
                sendActionResult(requestId, false, "应用未安装: $packageName")
            }
        } catch (e: Exception) {
            Log.e(TAG, "启动应用异常", e)
            sendActionResult(requestId, false, "启动应用失败: ${e.message}")
        }
    }

    private fun tryLaunchPackage(packageName: String): Boolean {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName) ?: return false
        intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return true
    }

    private fun sendScreenshotResult(
        requestId: String,
        success: Boolean,
        image: String = "",
        error: String = "",
        width: Int = 0,
        height: Int = 0
    ) {
        val json = JSONObject().apply {
            put("type", "screenshot_result")
            put("request_id", requestId)
            put("success", success)
            if (image.isNotEmpty()) put("image", image)
            if (error.isNotEmpty()) put("error", error)
            put("width", width)
            put("height", height)
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
