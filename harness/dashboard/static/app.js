(function () {
  'use strict';

  const API_BASE = '/api';

  let pollingInterval = null;
  let pollingPaused = false;
  let agentsData = [];
  let selectedAgent = null;
  let logEventSource = null;

  const $ = (sel, ctx) => (ctx || document).querySelector(sel);
  const $$ = (sel, ctx) => Array.from((ctx || document).querySelectorAll(sel));

  const Dom = {
    agentList: () => $('#agent-list'),
    emptyState: () => $('#empty-state'),
    loadingState: () => $('#loading-state'),
    agentCountBadge: () => $('#agent-count-badge'),
    refreshIndicator: () => $('#refresh-indicator'),
    statTotal: () => $('#stat-total'),
    statRunning: () => $('#stat-running-count'),
    statusIndicator: () => $('#status-indicator'),
    statProjects: () => $('#stat-projects-count'),
    statBranch: () => $('#stat-base-branch'),

    detailPanel: () => $('#detail-panel'),
    detailBody: () => $('#detail-body'),
    btnCloseDetail: () => $('#btn-close-detail'),

    modalOverlay: () => $('#modal-overlay'),
    btnCloseModal: () => $('#btn-close-modal'),
    btnCancelDispatch: () => $('#btn-cancel-dispatch'),
    dispatchForm: () => $('#dispatch-form'),
    projectSelect: () => $('#task-project'),
    taskNameInput: () => $('#task-name'),
    agentSelect: () => $('#task-agent'),
    baseBranchInput: () => $('#task-base-branch'),
    dockerCheckbox: () => $('#task-docker'),
    launchCheckbox: () => $('#task-launch'),
    btnSubmit: () => $('#btn-submit-dispatch'),
    errorProject: () => $('#error-project'),
    errorTaskName: () => $('#error-task-name'),

    btnNewTask: () => $('#btn-new-task'),
    btnEmptyDispatch: () => $('#btn-empty-dispatch'),

    toastContainer: () => $('#toast-container'),

    credContainer: () => $('#cred-indicator'),
    credTooltip: () => $('#cred-tooltip'),
    filterSearch: () => $('#filter-search'),
    filterProject: () => $('#filter-project'),
    filterType: () => $('#filter-type'),
    filterStatus: () => $('#filter-status'),
    filterCount: () => $('#filter-count'),
    btnFilterReset: () => $('#btn-filter-reset'),
  };

  // ── API Client ──

  async function apiGet(path) {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Request failed: ${res.status}`);
    }
    return res.json();
  }

  async function apiPost(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || `Request failed: ${res.status}`);
    }
    return data;
  }

  // ── Toast System ──

  function showToast(message, type, duration) {
    type = type || 'info';
    duration = duration || 4000;
    const iconMap = { success: '\u2713', error: '\u2717', warning: '\u26A0', info: '\u25CB' };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="toast-icon">${iconMap[type] || iconMap.info}</span><span>${escapeHtml(message)}</span><button class="toast-close">x</button>`;
    el.querySelector('.toast-close').addEventListener('click', () => removeToast(el));
    Dom.toastContainer().appendChild(el);
    setTimeout(() => removeToast(el), duration);
  }

  function removeToast(el) {
    if (el.classList.contains('removing')) return;
    el.classList.add('removing');
    setTimeout(() => el.remove(), 200);
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Formatting Helpers ──

  function timeAgo(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr.replace(' ', 'T'));
    if (isNaN(date.getTime())) return dateStr;
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function phaseClass(phase) {
    const p = (phase || '').toLowerCase();
    if (p.includes('规划') || p.includes('plan')) return 'planning';
    if (p.includes('编码') || p.includes('cod')) return 'coding';
    if (p.includes('评审') || p.includes('review')) return 'review';
    if (p.includes('归档') || p.includes('archive')) return 'archive';
    if (p.includes('brain') || p.includes('头脑')) return 'brainstorm';
    if (p.includes('init') || p === 'unknown' || p === 'n/a') return 'initialized';
    return 'planning';
  }

  function projectBadgeClass(project) {
    return project === 'harness-flow' ? 'harness-flow' : '';
  }

  function phaseDisplay(phase) {
    if (!phase || phase === 'N/A') return 'Init';
    return phase;
  }

  // ── Agent List Rendering ──

  function renderAgentList(agents) {
    agentsData = agents;
    const list = Dom.agentList();
    const empty = Dom.emptyState();
    const loading = Dom.loadingState();
    const badge = Dom.agentCountBadge();

    loading.classList.add('hidden');
    badge.textContent = agents.length;

    if (agents.length === 0) {
      list.innerHTML = '';
      empty.classList.add('visible');
      return;
    }
    empty.classList.remove('visible');

    list.innerHTML = agents.map(agent => buildAgentCard(agent)).join('');

    $$('.agent-card').forEach(card => {
      card.addEventListener('click', () => {
        const taskName = card.dataset.task;
        const agent = agentsData.find(a => a.task === taskName);
        if (agent) openDetail(agent);
      });
      if (selectedAgent && card.dataset.task === selectedAgent.task) {
        card.classList.add('active');
      }
    });
  }

  function buildAgentCard(agent) {
    const running = agent.running;
    const isActive = selectedAgent && selectedAgent.task === agent.task;
    return `
      <div class="agent-card${isActive ? ' active' : ''}" data-task="${escapeHtml(agent.task)}">
        <span class="agent-status-dot ${running ? 'running' : 'stopped'}"></span>
        <div class="agent-info">
          <div class="agent-info-top">
            <span class="agent-task-name">${escapeHtml(agent.task)}</span>
            <span class="agent-project-badge ${projectBadgeClass(agent.project)}">${escapeHtml(agent.project)}</span>
          </div>
          <div class="agent-meta">
            <span class="agent-meta-item"><span class="ico">●</span> ${escapeHtml(agent.agent_type)}</span>
            <span class="agent-meta-item"><span class="ico">●</span> ${escapeHtml(agent.status)}</span>
          </div>
        </div>
        <div class="agent-badges">
          <span class="badge badge-phase ${phaseClass(agent.phase)}">${escapeHtml(phaseDisplay(agent.phase))}</span>
          ${agent.docker ? '<span class="badge badge-docker">Docker</span>' : ''}
        </div>
        <span class="agent-time">${timeAgo(agent.created_at)}</span>
      </div>
    `;
  }

  // ── Detail Panel ──

  function openDetail(agent) {
    stopLogStream();
    selectedAgent = agent;
    const panel = Dom.detailPanel();
    const body = Dom.detailBody();
    panel.classList.add('open');

    $$('.agent-card').forEach(c => c.classList.toggle('active', c.dataset.task === agent.task));

    const contextPreview = agent.context
      ? agent.context.substring(0, 500) + (agent.context.length > 500 ? '...' : '')
      : '';

    body.innerHTML = `
      <div class="detail-section">
        <div class="detail-section-title">Metadata</div>
        <div class="detail-grid">
          <div class="detail-field">
            <span class="detail-label">Task</span>
            <span class="detail-value">${escapeHtml(agent.task)}</span>
          </div>
          <div class="detail-field">
            <span class="detail-label">Project</span>
            <span class="detail-value">${escapeHtml(agent.project)}</span>
          </div>
          <div class="detail-field full">
            <span class="detail-label">Branch</span>
            <span class="detail-value code-path">${escapeHtml(agent.branch)}</span>
          </div>
          <div class="detail-field">
            <span class="detail-label">Agent</span>
            <span class="detail-value">${escapeHtml(agent.agent_type)}</span>
          </div>
          <div class="detail-field">
            <span class="detail-label">Status</span>
            <span class="detail-value">${escapeHtml(agent.status)}</span>
          </div>
          <div class="detail-field">
            <span class="detail-label">Phase</span>
            <span class="detail-value">${escapeHtml(phaseDisplay(agent.phase))}</span>
          </div>
          <div class="detail-field">
            <span class="detail-label">Docker</span>
            <span class="detail-value">${agent.docker ? 'Yes' : 'No'}</span>
          </div>
          <div class="detail-field full">
            <span class="detail-label">Created</span>
            <span class="detail-value">${escapeHtml(agent.created_at)}</span>
          </div>
          <div class="detail-field full">
            <span class="detail-label">Worktree</span>
            <span class="detail-value code-path">${escapeHtml(agent.worktree_path)}</span>
          </div>
        </div>
      </div>
      ${contextPreview ? `
      <div class="detail-section">
        <div class="detail-section-title">Context Preview</div>
        <div class="detail-context">${escapeHtml(contextPreview)}</div>
      </div>
      ` : ''}
      <div class="detail-section detail-logs">
        <div class="detail-section-title">Logs</div>
        <div class="log-controls">
          <button class="btn btn-secondary btn-sm log-load-btn">Load Logs</button>
          <label class="toggle">
            <input type="checkbox" class="log-stream-toggle">
            <span class="toggle-track"></span>
            <span class="toggle-label">Stream</span>
          </label>
        </div>
        <div class="log-terminal" data-agent="${escapeHtml(agent.task)}">
          <div class="log-placeholder">Click "Load Logs" to fetch recent output...</div>
        </div>
      </div>
      <div class="detail-actions">
        <button class="btn btn-secondary btn-view-worktree" data-path="${escapeHtml(agent.worktree_path)}">View Worktree</button>
        <button class="btn btn-danger btn-cleanup" data-task="${escapeHtml(agent.task)}">Cleanup</button>
      </div>
    `;

    body.querySelector('.btn-view-worktree').addEventListener('click', () => {
      showToast(`Worktree: ${agent.worktree_path}`, 'info');
    });

    body.querySelector('.btn-cleanup').addEventListener('click', () => {
      showToast(`Cleanup for "${agent.task}" — use ./harness/cleanup.sh ${agent.task}`, 'warning', 6000);
    });

    const logLoadBtn = body.querySelector('.log-load-btn');
    if (logLoadBtn) {
      logLoadBtn.addEventListener('click', function () {
        loadLogs(agent.task);
      });
    }

    const logStreamToggle = body.querySelector('.log-stream-toggle');
    if (logStreamToggle) {
      logStreamToggle.addEventListener('change', function () {
        if (logStreamToggle.checked) {
          startLogStream(agent.task);
        } else {
          stopLogStream();
        }
      });
    }
  }

  function closeDetail() {
    stopLogStream();
    selectedAgent = null;
    Dom.detailPanel().classList.remove('open');
    $$('.agent-card').forEach(c => c.classList.remove('active'));
  }

  // ── Status Bar Update ──

  function updateStatusBar(status) {
    Dom.statTotal().textContent = status.total_agents;
    Dom.statRunning().textContent = status.running_agents;
    Dom.statProjects().textContent = status.projects_count;
    Dom.statBranch().textContent = status.base_branch;

    const indicator = Dom.statusIndicator();
    if (status.running_agents > 0) {
      indicator.className = 'stat-dot active';
    } else {
      indicator.className = 'stat-dot inactive';
    }
  }

  // ── Polling ──

  async function fetchAll() {
    if (pollingPaused) return;
    try {
      const [agents, status] = await Promise.all([
        apiGet('/agents'),
        apiGet('/status'),
      ]);
      agentsData = agents;
      renderFiltered();
      updateStatusBar(status);
    } catch (err) {
      console.error('Poll failed:', err);
    }
  }

  function startPolling() {
    if (pollingInterval) return;
    pollingPaused = false;
    Dom.refreshIndicator().classList.remove('paused');
    fetchAll();
    pollingInterval = setInterval(fetchAll, 5000);
  }

  function stopPolling() {
    if (pollingInterval) {
      clearInterval(pollingInterval);
      pollingInterval = null;
    }
    pollingPaused = true;
    Dom.refreshIndicator().classList.add('paused');
  }

  function pausePolling() {
    pollingPaused = true;
    Dom.refreshIndicator().classList.add('paused');
  }

  function resumePolling() {
    pollingPaused = false;
    Dom.refreshIndicator().classList.remove('paused');
    fetchAll();
  }

  // ── Credential Functions ──

  async function fetchCredentials() {
    try {
      const data = await apiGet('/credentials');
      renderCredentialIndicator(data.agents);
    } catch (err) {
      console.error('Failed to fetch credentials:', err);
    }
  }

  function renderCredentialIndicator(agents) {
    const container = Dom.credContainer();
    if (!container) return;
    const initials = { claude: 'C', gemini: 'G', opencode: 'O' };
    container.innerHTML = Object.keys(agents).map(function (name) {
      const info = agents[name];
      const cls = info.configured ? 'configured' : 'unconfigured';
      const initial = initials[name] || name.charAt(0).toUpperCase();
      return '<span class="cred-dot ' + cls + '" data-agent="' + name + '">' + initial + '</span>';
    }).join('');
  }

  function toggleCredTooltip(indicator) {
    var dots = indicator.querySelectorAll('.cred-dot');
    var html = '';
    dots.forEach(function (dot) {
      var agent = dot.dataset.agent;
      var configured = dot.classList.contains('configured');
      var status = configured ? 'Configured' : 'Not configured';
      html += '<div class="cred-tooltip-item">' +
        '<span class="cred-tooltip-dot ' + (configured ? 'configured' : 'unconfigured') + '"></span>' +
        '<span>' + agent + '</span>' +
        '<span>' + status + '</span>' +
        '</div>';
    });
    var tip = Dom.credTooltip();
    if (!tip) return;
    tip.innerHTML = '<div style="font-weight:600;margin-bottom:6px;color:#F8FAFC;">Credentials</div>' + html;
    var rect = indicator.getBoundingClientRect();
    tip.style.top = (rect.bottom + 8) + 'px';
    tip.style.left = Math.max(8, rect.left) + 'px';
    tip.classList.toggle('visible');
  }

  function initCredentialIndicator() {
    var left = document.querySelector('.status-bar-left');
    if (!left) return;
    var indicator = document.createElement('div');
    indicator.className = 'cred-indicator';
    indicator.id = 'cred-indicator';
    left.appendChild(indicator);
    var tooltip = document.createElement('div');
    tooltip.className = 'cred-tooltip';
    tooltip.id = 'cred-tooltip';
    document.body.appendChild(tooltip);
    indicator.addEventListener('click', function (e) {
      e.stopPropagation();
      toggleCredTooltip(indicator);
    });
    document.addEventListener('click', function () {
      var tip = Dom.credTooltip();
      if (tip) tip.classList.remove('visible');
    });
  }

  // ── Filter Functions ──

  function initFilterBar() {
    var section = document.getElementById('agent-list-section');
    if (!section) return;
    var bar = document.createElement('div');
    bar.className = 'filter-bar';
    bar.innerHTML =
      '<input type="text" class="filter-input" id="filter-search" placeholder="Search by task or project..." autocomplete="off">' +
      '<select class="filter-select" id="filter-project"><option value="">All Projects</option></select>' +
      '<select class="filter-select" id="filter-type"><option value="">All Types</option></select>' +
      '<select class="filter-select" id="filter-status"><option value="">All Status</option></select>' +
      '<span class="filter-count" id="filter-count"></span>' +
      '<button class="btn btn-secondary btn-sm" id="btn-filter-reset">Reset</button>';
    var sectionHeader = section.querySelector('.section-header');
    sectionHeader.parentNode.insertBefore(bar, sectionHeader.nextSibling);
    Dom.filterSearch().addEventListener('input', renderFiltered);
    Dom.filterProject().addEventListener('change', renderFiltered);
    Dom.filterType().addEventListener('change', renderFiltered);
    Dom.filterStatus().addEventListener('change', renderFiltered);
    Dom.btnFilterReset().addEventListener('click', function () {
      Dom.filterSearch().value = '';
      Dom.filterProject().value = '';
      Dom.filterType().value = '';
      Dom.filterStatus().value = '';
      renderFiltered();
    });
  }

  function updateFilterDropdowns(agents) {
    var projectSelect = Dom.filterProject();
    var typeSelect = Dom.filterType();
    var statusSelect = Dom.filterStatus();
    if (!projectSelect) return;
    var currentProject = projectSelect.value;
    var currentType = typeSelect.value;
    var currentStatus = statusSelect.value;
    var projects = [...new Set(agents.map(function (a) { return a.project; }))].sort();
    var types = [...new Set(agents.map(function (a) { return a.agent_type; }))].sort();
    var statuses = [...new Set(agents.map(function (a) { return a.status; }))].sort();
    projectSelect.innerHTML = '<option value="">All Projects</option>' + projects.map(function (p) {
      return '<option value="' + escapeHtml(p) + '">' + escapeHtml(p) + '</option>';
    }).join('');
    typeSelect.innerHTML = '<option value="">All Types</option>' + types.map(function (t) {
      return '<option value="' + escapeHtml(t) + '">' + escapeHtml(t) + '</option>';
    }).join('');
    statusSelect.innerHTML = '<option value="">All Status</option>' + statuses.map(function (s) {
      return '<option value="' + escapeHtml(s) + '">' + escapeHtml(s) + '</option>';
    }).join('');
    if (currentProject) projectSelect.value = currentProject;
    if (currentType) typeSelect.value = currentType;
    if (currentStatus) statusSelect.value = currentStatus;
  }

  function renderFiltered() {
    updateFilterDropdowns(agentsData);
    var searchStr = (Dom.filterSearch().value || '').toLowerCase();
    var projectVal = Dom.filterProject().value;
    var typeVal = Dom.filterType().value;
    var statusVal = Dom.filterStatus().value;
    var filtered = agentsData.filter(function (agent) {
      if (searchStr && agent.task.toLowerCase().indexOf(searchStr) === -1 && agent.project.toLowerCase().indexOf(searchStr) === -1) return false;
      if (projectVal && agent.project !== projectVal) return false;
      if (typeVal && agent.agent_type !== typeVal) return false;
      if (statusVal && agent.status !== statusVal) return false;
      return true;
    });
    Dom.filterCount().textContent = filtered.length === agentsData.length ? '' : filtered.length + ' of ' + agentsData.length;
    renderAgentList(filtered);
  }

  // ── Log Functions ──

  async function loadLogs(taskName) {
    var terminal = document.querySelector('.log-terminal[data-agent="' + taskName + '"]');
    if (!terminal) return;
    terminal.innerHTML = '<div class="log-line">Loading logs...</div>';
    try {
      var data = await apiGet('/agents/' + encodeURIComponent(taskName) + '/log?tail=200');
      if (data.lines && data.lines.length > 0) {
        terminal.innerHTML = data.lines.map(function (l) {
          return '<div class="log-line">' + escapeHtml(l) + '</div>';
        }).join('') + '<div class="log-line log-muted">--- ' + data.total + ' total lines ---</div>';
      } else {
        terminal.innerHTML = '<div class="log-line log-placeholder">No log output available.</div>';
      }
    } catch (err) {
      terminal.innerHTML = '<div class="log-line" style="color:#EF4444;">Error: ' + escapeHtml(err.message) + '</div>';
    }
  }

  function startLogStream(taskName) {
    if (logEventSource) logEventSource.close();
    var terminal = document.querySelector('.log-terminal[data-agent="' + taskName + '"]');
    if (!terminal) return;
    terminal.innerHTML = '<div class="log-line"><span class="log-stream-dot"></span>Streaming logs...</div>';
    logEventSource = new EventSource('/api/agents/' + encodeURIComponent(taskName) + '/log/stream');
    logEventSource.onmessage = function (e) {
      try {
        var data = JSON.parse(e.data);
        if (data.line) {
          var lineEl = document.createElement('div');
          lineEl.className = 'log-line';
          lineEl.textContent = data.line;
          terminal.appendChild(lineEl);
          terminal.scrollTop = terminal.scrollHeight;
        }
      } catch (_) {}
    };
  }

  function stopLogStream() {
    if (logEventSource) {
      logEventSource.close();
      logEventSource = null;
    }
  }

  // ── Modal / Form ──

  function openModal() {
    pausePolling();
    Dom.modalOverlay().classList.add('open');
    loadProjectOptions();
    Dom.taskNameInput().value = '';
    Dom.errorProject().textContent = '';
    Dom.errorTaskName().textContent = '';
    Dom.projectSelect().classList.remove('error');
    Dom.taskNameInput().classList.remove('error');
    Dom.btnSubmit().disabled = false;
  }

  function closeModal() {
    Dom.modalOverlay().classList.remove('open');
    resumePolling();
  }

  async function loadProjectOptions() {
    try {
      const projects = await apiGet('/projects');
      const select = Dom.projectSelect();
      select.innerHTML = '<option value="">-- Select project --</option>';
      projects.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name;
        select.appendChild(opt);
      });
    } catch (err) {
      showToast('Failed to load projects: ' + err.message, 'error');
    }
  }

  function validateForm() {
    let valid = true;
    const project = Dom.projectSelect().value;
    const taskName = Dom.taskNameInput().value.trim();

    if (!project) {
      Dom.errorProject().textContent = 'Project is required';
      Dom.projectSelect().classList.add('error');
      valid = false;
    } else {
      Dom.errorProject().textContent = '';
      Dom.projectSelect().classList.remove('error');
    }

    if (!taskName) {
      Dom.errorTaskName().textContent = 'Task name is required';
      Dom.taskNameInput().classList.add('error');
      valid = false;
    } else if (taskName.length < 3) {
      Dom.errorTaskName().textContent = 'Task name must be at least 3 characters';
      Dom.taskNameInput().classList.add('error');
      valid = false;
    } else {
      Dom.errorTaskName().textContent = '';
      Dom.taskNameInput().classList.remove('error');
    }

    return valid;
  }

  async function handleFormSubmit(e) {
    e.preventDefault();
    if (!validateForm()) return;

    const payload = {
      project: Dom.projectSelect().value,
      task_name: Dom.taskNameInput().value.trim(),
      agent: Dom.agentSelect().value,
      base_branch: Dom.baseBranchInput().value.trim() || 'dev',
      docker: Dom.dockerCheckbox().checked,
      launch: Dom.launchCheckbox().checked,
    };

    Dom.btnSubmit().disabled = true;
    Dom.btnSubmit().textContent = 'Dispatching...';

    try {
      const result = await apiPost('/dispatch', payload);
      if (result.success) {
        showToast(`Task "${payload.task_name}" dispatched successfully`, 'success');
        closeModal();
        fetchAll();
      } else {
        showToast('Dispatch failed: ' + result.message, 'error');
      }
    } catch (err) {
      showToast('Dispatch error: ' + err.message, 'error');
    } finally {
      Dom.btnSubmit().disabled = false;
      Dom.btnSubmit().textContent = 'Dispatch';
    }
  }

  // ── Initialization ──

  function init() {
    Dom.loadingState().classList.remove('hidden');

    Dom.btnNewTask().addEventListener('click', openModal);
    Dom.btnEmptyDispatch().addEventListener('click', openModal);
    Dom.btnCloseModal().addEventListener('click', closeModal);
    Dom.btnCancelDispatch().addEventListener('click', closeModal);
    Dom.btnCloseDetail().addEventListener('click', closeDetail);

    Dom.modalOverlay().addEventListener('click', (e) => {
      if (e.target === Dom.modalOverlay()) closeModal();
    });

    Dom.dispatchForm().addEventListener('submit', handleFormSubmit);

    Dom.taskNameInput().addEventListener('input', () => {
      if (Dom.taskNameInput().classList.contains('error')) {
        validateForm();
      }
    });

    Dom.projectSelect().addEventListener('change', () => {
      if (Dom.projectSelect().classList.contains('error')) {
        validateForm();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (Dom.modalOverlay().classList.contains('open')) closeModal();
        else if (Dom.detailPanel().classList.contains('open')) closeDetail();
      }
    });

    initFilterBar();
    initCredentialIndicator();
    fetchCredentials();

    startPolling();
  }

  // Boot on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
