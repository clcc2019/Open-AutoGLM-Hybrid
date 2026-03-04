package com.autoglm.helper

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.util.Log
import android.view.Display
import android.view.KeyEvent
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

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "Service connected")
        startHttpServer()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {
        Log.w(TAG, "Service interrupted")
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        stopHttpServer()
        Log.i(TAG, "Service destroyed")
    }

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

    fun performDoubleTap(x: Int, y: Int): Boolean {
        val first = performTap(x, y)
        Thread.sleep(100)
        val second = performTap(x, y)
        return first && second
    }

    fun performLongPress(x: Int, y: Int, duration: Int = 1000): Boolean {
        return try {
            val path = Path()
            path.moveTo(x.toFloat(), y.toFloat())

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

            latch.await(5, TimeUnit.SECONDS)
            Log.d(TAG, "Long press at ($x, $y) duration=$duration: $success")
            success
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform long press", e)
            false
        }
    }

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
            Log.d(TAG, "Swipe from ($x1, $y1) to ($x2, $y2) duration=$duration: $success")
            success
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform swipe", e)
            false
        }
    }

    fun performInput(text: String): Boolean {
        return try {
            val rootNode = rootInActiveWindow
            if (rootNode == null) {
                Log.w(TAG, "rootInActiveWindow is null, trying clipboard paste")
                return inputViaClipboard(text)
            }

            // Strategy 1: Find focused editable node and use ACTION_SET_TEXT
            val focusedNode = findFocusedEditText(rootNode)
            if (focusedNode != null) {
                val args = Bundle()
                args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                val success = focusedNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
                focusedNode.recycle()
                if (success) {
                    Log.i(TAG, "Input via ACTION_SET_TEXT on focused node: success")
                    return true
                }
                Log.w(TAG, "ACTION_SET_TEXT on focused node failed, trying clipboard")
            }

            // Strategy 2: Find any editable node
            val editableNode = findAnyEditText(rootNode)
            if (editableNode != null) {
                editableNode.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                Thread.sleep(100)
                val args = Bundle()
                args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                val success = editableNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
                editableNode.recycle()
                if (success) {
                    Log.i(TAG, "Input via ACTION_SET_TEXT on editable node: success")
                    return true
                }
                Log.w(TAG, "ACTION_SET_TEXT on editable node failed, trying clipboard")
            }

            // Strategy 3: Clipboard paste — works on most apps including WeChat
            Log.i(TAG, "Falling back to clipboard paste for: '$text'")
            inputViaClipboard(text)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to perform input", e)
            false
        }
    }

    /**
     * 通过剪贴板粘贴文本。
     * 这是最可靠的输入方式，适用于微信等不支持 ACTION_SET_TEXT 的应用。
     */
    private fun inputViaClipboard(text: String): Boolean {
        return try {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clip = ClipData.newPlainText("autoglm_input", text)
            clipboard.setPrimaryClip(clip)
            Thread.sleep(100)

            val rootNode = rootInActiveWindow ?: return false

            // Try to find and paste into the focused/editable node
            val targetNode = findFocusedEditText(rootNode) ?: findAnyEditText(rootNode)
            if (targetNode != null) {
                targetNode.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                Thread.sleep(50)
                val success = targetNode.performAction(AccessibilityNodeInfo.ACTION_PASTE)
                targetNode.recycle()
                if (success) {
                    Log.i(TAG, "Input via clipboard paste: success")
                    return true
                }
            }

            // Last resort: use the input connection focus node
            val focusNode = rootNode.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            if (focusNode != null) {
                val success = focusNode.performAction(AccessibilityNodeInfo.ACTION_PASTE)
                focusNode.recycle()
                if (success) {
                    Log.i(TAG, "Input via clipboard paste on input-focused node: success")
                    return true
                }
            }

            Log.w(TAG, "All input methods failed for: '$text'")
            false
        } catch (e: Exception) {
            Log.e(TAG, "Clipboard paste failed", e)
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
            if (result != null) return result
            child.recycle()
        }
        return null
    }

    private fun findAnyEditText(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable) {
            return node
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val result = findAnyEditText(child)
            if (result != null) return result
            child.recycle()
        }
        return null
    }

    data class ScreenshotData(
        val base64: String,
        val width: Int,
        val height: Int
    )

    /**
     * 截取屏幕并返回 Base64 编码的 PNG 图片 + 屏幕尺寸。
     * 使用 PNG 格式与 Open-AutoGLM 保持一致。
     */
    fun takeScreenshotWithSize(): ScreenshotData? {
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
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
                    val bmp = bitmap!!
                    val width = bmp.width
                    val height = bmp.height
                    val outputStream = ByteArrayOutputStream()
                    bmp.compress(Bitmap.CompressFormat.PNG, 100, outputStream)
                    val bytes = outputStream.toByteArray()
                    bmp.recycle()
                    ScreenshotData(
                        base64 = Base64.encodeToString(bytes, Base64.NO_WRAP),
                        width = width,
                        height = height
                    )
                } else {
                    null
                }
            } else {
                Log.w(TAG, "takeScreenshot not supported on Android < 11")
                null
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to take screenshot", e)
            null
        }
    }

    @Deprecated("Use takeScreenshotWithSize() instead")
    fun takeScreenshotBase64(): String? {
        return takeScreenshotWithSize()?.base64
    }
}
