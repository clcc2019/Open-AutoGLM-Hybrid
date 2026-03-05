package com.autoglm.helper

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : Activity() {

    private lateinit var statusText: TextView
    private lateinit var serverStatusText: TextView
    private lateinit var agentStatusText: TextView
    private lateinit var agentUrlInput: EditText
    private lateinit var agentApiKeyInput: EditText
    private lateinit var openSettingsButton: Button
    private lateinit var testConnectionButton: Button
    private lateinit var startAgentButton: Button
    private lateinit var stopAgentButton: Button

    private val handler = Handler(Looper.getMainLooper())
    private val updateRunnable = object : Runnable {
        override fun run() {
            updateStatus()
            handler.postDelayed(this, 1000)
        }
    }

    companion object {
        private const val PREFS_NAME = "autoglm_prefs"
        private const val KEY_AGENT_URL = "agent_url"
        private const val KEY_API_KEY = "api_key"
        private const val DEFAULT_AGENT_URL = "http://192.168.1.100:8080"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.statusText)
        serverStatusText = findViewById(R.id.serverStatusText)
        agentStatusText = findViewById(R.id.agentStatusText)
        agentUrlInput = findViewById(R.id.agentUrlInput)
        agentApiKeyInput = findViewById(R.id.agentApiKeyInput)
        openSettingsButton = findViewById(R.id.openSettingsButton)
        testConnectionButton = findViewById(R.id.testConnectionButton)
        startAgentButton = findViewById(R.id.startAgentButton)
        stopAgentButton = findViewById(R.id.stopAgentButton)

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        agentUrlInput.setText(prefs.getString(KEY_AGENT_URL, DEFAULT_AGENT_URL))
        agentApiKeyInput.setText(prefs.getString(KEY_API_KEY, ""))

        openSettingsButton.setOnClickListener { openAccessibilitySettings() }
        testConnectionButton.setOnClickListener { testLocalConnection() }

        startAgentButton.setOnClickListener {
            val url = agentUrlInput.text.toString().trim()
            if (url.isEmpty()) {
                Toast.makeText(this, "请输入 Agent 服务器地址", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val apiKey = agentApiKeyInput.text.toString().trim()
            prefs.edit()
                .putString(KEY_AGENT_URL, url)
                .putString(KEY_API_KEY, apiKey)
                .apply()

            val service = AutoGLMAccessibilityService.getInstance()
            if (service == null) {
                Toast.makeText(this, "请先开启无障碍服务", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            service.startPoller(url, apiKey = apiKey)
            updateAgentButtons(true)
        }

        stopAgentButton.setOnClickListener {
            AutoGLMAccessibilityService.getInstance()?.stopPoller()
            updateAgentButtons(false)
        }

        updateStatus()
    }

    override fun onResume() {
        super.onResume()
        handler.post(updateRunnable)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(updateRunnable)
    }

    private fun updateStatus() {
        val service = AutoGLMAccessibilityService.getInstance()

        if (service != null) {
            statusText.text = getString(R.string.service_running)
            serverStatusText.text = getString(
                R.string.server_status,
                getString(R.string.server_running, AutoGLMAccessibilityService.PORT)
            )
            val pollerRunning = service.isPollerRunning()
            updateAgentButtons(pollerRunning)
            agentStatusText.text = if (pollerRunning) {
                getString(R.string.agent_connected, service.getPollerStatus())
            } else {
                getString(R.string.agent_disconnected)
            }
        } else {
            statusText.text = getString(R.string.service_stopped)
            serverStatusText.text = getString(
                R.string.server_status,
                getString(R.string.server_stopped)
            )
            agentStatusText.text = getString(R.string.agent_disconnected)
            updateAgentButtons(false)
        }
    }

    private fun updateAgentButtons(pollerRunning: Boolean) {
        startAgentButton.isEnabled = !pollerRunning
        stopAgentButton.isEnabled = pollerRunning
        agentUrlInput.isEnabled = !pollerRunning
        agentApiKeyInput.isEnabled = !pollerRunning
    }

    private fun openAccessibilitySettings() {
        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
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
                    } else {
                        Toast.makeText(this, getString(R.string.connection_failed, "HTTP $code"), Toast.LENGTH_SHORT).show()
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, getString(R.string.connection_failed, e.message), Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }
}
