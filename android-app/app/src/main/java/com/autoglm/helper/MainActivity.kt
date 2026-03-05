package com.autoglm.helper

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity(), WebSocketClient.ConnectionListener {

    private lateinit var statusText: TextView
    private lateinit var wsStatusText: TextView
    private lateinit var accessibilityDot: View
    private lateinit var serverDot: View
    private lateinit var serverUrlInput: TextInputEditText
    private lateinit var connectButton: MaterialButton
    private lateinit var logText: TextView
    private lateinit var logScrollView: ScrollView

    private lateinit var wsClient: WebSocketClient
    private lateinit var commandExecutor: CommandExecutor

    private val handler = Handler(Looper.getMainLooper())
    private val timeFormat = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    private val logBuffer = StringBuilder()
    private val maxLogLines = 200

    private val statusRunnable = object : Runnable {
        override fun run() {
            updateAccessibilityStatus()
            handler.postDelayed(this, 2000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.statusText)
        wsStatusText = findViewById(R.id.wsStatusText)
        accessibilityDot = findViewById(R.id.accessibilityDot)
        serverDot = findViewById(R.id.serverDot)
        serverUrlInput = findViewById(R.id.serverUrlInput)
        connectButton = findViewById(R.id.connectButton)
        logText = findViewById(R.id.logText)
        logScrollView = findViewById(R.id.logScrollView)

        wsClient = WebSocketClient(this)
        commandExecutor = CommandExecutor(this, wsClient)
        wsClient.setListener(this)
        wsClient.setCommandExecutor(commandExecutor)

        serverUrlInput.setText(wsClient.serverUrl)

        connectButton.setOnClickListener {
            if (wsClient.isConnected()) {
                wsClient.disconnect()
            } else {
                val url = serverUrlInput.text.toString().trim()
                if (url.isNotEmpty()) {
                    wsClient.serverUrl = url
                }
                wsClient.connect()
                updateServerStatus("connecting")
            }
        }

        findViewById<MaterialButton>(R.id.openSettingsButton).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        findViewById<MaterialButton>(R.id.testConnectionButton).setOnClickListener {
            testLocalConnection()
        }

        findViewById<MaterialButton>(R.id.clearLogButton).setOnClickListener {
            logBuffer.clear()
            logText.text = ""
        }

        appendLog("AutoGLM 已启动")
        updateAccessibilityStatus()
    }

    override fun onResume() {
        super.onResume()
        handler.post(statusRunnable)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(statusRunnable)
    }

    override fun onDestroy() {
        super.onDestroy()
        wsClient.disconnect()
    }

    private fun updateAccessibilityStatus() {
        val running = AutoGLMAccessibilityService.getInstance() != null
        statusText.text = if (running) getString(R.string.service_running) else getString(R.string.service_stopped)
        statusText.setTextColor(ContextCompat.getColor(this,
            if (running) R.color.status_running else R.color.status_stopped))
        accessibilityDot.backgroundTintList = ContextCompat.getColorStateList(this,
            if (running) R.color.status_running else R.color.status_stopped)
    }

    private fun updateServerStatus(state: String) {
        val colorRes: Int
        val text: String
        when (state) {
            "connected" -> {
                colorRes = R.color.status_running
                text = getString(R.string.server_connected)
                connectButton.text = "断开连接"
            }
            "connecting" -> {
                colorRes = R.color.status_connecting
                text = getString(R.string.server_connecting)
                connectButton.text = "连接中…"
            }
            else -> {
                colorRes = R.color.status_stopped
                text = getString(R.string.server_disconnected)
                connectButton.text = "连接服务器"
            }
        }
        wsStatusText.text = text
        wsStatusText.setTextColor(ContextCompat.getColor(this, colorRes))
        serverDot.backgroundTintList = ContextCompat.getColorStateList(this, colorRes)
    }

    // --- WebSocketClient.ConnectionListener ---

    override fun onConnected() {
        runOnUiThread { updateServerStatus("connected") }
    }

    override fun onDisconnected(reason: String) {
        runOnUiThread { updateServerStatus("disconnected") }
    }

    override fun onError(error: String) {
        runOnUiThread {
            Toast.makeText(this, error, Toast.LENGTH_SHORT).show()
        }
    }

    override fun onLog(message: String) {
        runOnUiThread { appendLog(message) }
    }

    // --- Helpers ---

    private fun appendLog(msg: String) {
        val ts = timeFormat.format(Date())
        logBuffer.append("[$ts] $msg\n")

        val lines = logBuffer.lines()
        if (lines.size > maxLogLines) {
            logBuffer.clear()
            logBuffer.append(lines.takeLast(maxLogLines).joinToString("\n"))
        }

        logText.text = logBuffer.toString()
        logScrollView.post { logScrollView.fullScroll(ScrollView.FOCUS_DOWN) }
    }

    private fun testLocalConnection() {
        Thread {
            try {
                val url = URL("http://localhost:${AutoGLMAccessibilityService.PORT}/status")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "GET"
                conn.connectTimeout = 3000
                conn.readTimeout = 3000
                val code = conn.responseCode
                runOnUiThread {
                    if (code == 200) {
                        Toast.makeText(this, getString(R.string.connection_success), Toast.LENGTH_SHORT).show()
                        appendLog("本地服务测试: 成功")
                    } else {
                        Toast.makeText(this, getString(R.string.connection_failed, "HTTP $code"), Toast.LENGTH_SHORT).show()
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, getString(R.string.connection_failed, e.message), Toast.LENGTH_LONG).show()
                    appendLog("本地服务测试: 失败 - ${e.message}")
                }
            }
        }.start()
    }
}
