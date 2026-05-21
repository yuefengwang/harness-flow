(function () {
  "use strict";

  var app = document.getElementById("console-app");
  if (!app) return;

  var taskName = app.dataset.taskName;
  var logContainer = document.getElementById("log-container");
  var consoleInput = document.getElementById("console-input");
  var statusDot = document.getElementById("status-dot");
  var statusLabel = document.getElementById("status-label");
  var modeIndicator = document.getElementById("input-mode-indicator");
  var questionArea = document.getElementById("question-area");
  var questionPrompt = document.getElementById("question-prompt");
  var questionOptions = document.getElementById("question-options");
  var btnStart = document.getElementById("btn-start-engine");

  var inputMode = "none";
  var pendingQuestion = null;
  var pendingQIdx = 0;
  var sse = null;
  var reconnectTimer = null;
  var pollTimer = null;
  var lastLineCount = 0;
  var sseConnected = false;

  // ── 日志格式化（对齐 .log 文件格式：[ts] source | msg）──

  function parseLogLine(raw) {
    var m = raw.match(/^\[([^\]]+)\]\s+(\S+)\s*\|\s*(.*)/);
    if (m) {
      return { ts: m[1], source: m[2], msg: m[3] };
    }
    return { raw: raw };
  }

  function getSourceColor(source) {
    switch (source) {
      case "agent": return "var(--console-color-agent)";
      case "user":  return "var(--console-color-user)";
      case "sw":    return "var(--console-color-sw)";
      case "error": return "var(--console-color-error)";
      case "system":return "var(--console-color-system)";
      default:      return "var(--text)";
    }
  }

  function getSourceLabel(source) {
    return (source || "?").padEnd(6, " ");
  }

  function appendLog(source, msg, ts) {
    var line = document.createElement("div");
    line.className = "log-line";

    var timeStr = ts ? (ts.split("T")[1] || ts) : "";
    var color = getSourceColor(source);
    line.innerHTML =
      '<span class="log-time">[' + timeStr + ']</span> ' +
      '<span class="log-source" style="color:' + color + '">' + getSourceLabel(source) + '</span> ' +
      '<span class="log-msg">' + escapeHtml(msg) + '</span>';

    logContainer.appendChild(line);
    scrollToBottom();
  }

  function appendRawLine(raw) {
    var parsed = parseLogLine(raw);
    if (parsed.source) {
      appendLog(parsed.source, parsed.msg, parsed.ts);
    } else {
      var line = document.createElement("div");
      line.className = "log-line";
      line.innerHTML = '<span class="log-msg">' + escapeHtml(raw) + '</span>';
      logContainer.appendChild(line);
      scrollToBottom();
    }
  }

  function scrollToBottom() {
    var logs = document.getElementById("console-logs");
    if (logs) logs.scrollTop = logs.scrollHeight;
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  }

  // ── 日志拉取（增量轮询）──

  function fetchLogs(sinceLine) {
    var url = "/tasks/" + taskName + "/log?after_line=" + (sinceLine || 0);
    return fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.lines) {
          data.lines.forEach(function (item) {
            if (item.source) {
              appendLog(item.source, item.msg, item.ts);
            } else {
              appendRawLine(item.raw || "");
            }
          });
        }
        if (typeof data.total_lines === "number") {
          lastLineCount = data.total_lines;
        }
        scrollToBottom();
      })
      .catch(function () {});
  }

  function startLogPolling() {
    stopLogPolling();
    pollTimer = setInterval(function () {
      fetchLogs(lastLineCount);
    }, 2000);
  }

  function stopLogPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ── 状态轮询 ──

  function startPolling() {
    pollTaskState();
    pollTimer = setInterval(pollTaskState, 2000);
  }

  function pollTaskState() {
    fetch("/tasks/" + taskName + "/state")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) return;
        var stageEl = document.querySelector(".console-stage");
        if (stageEl && data.stage_label) {
          stageEl.textContent = data.stage_label;
        }
        if (data.stage_status) {
          setStatus(data.stage_status);
        }
      })
      .catch(function () {});
  }

  // ── 状态更新 ──

  function setStatus(status) {
    statusLabel.textContent = status;
    statusDot.className = "status-dot status-" + status;
  }

  function setInputMode(mode, promptText) {
    inputMode = mode || "none";
    if (mode === "question") {
      modeIndicator.textContent = "❓ Agent 正在等待回答";
      modeIndicator.className = "input-mode-question";
    } else if (mode === "yesno") {
      modeIndicator.textContent = "❓ 请回答 yes/no";
      modeIndicator.className = "input-mode-question";
    } else {
      modeIndicator.textContent = "💬 输入消息或 /命令";
      modeIndicator.className = "input-mode-none";
    }
  }

  // ── SSE 事件处理 ──

  function handleLogEvent(data) {
    if (sseConnected) stopLogPolling();
    appendLog(data.source, data.msg, data.ts);
  }

  function handleQuestionEvent(data) {
    pendingQuestion = data.questions;
    pendingQIdx = data.q_idx || 0;
    showQuestion(pendingQuestion, pendingQIdx);
    setInputMode("question");
  }

  function showQuestion(questions, idx) {
    if (!questions || idx >= questions.length) {
      questionArea.classList.add("hidden");
      consoleInput.focus();
      return;
    }
    var q = questions[idx];
    questionArea.classList.remove("hidden");
    questionPrompt.textContent = q.question || "";
    questionOptions.innerHTML = "";

    if (q.options && q.options.length > 0) {
      consoleInput.style.display = "none";
      q.options.forEach(function (opt) {
        var btn = document.createElement("button");
        btn.className = "btn option-btn";
        btn.textContent = opt;
        btn.onclick = function () { submitAnswer(opt); };
        questionOptions.appendChild(btn);
      });
    } else {
      consoleInput.style.display = "block";
      consoleInput.placeholder = q.question || "输入回答...";
      consoleInput.focus();
      questionOptions.innerHTML = "";
    }
  }

  function submitAnswer(text) {
    fetch("/tasks/" + taskName + "/engine/answer", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "text=" + encodeURIComponent(text),
    });

    appendLog("user", text, new Date().toISOString());
    questionArea.classList.add("hidden");
    consoleInput.style.display = "block";
    consoleInput.value = "";
    setInputMode("none");
    pendingQuestion = null;
    pendingQIdx = 0;
  }

  function handleSettlementEvent(data) {
    var msg = "🏁 阶段结算: " + (data.stage || "") + " (" + (data.stage_status || "") + ")";
    appendLog("sw", msg, new Date().toISOString());
  }

  function handleStatusEvent(data) {
    if (data.agent_status) setStatus(data.agent_status);
    if (data.status) setStatus(data.status);
  }

  function handleStateEvent(data) {
    if (data.stage) {
      var stageEl = document.querySelector(".console-stage");
      if (stageEl) stageEl.textContent = data.stage;
    }
    if (data.stage_status) setStatus(data.stage_status);
  }

  // ── SSE 连接管理 ──

  function connectSSE() {
    if (sse) {
      sse.close();
      sse = null;
    }

    sseConnected = false;
    sse = new EventSource("/tasks/" + taskName + "/sse");

    sse.onmessage = function (event) {
      if (!event.data || event.data.trim() === "") return;
      try {
        var data = JSON.parse(event.data);
        switch (data.type) {
          case "log":        handleLogEvent(data); break;
          case "question":   handleQuestionEvent(data); break;
          case "settlement": handleSettlementEvent(data); break;
          case "status":     handleStatusEvent(data); break;
          case "state":      handleStateEvent(data); break;
        }
      } catch (e) {
        console.warn("SSE parse error:", e);
      }
    };

    sse.onopen = function () {
      sseConnected = true;
      stopLogPolling();
      appendLog("sw", "SSE 实时连接已建立", new Date().toISOString());
    };

    sse.onerror = function () {
      sseConnected = false;
      sse.close();
      sse = null;
      appendLog("sw", "⚠️ SSE 连接断开，切换到轮询模式", new Date().toISOString());
      startLogPolling();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(function () {
        appendLog("sw", "正在尝试重连 SSE...", new Date().toISOString());
        connectSSE();
      }, 5000);
    };
  }

  // ── 引擎控制 ──

  window.startEngine = function () {
    var btn = document.getElementById("btn-start-engine");
    btn.disabled = true;
    btn.textContent = "启动中...";

    fetch("/tasks/" + taskName + "/engine/start", {
      method: "POST",
    }).then(function (r) { return r.text(); }).then(function () {
      btn.textContent = "已启动";
      appendLog("sw", "引擎已启动", new Date().toISOString());
      connectSSE();
    }).catch(function (err) {
      btn.textContent = "启动失败";
      appendLog("error", "引擎启动失败: " + err.message, new Date().toISOString());
      btn.disabled = false;
    });
  };

  // ── 消息发送 ──

  window.sendMessage = function () {
    var text = consoleInput.value.trim();
    if (!text) return false;

    consoleInput.value = "";

    if (text.startsWith("/")) {
      return handleCommand(text);
    }

    if (inputMode === "question" && pendingQuestion) {
      submitAnswer(text);
      return false;
    }

    fetch("/tasks/" + taskName + "/engine/answer", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "text=" + encodeURIComponent(text),
    });

    appendLog("user", text, new Date().toISOString());
    return false;
  };

  function handleCommand(text) {
    var parts = text.split(" ");
    var cmd = parts[0].toLowerCase();

    if (cmd === "/advance" || cmd === "/status" || cmd === "/q") {
      fetch("/tasks/" + taskName + "/engine/command", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "cmd=" + encodeURIComponent(text.slice(1)),
      });
      appendLog("sw", "命令: " + text, new Date().toISOString());
    } else {
      appendLog("sw", "未知命令: " + text, new Date().toISOString());
    }
    return false;
  }

  // ── 键盘快捷键 ──

  consoleInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      sendMessage();
    }
  });

  // ── 初始化：加载日志 → 自动滚动 → 启动 SSE/轮询 ──

  function init() {
    fetchLogs(0).then(function () {
      scrollToBottom();
      connectSSE();
    }).catch(function () {
      startLogPolling();
      connectSSE();
    });

    startPolling();
  }

  init();

})();
