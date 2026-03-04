package com.autoglm.helper

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity(), WebSocketClient.ConnectionListener {

    private lateinit var statusText: TextView
    private lateinit var wsStatusText: TextView
    private lateinit var serverUrlInput: EditText
    private lateinit var connectButton: Button
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
        serverUrlInput = findViewById(R.id.serverUrlInput)
        connectButton = findViewById(R.id.connectButton)
        logText = findViewById(R.id.logText)
        logScrollView = findViewById(R.id.logScrollView)

        wsClient = WebSocketClient(this)
        commandExecutor = CommandExecutor(this, wsClient)
        wsClient.setListener(this)
        wsClient.setCommandExecutor(commandExecutor)

        serverUrlInput.setText(wsClient.serverUrl)

        findViewById<Button>(R.id.saveUrlButton).setOnClickListener {
            val url = serverUrlInput.text.toString().trim()
            wsClient.serverUrl = url
            Toast.makeText(this, "已保存", Toast.LENGTH_SHORT).show()
        }

        findViewById<Button>(R.id.openSettingsButton).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        connectButton.setOnClickListener {
            if (wsClient.isConnected()) {
                wsClient.disconnect()
                connectButton.text = "连接服务器"
                wsStatusText.text = "远程服务器: 已断开"
            } else {
                val url = serverUrlInput.text.toString().trim()
                if (url.isNotEmpty()) {
                    wsClient.serverUrl = url
                }
                wsClient.connect()
                connectButton.text = "断开连接"
            }
        }

        findViewById<Button>(R.id.testConnectionButton).setOnClickListener {
            testLocalConnection()
        }

        appendLog("应用启动")
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
        val service = AutoGLMAccessibilityService.getInstance()
        statusText.text = if (service != null) {
            getString(R.string.service_running)
        } else {
            getString(R.string.service_stopped)
        }
    }

    // --- WebSocketClient.ConnectionListener ---

    override fun onConnected() {
        wsStatusText.text = "远程服务器: 已连接"
        connectButton.text = "断开连接"
    }

    override fun onDisconnected(reason: String) {
        wsStatusText.text = "远程服务器: 已断开"
        connectButton.text = "连接服务器"
    }

    override fun onError(error: String) {
        Toast.makeText(this, error, Toast.LENGTH_SHORT).show()
    }

    override fun onLog(message: String) {
        appendLog(message)
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
                        appendLog("本地无障碍服务测试: 成功")
                    } else {
                        Toast.makeText(this, getString(R.string.connection_failed, "HTTP $code"), Toast.LENGTH_SHORT).show()
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, getString(R.string.connection_failed, e.message), Toast.LENGTH_LONG).show()
                    appendLog("本地无障碍服务测试: 失败 - ${e.message}")
                }
            }
        }.start()
    }
}
