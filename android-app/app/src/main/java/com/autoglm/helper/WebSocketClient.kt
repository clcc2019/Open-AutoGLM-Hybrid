package com.autoglm.helper

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import android.util.Log
import android.view.WindowManager
import okhttp3.*
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

class WebSocketClient(private val context: Context) {

    companion object {
        private const val TAG = "AutoGLM-WS"
        private const val PREFS_NAME = "autoglm_prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_DEVICE_ID = "device_id"
        private const val RECONNECT_DELAY_MS = 5000L
        private const val HEARTBEAT_INTERVAL_MS = 25000L
        private const val NORMAL_CLOSURE = 1000
    }

    interface ConnectionListener {
        fun onConnected()
        fun onDisconnected(reason: String)
        fun onError(error: String)
        fun onLog(message: String)
    }

    private val prefs: SharedPreferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val handler = Handler(Looper.getMainLooper())
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    @Volatile private var activeWebSocket: WebSocket? = null
    @Volatile private var isConnected = false
    @Volatile private var shouldReconnect = true
    private val connecting = AtomicBoolean(false)
    private val generation = AtomicInteger(0)
    private var listener: ConnectionListener? = null
    private var commandExecutor: CommandExecutor? = null

    private val reconnectRunnable = Runnable { connect() }

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value).apply()

    var deviceId: String
        get() {
            var id = prefs.getString(KEY_DEVICE_ID, null)
            if (id == null) {
                id = "android_${Build.MODEL.replace(" ", "_")}_${System.currentTimeMillis() % 10000}"
                prefs.edit().putString(KEY_DEVICE_ID, id).apply()
            }
            return id
        }
        set(value) = prefs.edit().putString(KEY_DEVICE_ID, value).apply()

    fun setListener(l: ConnectionListener?) { listener = l }
    fun setCommandExecutor(executor: CommandExecutor?) { commandExecutor = executor }
    fun isConnected(): Boolean = isConnected

    fun connect() {
        val url = serverUrl
        if (url.isBlank()) {
            handler.post { listener?.onError("服务器地址未配置") }
            return
        }

        if (!connecting.compareAndSet(false, true)) {
            Log.d(TAG, "连接进行中，忽略重复请求")
            return
        }

        shouldReconnect = true
        handler.removeCallbacks(reconnectRunnable)

        val gen = generation.incrementAndGet()
        val wsUrl = buildWsUrl(url)
        log("正在连接: $wsUrl")

        val request = Request.Builder().url(wsUrl).build()
        client.newWebSocket(request, object : WebSocketListener() {

            override fun onOpen(ws: WebSocket, response: Response) {
                if (gen != generation.get()) {
                    ws.close(NORMAL_CLOSURE, "stale")
                    return
                }
                activeWebSocket = ws
                isConnected = true
                connecting.set(false)
                handler.post { listener?.onConnected() }
                log("已连接到服务器")
                sendDeviceInfo()
                startHeartbeat()
            }

            override fun onMessage(ws: WebSocket, text: String) {
                if (gen != generation.get()) return
                try {
                    val json = JSONObject(text)
                    val type = json.optString("type", "")

                    when (type) {
                        "connected" -> log("服务器确认连接")
                        "heartbeat_ack" -> { /* ok */ }
                        else -> commandExecutor?.execute(json)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "处理消息异常", e)
                }
            }

            override fun onClosing(ws: WebSocket, code: Int, reason: String) {
                ws.close(NORMAL_CLOSURE, null)
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                onWsGone(ws, gen, "连接关闭($code): $reason")
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                onWsGone(ws, gen, "连接失败: ${t.message}")
            }
        })
    }

    fun disconnect() {
        shouldReconnect = false
        generation.incrementAndGet()
        connecting.set(false)
        stopHeartbeat()
        handler.removeCallbacks(reconnectRunnable)
        activeWebSocket?.close(NORMAL_CLOSURE, "用户断开")
        activeWebSocket = null
        isConnected = false
    }

    fun send(json: JSONObject) {
        val ws = activeWebSocket
        if (ws != null && isConnected) {
            ws.send(json.toString())
        } else {
            Log.w(TAG, "WebSocket 未连接，无法发送")
        }
    }

    private fun onWsGone(ws: WebSocket, gen: Int, reason: String) {
        if (gen != generation.get()) {
            Log.d(TAG, "忽略过期连接的断开回调 (gen=$gen, current=${generation.get()})")
            return
        }

        if (activeWebSocket !== ws) {
            Log.d(TAG, "忽略非活跃连接的断开回调")
            return
        }

        activeWebSocket = null
        isConnected = false
        connecting.set(false)
        stopHeartbeat()
        handler.post { listener?.onDisconnected(reason) }
        log(reason)

        scheduleReconnect()
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) return
        handler.removeCallbacks(reconnectRunnable)
        log("${RECONNECT_DELAY_MS / 1000}秒后重连...")
        handler.postDelayed(reconnectRunnable, RECONNECT_DELAY_MS)
    }

    private fun buildWsUrl(baseUrl: String): String {
        var url = baseUrl.trimEnd('/')
        if (!url.startsWith("ws://") && !url.startsWith("wss://")) {
            url = if (url.startsWith("https://")) {
                url.replace("https://", "wss://")
            } else {
                url.replace("http://", "ws://")
            }
            if (!url.startsWith("ws")) url = "ws://$url"
        }
        return "$url/device/$deviceId"
    }

    private fun sendDeviceInfo() {
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getMetrics(metrics)

        val info = JSONObject().apply {
            put("type", "device_info")
            put("request_id", "")
            put("device_id", deviceId)
            put("model", Build.MODEL)
            put("android_version", Build.VERSION.RELEASE)
            put("screen_width", metrics.widthPixels)
            put("screen_height", metrics.heightPixels)
        }
        send(info)
    }

    // --- Heartbeat ---

    private val heartbeatRunnable = object : Runnable {
        override fun run() {
            if (isConnected) {
                val hb = JSONObject().apply {
                    put("type", "heartbeat")
                    put("request_id", "")
                    put("device_id", deviceId)
                    put("timestamp", System.currentTimeMillis() / 1000)
                }
                send(hb)
                handler.postDelayed(this, HEARTBEAT_INTERVAL_MS)
            }
        }
    }

    private fun startHeartbeat() {
        handler.removeCallbacks(heartbeatRunnable)
        handler.postDelayed(heartbeatRunnable, HEARTBEAT_INTERVAL_MS)
    }

    private fun stopHeartbeat() {
        handler.removeCallbacks(heartbeatRunnable)
    }

    private fun log(msg: String) {
        Log.i(TAG, msg)
        handler.post { listener?.onLog(msg) }
    }
}
