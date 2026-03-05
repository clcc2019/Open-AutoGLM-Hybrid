package com.autoglm.helper

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.util.Base64
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.io.ByteArrayOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class AutoGLMAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "AutoGLM-Service"
        const val PORT = 8080
        
        @Volatile
        private var instance: AutoGLMAccessibilityService? = null
        
        fun getInstance(): AutoGLMAccessibilityService? = instance
    }

    private var httpServer: HttpServer? = null
    private var agentPoller: AgentPoller? = null
    private var pollerStatus: String = "stopped"

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        
        Log.i(TAG, "Service connected")
        
        startHttpServer()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
    }

    override fun onInterrupt() {
        Log.w(TAG, "Service interrupted")
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        
        stopPoller()
        stopHttpServer()
        
        Log.i(TAG, "Service destroyed")
    }

    fun startPoller(agentUrl: String, deviceId: String = "phone-1", apiKey: String = "") {
        stopPoller()
        agentPoller = AgentPoller(this, agentUrl, deviceId, apiKey).apply {
            onStatusChange = { status ->
                pollerStatus = status
                Log.i(TAG, "Poller status: $status")
            }
            start()
        }
    }

    fun stopPoller() {
        agentPoller?.stop()
        agentPoller = null
        pollerStatus = "stopped"
    }

    fun isPollerRunning(): Boolean = agentPoller?.isRunning == true

    fun getPollerStatus(): String = pollerStatus

    private fun startHttpServer() {
        try {
            httpServer = HttpServer(this, PORT)
            httpServer?.start()
            Log.i(TAG, "HTTP server started on port $PORT")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start HTTP server", e)
        }
    }

    private fun stopHttpServer() {
        httpServer?.stop()
        httpServer = null
        Log.i(TAG, "HTTP server stopped")
    }

    fun isAccessibilityEnabled(): Boolean {
        return instance != null
    }

    /**
     * 执行点击操作
     */
    fun performTap(x: Int, y: Int): Boolean {
        return try {
            val path = Path()
            path.moveTo(x.toFloat(), y.toFloat())
            
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, 100))
                .build()
            
            val latch = CountDownLatch(1)
            var success = false
            
            dispatchGesture(gesture, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    success = true
                    latch.countDown()
                }
                
                override fun onCancelled(gestureDescription: GestureDescription?) {
                    success = false
                    latch.countDown()
                }
            }, null)
            
            latch.await(5, TimeUnit.SECONDS)
            Log.d(TAG, "Tap at ($x, $y): $success")
            success
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform tap", e)
            false
        }
    }

    /**
     * 执行滑动操作
     */
    fun performSwipe(x1: Int, y1: Int, x2: Int, y2: Int, duration: Int): Boolean {
        return try {
            val path = Path()
            path.moveTo(x1.toFloat(), y1.toFloat())
            path.lineTo(x2.toFloat(), y2.toFloat())
            
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, duration.toLong()))
                .build()
            
            val latch = CountDownLatch(1)
            var success = false
            
            dispatchGesture(gesture, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    success = true
                    latch.countDown()
                }
                
                override fun onCancelled(gestureDescription: GestureDescription?) {
                    success = false
                    latch.countDown()
                }
            }, null)
            
            latch.await(10, TimeUnit.SECONDS)
            Log.d(TAG, "Swipe from ($x1, $y1) to ($x2, $y2): $success")
            success
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform swipe", e)
            false
        }
    }

    /**
     * 执行输入操作
     */
    fun performInput(text: String): Boolean {
        return try {
            val rootNode = rootInActiveWindow ?: return false
            val focusedNode = findFocusedEditText(rootNode)

            if (focusedNode != null) {
                val arguments = android.os.Bundle()
                arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                val success = focusedNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
                focusedNode.recycle()
                Log.d(TAG, "Input text: $success")
                success
            } else {
                Log.w(TAG, "No focused EditText found")
                false
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform input", e)
            false
        }
    }

    /**
     * 执行返回操作
     */
    fun performBack(): Boolean {
        return try {
            performGlobalAction(GLOBAL_ACTION_BACK)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform back", e)
            false
        }
    }

    /**
     * 执行 Home 操作
     */
    fun performHome(): Boolean {
        return try {
            performGlobalAction(GLOBAL_ACTION_HOME)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform home", e)
            false
        }
    }

    /**
     * 获取当前应用信息
     * Returns: Pair<appName, packageName>
     */
    fun getCurrentApp(): Pair<String, String>? {
        return try {
            val rootNode = rootInActiveWindow ?: return null
            val packageName = rootNode.packageName?.toString() ?: return null

            // 获取应用名称
            val appInfo = packageManager.getApplicationInfo(packageName, 0)
            val appName = packageManager.getApplicationLabel(appInfo).toString()

            rootNode.recycle()
            Pair(appName, packageName)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to get current app", e)
            null
        }
    }

    /**
     * 通过包名启动应用
     * 先尝试 getLaunchIntentForPackage；若为 null（如闲鱼等部分应用），则查询 MAIN/LAUNCHER 并显式启动
     */
    fun launchAppByPackage(packageName: String): Boolean {
        return try {
            var intent = packageManager.getLaunchIntentForPackage(packageName)
            if (intent == null) {
                // 部分应用不通过 getLaunchIntentForPackage 暴露，用 MAIN+LAUNCHER 查询后显式启动
                val mainIntent = Intent(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_LAUNCHER)
                    setPackage(packageName)
                }
                @Suppress("DEPRECATION")
                val list = packageManager.queryIntentActivities(mainIntent, PackageManager.MATCH_DEFAULT_ONLY)
                if (list.isNotEmpty()) {
                    val ai = list[0].activityInfo
                    intent = Intent(Intent.ACTION_MAIN).apply {
                        setClassName(ai.packageName, ai.name)
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    Log.d(TAG, "Launched app via launcher activity: $packageName / ${ai.name}")
                }
            }
            if (intent != null) {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                Log.d(TAG, "Launched app: $packageName")
                true
            } else {
                Log.w(TAG, "Cannot launch app, no launcher intent: $packageName")
                false
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to launch app: $packageName", e)
            false
        }
    }

    /**
     * 通过应用名称启动应用 (需要已知包名映射)
     */
    fun launchAppByName(appName: String): Boolean {
        // 常用应用名称到包名的映射
        val appPackages = mapOf(
            "淘宝" to "com.taobao.taobao",
            "咸鱼" to "com.taobao.idlefish",
            "闲鱼" to "com.taobao.idlefish",
            "京东" to "com.jingdong.app.mall",
            "微信" to "com.tencent.mm",
            "支付宝" to "com.eg.android.AlipayGphone",
            "抖音" to "com.ss.android.ugc.aweme",
            "快手" to "com.smile.gifmaker",
            "微博" to "com.sina.weibo",
            "小红书" to "com.xingin.xhs",
            "美团" to "com.sankuai.meituan",
            "饿了么" to "me.ele",
            "拼多多" to "com.xunmeng.pinduoduo",
            "高德地图" to "com.autonavi.minimap",
            "百度地图" to "com.baidu.BaiduMap",
            "QQ" to "com.tencent.mobileqq",
            "qq" to "com.tencent.mobileqq",
            "QQ音乐" to "com.tencent.qqmusic",
            "网易云音乐" to "com.netease.cloudmusic",
            "哔哩哔哩" to "tv.danmaku.bili",
            "B站" to "tv.danmaku.bili",
            "bilibili" to "tv.danmaku.bili"
        )

        val packageName = appPackages[appName]
        return if (packageName != null) {
            launchAppByPackage(packageName)
        } else {
            Log.w(TAG, "Unknown app name: $appName")
            false
        }
    }

    private fun findFocusedEditText(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isFocused && node.isEditable) {
            return node
        }
        
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val result = findFocusedEditText(child)
            if (result != null) {
                return result
            }
            child.recycle()
        }
        
        return null
    }

    /**
     * 截取屏幕并返回 Base64 编码
     */
    fun takeScreenshotBase64(): String? {
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                // Android 11+ 使用 takeScreenshot API
                val latch = CountDownLatch(1)
                var bitmap: Bitmap? = null
                
                takeScreenshot(
                    Display.DEFAULT_DISPLAY,
                    mainExecutor,
                    object : TakeScreenshotCallback {
                        override fun onSuccess(screenshotResult: ScreenshotResult) {
                            bitmap = Bitmap.wrapHardwareBuffer(
                                screenshotResult.hardwareBuffer,
                                screenshotResult.colorSpace
                            )
                            latch.countDown()
                        }
                        
                        override fun onFailure(errorCode: Int) {
                            Log.e(TAG, "Screenshot failed with error code: $errorCode")
                            latch.countDown()
                        }
                    }
                )
                
                latch.await(5, TimeUnit.SECONDS)
                
                if (bitmap != null) {
                    val outputStream = ByteArrayOutputStream()
                    bitmap!!.compress(Bitmap.CompressFormat.JPEG, 80, outputStream)
                    val bytes = outputStream.toByteArray()
                    bitmap!!.recycle()
                    Base64.encodeToString(bytes, Base64.NO_WRAP)
                } else {
                    null
                }
            } else {
                // Android 7-10 不支持 takeScreenshot，返回 null
                // 调用方应降级到 ADB screencap
                Log.w(TAG, "takeScreenshot not supported on Android < 11")
                null
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to take screenshot", e)
            null
        }
    }
}
