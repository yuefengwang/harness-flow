/**
 * deploy.js — SSE 驱动的实时部署日志客户端
 *
 * 连接到 /tasks/{name}/deploy/sse 端点，实时接收部署日志并显示。
 * 同时定期轮询 /tasks/{name}/deploy/status 获取部署状态。
 */
(function() {
    'use strict';

    var DeployMonitor = {
        taskName: null,
        eventSource: null,
        statusInterval: null,
        logViewer: null,
        autoScroll: true,
        deployInProgress: false,
        statusBadge: null,

        /**
         * 初始化部署监控
         * @param {string} taskName - 任务名称
         */
        init: function(taskName) {
            this.taskName = taskName;
            this.logViewer = document.getElementById('deploy-log-content');
            this.statusBadge = document.getElementById('deploy-status-badge');

            if (!this.logViewer) return;

            // 检查是否正在部署中
            this.checkStatus();
        },

        /**
         * 检查部署状态并决定是否连接 SSE
         */
        checkStatus: function() {
            var self = this;
            fetch('/tasks/' + self.taskName + '/deploy/status')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.status === 'deploying') {
                        self.deployInProgress = true;
                        self.connectSSE();
                        self.startPolling();
                    } else if (data.status === 'deployed') {
                        self.showSuccess(data.url);
                        // 仍然连接 SSE 以获取可能的新日志
                        self.connectSSE();
                    } else if (data.status === 'deploy_failed') {
                        self.showFailed();
                        self.connectSSE();
                    }
                })
                .catch(function() {});
        },

        /**
         * 连接 SSE 端点
         */
        connectSSE: function() {
            var self = this;
            if (this.eventSource) {
                this.eventSource.close();
            }

            this.eventSource = new EventSource('/tasks/' + this.taskName + '/deploy/sse');

            this.eventSource.onmessage = function(event) {
                var line = event.data;
                if (line) {
                    self.appendLog(line);
                }
            };

            this.eventSource.onerror = function() {
                // SSE 连接断开后尝试重连（EventSource 会自动重连）
                console.log('deploy SSE disconnected, will retry...');
            };

            this.eventSource.onopen = function() {
                console.log('deploy SSE connected');
            };
        },

        /**
         * 定期轮询部署状态
         */
        startPolling: function() {
            var self = this;
            if (this.statusInterval) {
                clearInterval(this.statusInterval);
            }
            this.statusInterval = setInterval(function() {
                fetch('/tasks/' + self.taskName + '/deploy/status')
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.status === 'deployed') {
                            self.showSuccess(data.url);
                            self.deployInProgress = false;
                            if (self.statusInterval) {
                                clearInterval(self.statusInterval);
                                self.statusInterval = null;
                            }
                        } else if (data.status === 'deploy_failed') {
                            self.showFailed();
                            self.deployInProgress = false;
                            if (self.statusInterval) {
                                clearInterval(self.statusInterval);
                                self.statusInterval = null;
                            }
                        }
                    })
                    .catch(function() {});
            }, 2000);
        },

        /**
         * 追加日志行到查看器
         */
        appendLog: function(line) {
            if (!this.logViewer) return;

            // 解析时间戳和内容
            var displayLine = line;
            var match = line.match(/^\[([^\]]+)\]\s*(.*)/);
            if (match) {
                displayLine = '<span class="deploy-log-time">[' + match[1] + ']</span> ' + this._escapeHtml(match[2]);
            } else {
                displayLine = this._escapeHtml(line);
            }

            var div = document.createElement('div');
            div.className = 'deploy-log-line';
            div.innerHTML = displayLine;
            this.logViewer.appendChild(div);

            // 自动滚动到底部
            if (this.autoScroll) {
                var container = this.logViewer.parentElement;
                container.scrollTop = container.scrollHeight;
            }

            // 分析日志内容，更新状态指示器
            this._updateStepIndicator(line);
        },

        /**
         * 更新部署步骤指示器
         */
        _updateStepIndicator: function(line) {
            var lower = line.toLowerCase();

            if (lower.includes('扫描') || lower.includes('检测项目') || lower.includes('step 1')) {
                this._setStep('detect', '进行中');
            } else if (lower.includes('依赖') || lower.includes('pip install') || lower.includes('npm install') || lower.includes('step 2')) {
                this._setStep('deps', '进行中');
            } else if (lower.includes('端口') || lower.includes('port') || lower.includes('step 3')) {
                this._setStep('port', '进行中');
            } else if (lower.includes('启动') || lower.includes('uvicorn') || lower.includes('flask') || lower.includes('step 4')) {
                this._setStep('start', '进行中');
            } else if (lower.includes('tunnel') || lower.includes('cloudflare')) {
                this._setStep('tunnel', '进行中');
            }

            if (lower.includes('部署成功') || lower.includes('部署完成') || lower.includes('service_url')) {
                this._setStep('done', '完成');
            }
        },

        /**
         * 设置步骤状态
         */
        _setStep: function(stepId, status) {
            var el = document.getElementById('step-' + stepId);
            if (!el) return;

            el.className = 'step-item step-' + status;
            var icon = el.querySelector('.step-icon');
            if (icon) {
                if (status === '进行中') {
                    icon.textContent = '◌';
                } else if (status === '完成') {
                    icon.textContent = '✓';
                } else {
                    icon.textContent = '○';
                }
            }
        },

        /**
         * 显示部署成功
         */
        showSuccess: function(url) {
            var banner = document.getElementById('deploy-success-banner');
            if (banner) {
                banner.classList.remove('hidden');
                var link = banner.querySelector('.deploy-url-link');
                if (link) {
                    link.href = url;
                    link.textContent = url;
                }
            }
            if (this.statusBadge) {
                this.statusBadge.className = 'badge badge-success';
                this.statusBadge.textContent = '✓ 已部署';
            }
            this._setStep('done', '完成');
        },

        /**
         * 显示部署失败
         */
        showFailed: function() {
            if (this.statusBadge) {
                this.statusBadge.className = 'badge badge-error';
                this.statusBadge.textContent = '✗ 部署失败';
            }
        },

        /**
         * HTML 转义
         */
        _escapeHtml: function(text) {
            var div = document.createElement('div');
            div.appendChild(document.createTextNode(text));
            return div.innerHTML;
        },

        /**
         * 断开连接并清理
         */
        disconnect: function() {
            if (this.eventSource) {
                this.eventSource.close();
                this.eventSource = null;
            }
            if (this.statusInterval) {
                clearInterval(this.statusInterval);
                this.statusInterval = null;
            }
        }
    };

    // 页面加载后自动初始化
    var taskNameEl = document.getElementById('deploy-task-name');
    if (taskNameEl) {
        DeployMonitor.init(taskNameEl.value);
    }

    // 导出到全局
    window.DeployMonitor = DeployMonitor;
})();
