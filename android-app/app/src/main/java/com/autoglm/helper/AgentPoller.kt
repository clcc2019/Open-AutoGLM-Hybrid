package com.autoglm.helper

import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

/**
 * Polls the remote Agent server for commands and executes them locally.
 *
 * Flow:
 *   1. POST /api/phone/poll  (send screenshot + status)
 *   2. Server returns commands: [{action, params}, ...]
 *   3. Execute each command via AccessibilityService
 *   4. Report results back on next poll
 */
class AgentPoller(
    private val service: AutoGLMAccessibilityService,
    private var agentUrl: String,
    private var deviceId: String = "phone-1",
    private var apiKey: String = "",
) {
    companion object {
        private const val TAG = "AutoGLM-Poller"
        private const val DEFAULT_INTERVAL_MS = 3000L
        private const val IDLE_INTERVAL_MS = 5000L
        private const val CONNECT_TIMEOUT = 10_000
        private const val READ_TIMEOUT = 30_000
    }

    private var handlerThread: HandlerThread? = null
    private var handler: Handler? = null
    private var running = false
    private var intervalMs = DEFAULT_INTERVAL_MS
    private var lastResults: JSONArray = JSONArray()
    var onStatusChange: ((String) -> Unit)? = null

    val isRunning: Boolean get() = running

    fun start() {
        if (running) return
        running = true

        handlerThread = HandlerThread("AgentPoller").also { it.start() }
        handler = Handler(handlerThread!!.looper)
        handler?.post(pollRunnable)

        Log.i(TAG, "Poller started: $agentUrl")
        onStatusChange?.invoke("running")
    }

    fun stop() {
        running = false
        handler?.removeCallbacksAndMessages(null)
        handlerThread?.quitSafely()
        handlerThread = null
        handler = null

        Log.i(TAG, "Poller stopped")
        onStatusChange?.invoke("stopped")
    }

    fun updateConfig(url: String, device: String, key: String = "") {
        agentUrl = url.trimEnd('/')
        deviceId = device
        apiKey = key
    }

    private val pollRunnable = object : Runnable {
        override fun run() {
            if (!running) return
            try {
                doPoll()
            } catch (e: Exception) {
                Log.e(TAG, "Poll error: ${e.message}")
                onStatusChange?.invoke("error: ${e.message}")
            }
            handler?.postDelayed(this, intervalMs)
        }
    }

    private fun doPoll() {
        val screenshot = service.takeScreenshotBase64()
        val appInfo = service.getCurrentApp()

        val payload = JSONObject().apply {
            put("device_id", deviceId)
            put("screenshot", screenshot ?: "")
            put("current_app", appInfo?.first ?: "")
            put("current_package", appInfo?.second ?: "")
            put("accessibility_enabled", service.isAccessibilityEnabled())
            put("last_results", lastResults)
        }

        val url = URL("$agentUrl/api/phone/poll")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json")
        if (apiKey.isNotEmpty()) {
            conn.setRequestProperty("X-API-Key", apiKey)
        }
        conn.connectTimeout = CONNECT_TIMEOUT
        conn.readTimeout = READ_TIMEOUT
        conn.doOutput = true

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        val responseCode = conn.responseCode
        if (responseCode != 200) {
            Log.w(TAG, "Poll response: HTTP $responseCode")
            onStatusChange?.invoke("server error: $responseCode")
            intervalMs = IDLE_INTERVAL_MS
            return
        }

        val body = conn.inputStream.bufferedReader().readText()
        conn.disconnect()

        val resp = JSONObject(body)
        val commands = resp.optJSONArray("commands") ?: JSONArray()
        intervalMs = resp.optLong("next_poll_ms", DEFAULT_INTERVAL_MS)

        if (commands.length() == 0) {
            onStatusChange?.invoke("waiting")
            lastResults = JSONArray()
            return
        }

        Log.i(TAG, "Received ${commands.length()} commands")
        onStatusChange?.invoke("executing ${commands.length()} commands")

        val results = JSONArray()
        for (i in 0 until commands.length()) {
            val cmd = commands.getJSONObject(i)
            val result = executeCommand(cmd)
            results.put(result)
        }
        lastResults = results
    }

    private fun executeCommand(cmd: JSONObject): JSONObject {
        val action = cmd.optString("action", "")
        val result = JSONObject()
        result.put("action", action)

        try {
            val success = when (action) {
                "tap" -> {
                    val x = cmd.getInt("x")
                    val y = cmd.getInt("y")
                    service.performTap(x, y)
                }
                "swipe" -> {
                    val x1 = cmd.getInt("x1")
                    val y1 = cmd.getInt("y1")
                    val x2 = cmd.getInt("x2")
                    val y2 = cmd.getInt("y2")
                    val duration = cmd.optInt("duration", 300)
                    service.performSwipe(x1, y1, x2, y2, duration)
                }
                "input" -> {
                    val text = cmd.getString("text")
                    service.performInput(text)
                }
                "back" -> service.performBack()
                "home" -> service.performHome()
                "launch_app" -> {
                    val pkg = cmd.optString("package_name", "")
                    val name = cmd.optString("app_name", "")
                    if (pkg.isNotEmpty()) service.launchAppByPackage(pkg)
                    else service.launchAppByName(name)
                }
                "wait" -> {
                    val ms = cmd.optLong("ms", 1000)
                    Thread.sleep(ms)
                    true
                }
                "noop" -> true
                else -> {
                    Log.w(TAG, "Unknown action: $action")
                    false
                }
            }
            result.put("success", success)
        } catch (e: Exception) {
            Log.e(TAG, "Command failed: $action", e)
            result.put("success", false)
            result.put("error", e.message)
        }
        return result
    }
}
