/**
 * PathologyAssistantClient
 *
 * 本次修改要点：
 * 1. 轮询从 setInterval 改为 setTimeout 链，避免竞态导致孤儿 interval
 * 2. 引入 stopPolling() 统一停止入口
 * 3. 终态判断收敛到 TERMINAL_STATUSES 集合，绝不漏停
 * 4. sendMessage 加 in-flight 防抖，杜绝并发重复提交
 * 5. 轮询 tick 校验 sessionId，跨 session 自动失效
 * 6. startNewSession / sendMessage 都先 stopPolling()，避免旧轮询残留
 */
class PathologyAssistantClient {
    constructor() {
        console.log('PathologyAssistantClient initialized');
        const host = window.location.hostname || '127.0.0.1';
        // 同源优先（前端挂在 Leader 上时）；否则回退到同主机 8021
        this.apiUrl = window.location.port === '8021'
            ? window.location.origin
            : `http://${host}:8021`;
        console.log('API URL:', this.apiUrl);

        this.sessionId = null;
        this.messageCount = 0;
        this.protocolLogs = [];

        // ★ 轮询状态统一管理
        this.pollingTimer = null;       // setTimeout 句柄
        this.pollingInterval = null;    // 兼容旧代码（不再使用）
        this.sessionCompleted = false;

        this.lastLogTimestamp = null;
        this.currentLoadingId = null;
        this.selectedImage = null;

        // ★ 发送防抖
        this.sending = false;

        this.init();
    }

    async init() {
        await this.detectApiEndpoint();
        this.bindEvents();
    }

    async detectApiEndpoint() {
        try {
            const response = await axios.get(`${this.apiUrl}/health`, { timeout: 3000 });
            if (response.status === 200) {
                this.showConnectionStatus('已连接', 'success');
                return;
            }
        } catch (error) {
            console.error('连接失败:', error.message);
            this.showConnectionStatus('连接失败', 'error');
        }
    }

    showConnectionStatus(status, type) {
        const statusElement = document.getElementById('connection-status');
        if (statusElement) {
            const colors = {
                success: 'text-green-600 bg-green-50 border-green-200',
                warning: 'text-yellow-600 bg-yellow-50 border-yellow-200',
                error: 'text-red-600 bg-red-50 border-red-200',
                info: 'text-blue-600 bg-blue-50 border-blue-200'
            };
            statusElement.className = `px-3 py-1 rounded border text-sm ${colors[type] || colors.error}`;
            statusElement.textContent = `连接状态: ${status}`;
            statusElement.style.display = 'block';
        }
    }

    bindEvents() {
        console.log('Binding events...');
        const sendButton = document.getElementById('send-button');
        const newSessionButton = document.getElementById('new-session-button');
        const userInput = document.getElementById('user-input');
        const clearProtocolLogButton = document.getElementById('clear-protocol-log');
        const imageInput = document.getElementById('image-input');
        const dropZone = document.getElementById('drop-zone');
        const clearImageButton = document.getElementById('clear-image');

        if (sendButton) {
            sendButton.addEventListener('click', () => this.sendMessage());
        }

        if (newSessionButton) {
            newSessionButton.addEventListener('click', () => this.startNewSession());
        }

        if (clearProtocolLogButton) {
            clearProtocolLogButton.addEventListener('click', () => this.clearProtocolLog());
        }

        if (userInput) {
            userInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
        }

        // 图片选择
        if (imageInput) {
            imageInput.addEventListener('change', (e) => {
                const file = e.target.files && e.target.files[0];
                if (file) this.handleSelectImage(file);
            });
        }

        // 拖拽上传
        if (dropZone) {
            const prevent = (e) => { e.preventDefault(); e.stopPropagation(); };
            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(evt => {
                dropZone.addEventListener(evt, prevent);
            });
            dropZone.addEventListener('dragover', () => {
                dropZone.classList.add('border-blue-400', 'bg-blue-50');
            });
            dropZone.addEventListener('dragleave', () => {
                dropZone.classList.remove('border-blue-400', 'bg-blue-50');
            });
            dropZone.addEventListener('drop', (e) => {
                dropZone.classList.remove('border-blue-400', 'bg-blue-50');
                const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
                if (file) this.handleSelectImage(file);
            });
        }

        // 粘贴上传
        document.addEventListener('paste', (e) => {
            const items = e.clipboardData && e.clipboardData.items;
            if (!items) return;
            for (let i = 0; i < items.length; i++) {
                const item = items[i];
                if (item.kind === 'file') {
                    const file = item.getAsFile();
                    if (file && file.type.startsWith('image/')) {
                        this.handleSelectImage(file);
                        break;
                    }
                }
            }
        });

        // 清除图片
        if (clearImageButton) {
            clearImageButton.addEventListener('click', () => this.clearSelectedImage());
        }
    }

    validateImage(file) {
        if (!file.type.startsWith('image/')) {
            this.addMessage('assistant', '仅支持图片文件。', false, 'error');
            return false;
        }
        return true;
    }

    handleSelectImage(file) {
        if (!this.validateImage(file)) return;

        console.log('Selected file:', {
            name: file.name, type: file.type,
            size: file.size,
            sizeMB: (file.size / (1024 * 1024)).toFixed(2) + ' MB'
        });

        if (file.size > 10 * 1024 * 1024) {
            this.selectedImage = {
                name: file.name, type: file.type, size: file.size,
                file: file, dataUrl: null
            };
            this.updateImagePreview();
        } else {
            const reader = new FileReader();
            reader.onload = () => {
                this.selectedImage = {
                    name: file.name, type: file.type, size: file.size,
                    file: file, dataUrl: reader.result
                };
                this.updateImagePreview();
            };
            reader.onerror = () => {
                this.addMessage('assistant', '图片读取失败，请尝试其他图片。', false, 'error');
            };
            reader.readAsDataURL(file);
        }
    }

    updateImagePreview() {
        const preview = document.getElementById('image-preview');
        const previewImg = document.getElementById('preview-img');
        const previewMeta = document.getElementById('preview-meta');
        const clearBtn = document.getElementById('clear-image');
        if (this.selectedImage) {
            if (preview) preview.classList.remove('hidden');
            if (this.selectedImage.dataUrl) {
                if (previewImg) previewImg.src = this.selectedImage.dataUrl;
            } else {
                if (previewImg) previewImg.style.display = 'none';
            }
            if (previewMeta) {
                const sizeText = this.selectedImage.size > 1024 * 1024
                    ? `${(this.selectedImage.size / (1024 * 1024)).toFixed(1)} MB`
                    : `${(this.selectedImage.size / 1024).toFixed(1)} KB`;
                previewMeta.textContent = `${this.selectedImage.name} · ${sizeText}`;
            }
            if (clearBtn) clearBtn.classList.remove('hidden');
        } else {
            if (preview) preview.classList.add('hidden');
            if (previewImg) {
                previewImg.src = '';
                previewImg.style.display = 'block';
            }
            if (previewMeta) previewMeta.textContent = '';
            if (clearBtn) clearBtn.classList.add('hidden');
            const imageInput = document.getElementById('image-input');
            if (imageInput) imageInput.value = '';
        }
    }

    clearSelectedImage() {
        this.selectedImage = null;
        this.updateImagePreview();
    }

    // ========================================================================
    // 轮询统一管理
    // ========================================================================
    stopPolling() {
        if (this.pollingTimer) {
            clearTimeout(this.pollingTimer);
            this.pollingTimer = null;
        }
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
    }

    // ========================================================================
    // 发送消息（带防抖）
    // ========================================================================
    async sendMessage() {
        if (this.sending) {
            console.warn('[sendMessage] 已有请求在处理中，忽略本次点击');
            return;
        }

        const userInput = document.getElementById('user-input');
        const message = userInput.value.trim();
        if (!message && !this.selectedImage) return;

        this.sending = true;

        // 新请求：先停掉旧轮询、重置状态
        this.stopPolling();
        this.sessionCompleted = false;
        this.lastLogTimestamp = null;

        if (!this.sessionId) {
            this.sessionId = 'session-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
        }

        // 显示用户消息
        let userMessage = message || '';
        if (this.selectedImage) {
            userMessage = userMessage
                ? `${userMessage}\n[已附带图片：${this.selectedImage.name}]`
                : `[已上传图片：${this.selectedImage.name}]`;
        }
        if (userMessage) this.addMessage('user', userMessage);
        userInput.value = '';

        const loadingId = this.addMessage('assistant', '正在协调智能体处理您的请求...', true);
        this.currentLoadingId = loadingId;

        try {
            // 上传图片
            let uploaded = null;
            if (this.selectedImage) {
                let file;
                if (this.selectedImage.file) {
                    file = this.selectedImage.file;
                } else {
                    const blob = await (await fetch(this.selectedImage.dataUrl)).blob();
                    file = new File([blob], this.selectedImage.name || 'image.png',
                                    { type: this.selectedImage.type || blob.type });
                }
                const form = new FormData();
                form.append('file', file);
                const upRes = await axios.post(`${this.apiUrl}/upload`, form, {
                    headers: { 'Content-Type': 'multipart/form-data' }
                });
                uploaded = upRes.data;
            }

            // 组合消息
            let combinedMessage = message || '';
            if (uploaded?.path) {
                combinedMessage = combinedMessage
                    ? `${combinedMessage}\n${uploaded.path}`
                    : uploaded.path;
            }

            this.addProtocolLog('user_request', {
                message: combinedMessage || '(仅图片)',
                has_image: !!uploaded,
                session_id: this.sessionId,
                wsi_path: uploaded?.path || null,
                image_url: uploaded ? `${this.apiUrl}${uploaded.url}`.replace(/\/$/, '') : null,
                timestamp: new Date().toISOString()
            });

            // 发起异步协调
            const payload = {
                message: combinedMessage,
                session_id: this.sessionId
            };
            if (uploaded) {
                payload.wsi_path = uploaded.path;
                payload.image_url = `${this.apiUrl}${uploaded.url}`.replace(/\/$/, '');
            }

            const response = await axios.post(`${this.apiUrl}/coordinate-async`, payload);

            if (response.data.session_id) {
                this.sessionId = response.data.session_id;
            }

            // 清理已选图片
            if (this.selectedImage) this.clearSelectedImage();

            // ★ 启动轮询（内部会先 stopPolling）
            this.startPollingLogs();

        } catch (error) {
            console.error('Error in sendMessage:', error);
            this.removeMessage(loadingId);
            this.currentLoadingId = null;
            this.addMessage('assistant', '抱歉，处理请求时出现错误。请稍后重试。', false, 'error');
            this.addProtocolLog('coordination_error', {
                session_id: this.sessionId,
                error: error.message,
                timestamp: new Date().toISOString()
            });
        } finally {
            this.sending = false;
        }
    }

    // ========================================================================
    // 轮询：setTimeout 链 + 终态收敛
    // ========================================================================
    startPollingLogs() {
        this.stopPolling();
        this.sessionCompleted = false;

        console.log('Starting log polling for session:', this.sessionId);

        // 捕获当前 session，跨 session 自动失效
        const pollingSession = this.sessionId;

        const tick = async () => {
            // 已换 session 或已完成 → 退出
            if (this.sessionId !== pollingSession) return;
            if (this.sessionCompleted) return;

            try {
                await this.pollLogsAndStatus();
            } catch (e) {
                console.error('Error in poll tick:', e);
            }

            // 再次校验，决定是否继续
            if (this.sessionId === pollingSession && !this.sessionCompleted) {
                this.pollingTimer = setTimeout(tick, 1000);
            }
        };

        // 立即执行一次
        this.pollingTimer = setTimeout(tick, 0);
    }

    async pollLogsAndStatus() {
        if (!this.sessionId) return;

        try {
            const statusResponse = await axios.get(
                `${this.apiUrl}/sessions/${this.sessionId}/status`
            );
            const status = statusResponse.data.status;

            // 拉最新日志
            let logsUrl = `${this.apiUrl}/sessions/${this.sessionId}/interaction-logs/latest`;
            if (this.lastLogTimestamp) {
                logsUrl += `?since=${encodeURIComponent(this.lastLogTimestamp)}`;
            }
            const logsResponse = await axios.get(logsUrl);
            const newLogs = logsResponse.data.logs;

            if (newLogs && newLogs.length > 0) {
                this.processInteractionLogs(newLogs);
                const latestLog = newLogs[newLogs.length - 1];
                this.lastLogTimestamp = latestLog.timestamp;
            }

            // ★ 终态统一处理
            const TERMINAL = new Set(['completed', 'error', 'failed', 'cancelled']);
            if (TERMINAL.has(status)) {
                if (this.sessionCompleted) return;   // 已处理
                this.sessionCompleted = true;
                this.stopPolling();

                if (this.currentLoadingId) {
                    this.removeMessage(this.currentLoadingId);
                    this.currentLoadingId = null;
                }

                if (status === 'completed') {
                    await this.getFinalResponse();
                    this.addProtocolLog('coordination_complete', {
                        session_id: this.sessionId,
                        success: true,
                        timestamp: new Date().toISOString()
                    });
                } else {
                    this.addMessage('assistant',
                        `处理请求时发生错误（${status}），请稍后重试。`,
                        false, 'error');
                }
            }
        } catch (error) {
            console.error('Error in pollLogsAndStatus:', error);
        }
    }

    async getFinalResponse() {
        try {
            const response = await axios.get(`${this.apiUrl}/sessions/${this.sessionId}`);
            const messages = response.data.messages || [];
            const artifacts = response.data.last_artifacts || {};

            for (let i = messages.length - 1; i >= 0; i--) {
                if (messages[i].role === 'assistant') {
                    this.addMessage('assistant', messages[i].content);
                    break;
                }
            }

            const contourUrl = artifacts?.contour_url
                ? (artifacts.contour_url.startsWith('http')
                    ? artifacts.contour_url
                    : `${this.apiUrl}${artifacts.contour_url}`)
                : null;
            const transparentUrl = artifacts?.transparent_url
                ? (artifacts.transparent_url.startsWith('http')
                    ? artifacts.transparent_url
                    : `${this.apiUrl}${artifacts.transparent_url}`)
                : null;

            if (contourUrl) {
                this.addMessage('assistant',
                    `分割结果：<br><img src="${contourUrl}" alt="contour" class="max-w-full rounded border">`);
            } else if (transparentUrl) {
                this.addMessage('assistant',
                    `分割结果：<br><img src="${transparentUrl}" alt="transparent" class="max-w-full rounded border">`);
            }
        } catch (error) {
            console.error('Error getting final response:', error);
            this.addMessage('assistant', '无法获取最终响应，请查看日志了解详情。', false, 'error');
        }
    }

    addMessage(role, content, isLoading = false, type = 'normal') {
        const messagesContainer = document.getElementById('chat-messages');
        const messageId = `msg-${Date.now()}-${Math.random()}`;

        const messageDiv = document.createElement('div');
        messageDiv.id = messageId;
        messageDiv.className = 'message';

        let bgColor = 'bg-gray-50 border-gray-200';
        let textColor = 'text-gray-800';
        let icon = 'fas fa-user';

        if (role === 'assistant') {
            bgColor = type === 'error' ? 'bg-red-50 border-red-200' : 'bg-blue-50 border-blue-200';
            textColor = type === 'error' ? 'text-red-800' : 'text-blue-800';
            icon = 'fas fa-robot';
        }

        messageDiv.innerHTML = `
            <div class="${bgColor} border rounded-lg p-4">
                <div class="flex items-center mb-2">
                    <i class="${icon} ${role === 'assistant' ? (type === 'error' ? 'text-red-600' : 'text-blue-600') : 'text-gray-600'} mr-2"></i>
                    <span class="font-medium ${textColor}">${role === 'assistant' ? '基于智能体互联网的病理诊断助手' : '您'}</span>
                    ${isLoading ? '<i class="fas fa-spinner fa-spin ml-2"></i>' : ''}
                </div>
                <div class="text-gray-700">${this.formatMessage(content)}</div>
            </div>
        `;

        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
        this.messageCount++;
        return messageId;
    }

    removeMessage(messageId) {
        const message = document.getElementById(messageId);
        if (message) {
            message.remove();
            this.messageCount--;
        }
    }

    formatMessage(message) {
        return message
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
    }

    getAgentName(agentId) {
        const agentNames = {
            'brca-pre-agent-001': '乳腺组织切片预测智能体',
            'coad-pre-agent-001': '结肠组织切片预测智能体',
            'seg-agent-001': '癌症组织切片分割智能体',
            'alnpre-agent-001': '癌症组织切片淋巴结转移预测智能体'
        };
        return agentNames[agentId] || agentId;
    }

    startNewSession() {
        console.log('startNewSession called');

        // ★ 停止所有轮询
        this.stopPolling();
        this.lastLogTimestamp = null;
        this.currentLoadingId = null;
        this.sessionCompleted = false;
        this.sending = false;

        const messagesContainer = document.getElementById('chat-messages');
        if (!messagesContainer) return;

        messagesContainer.innerHTML = `
            <div class="message bg-blue-50 border border-blue-200 rounded-lg p-4">
                <div class="flex items-center mb-2">
                    <i class="fas fa-robot text-blue-600 mr-2"></i>
                    <span class="font-medium text-blue-800">基于智能体互联网的病理诊断助手</span>
                </div>
                <p class="text-gray-700">您好！我是基于智能体互联网的病理诊断助手。请描述您的分析需求并上传切片图片，例如："帮我分析这张乳腺切片"。我会协调多智能体进行图像与临床语义联合分析，返回可读的结论与建议。点击"新会话"可开始新的分析流程。</p>
            </div>
        `;

        this.sessionId = 'session-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
        this.messageCount = 1;

        this.protocolLogs = [];
        this.renderProtocolLog();

        this.addProtocolLog('new_session_start', {
            session_id: this.sessionId,
            timestamp: new Date().toISOString()
        });

        const newSessionButton = document.getElementById('new-session-button');
        if (newSessionButton) {
            const originalText = newSessionButton.innerHTML;
            newSessionButton.innerHTML = '<i class="fas fa-check mr-2"></i>新会话已开始';
            newSessionButton.disabled = true;
            setTimeout(() => {
                newSessionButton.innerHTML = originalText;
                newSessionButton.disabled = false;
            }, 2000);
        }
    }

    // ========================================================================
    // 交互日志
    // ========================================================================
    processInteractionLogs(logs) {
        logs.forEach(log => this.addDetailedProtocolLog(log));
    }

    addDetailedProtocolLog(logData) {
        this.protocolLogs.push({
            id: logData.id || `log-${Date.now()}-${Math.random()}`,
            type: 'detailed_interaction',
            data: logData,
            timestamp: logData.timestamp || new Date().toISOString()
        });
        this.renderProtocolLog();
    }

    addProtocolLog(type, data) {
        this.protocolLogs.push({
            id: `log-${Date.now()}-${Math.random()}`,
            type: type,
            data: data,
            timestamp: new Date().toISOString()
        });
        this.renderProtocolLog();
    }

    renderProtocolLog() {
        const logContainer = document.getElementById('protocol-log');
        if (!logContainer) return;

        if (this.protocolLogs.length === 0) {
            logContainer.innerHTML = `
                <div class="text-gray-500 text-center py-4">
                    <i class="fas fa-info-circle mr-2"></i>
                    暂无交互记录
                </div>
            `;
            return;
        }

        let html = '';
        this.protocolLogs.forEach((log, index) => {
            html += this.formatProtocolLogEntry(log, index);
        });
        logContainer.innerHTML = html;

        const logContainer2 = document.getElementById('protocol-log-container');
        if (logContainer2) logContainer2.scrollTop = logContainer2.scrollHeight;
    }

    formatProtocolLogEntry(log, index) {
        const time = new Date(log.timestamp).toLocaleTimeString();

        if (log.type === 'detailed_interaction') {
            return this.formatDetailedInteractionLog(log, time);
        }

        let bgColor = 'bg-gray-50';
        let borderColor = 'border-gray-200';
        let icon = 'fas fa-info-circle text-gray-600';
        let content = '';

        switch (log.type) {
            case 'user_request':
                bgColor = 'bg-blue-50'; borderColor = 'border-blue-200';
                icon = 'fas fa-user text-blue-600';
                content = `
                    <div class="font-semibold text-blue-800">用户请求</div>
                    <div class="text-sm text-blue-700 mt-1">${log.data.message}</div>
                `;
                break;
            case 'coordination_complete':
                bgColor = 'bg-green-50'; borderColor = 'border-green-200';
                icon = 'fas fa-check text-green-600';
                content = `
                    <div class="font-semibold text-green-800">协调完成</div>
                    <div class="text-sm text-green-700 mt-1">状态: ${log.data.success ? '成功' : '失败'}</div>
                `;
                break;
            case 'coordination_error':
                bgColor = 'bg-red-50'; borderColor = 'border-red-200';
                icon = 'fas fa-exclamation-triangle text-red-600';
                content = `
                    <div class="font-semibold text-red-800">协调错误</div>
                    <div class="text-sm text-red-700 mt-1">错误: ${log.data.error}</div>
                `;
                break;
            case 'new_session_start':
                bgColor = 'bg-cyan-50'; borderColor = 'border-cyan-200';
                icon = 'fas fa-refresh text-cyan-600';
                content = `
                    <div class="font-semibold text-cyan-800">新会话开始</div>
                    <div class="text-sm text-cyan-700 mt-1">会话ID: ${log.data.session_id}</div>
                `;
                break;
            default:
                content = `
                    <div class="font-semibold text-gray-800">${log.type}</div>
                    <div class="text-sm text-gray-700 mt-1">${JSON.stringify(log.data)}</div>
                `;
        }

        return `
            <div class="protocol-log-entry ${bgColor} ${borderColor} border rounded-lg p-3">
                <div class="flex items-start justify-between">
                    <div class="flex items-center">
                        <i class="${icon} mr-2"></i>
                        <div class="flex-1">${content}</div>
                    </div>
                    <div class="text-xs text-gray-500 ml-4">${time}</div>
                </div>
            </div>
        `;
    }

    formatDetailedInteractionLog(log, time) {
        const step = log.data.step;
        let bgColor = 'bg-gray-50';
        let borderColor = 'border-gray-200';
        let icon = 'fas fa-cog text-gray-600';
        let content = '';

        switch (step) {
            case 'request_started':
                bgColor = 'bg-cyan-50'; borderColor = 'border-cyan-200';
                icon = 'fas fa-play text-cyan-600';
                content = `<div class="font-semibold text-cyan-800">开始处理请求</div>
                    <div class="text-sm text-cyan-700 mt-1"><div>用户请求: ${log.data.user_request}</div></div>`;
                break;
            case 'request_completed':
                bgColor = 'bg-green-50'; borderColor = 'border-green-200';
                icon = 'fas fa-check-circle text-green-600';
                content = `<div class="font-semibold text-green-800">请求处理完成</div>
                    <div class="text-sm text-green-700 mt-1"><div>最终响应: ${log.data.final_response}</div></div>`;
                break;
            case 'request_error':
                bgColor = 'bg-red-50'; borderColor = 'border-red-200';
                icon = 'fas fa-exclamation-circle text-red-600';
                content = `<div class="font-semibold text-red-800">请求处理错误</div>
                    <div class="text-sm text-red-700 mt-1"><div>错误: ${log.data.error}</div></div>`;
                break;
            case 'agent_request':
                bgColor = 'bg-blue-50'; borderColor = 'border-blue-200';
                icon = 'fas fa-paper-plane text-blue-600';
                content = `<div class="font-semibold text-blue-800">向智能体发送请求</div>
                    <div class="text-sm text-blue-700 mt-1">
                        <div>智能体: ${log.data.agent_name} (${log.data.agent_id})</div>
                        <div class="mt-1">请求内容: ${log.data.request_content}</div>
                    </div>`;
                break;
            case 'agent_response_success':
                bgColor = 'bg-green-50'; borderColor = 'border-green-200';
                icon = 'fas fa-check text-green-600';
                content = `<div class="font-semibold text-green-800">智能体响应成功</div>
                    <div class="text-sm text-green-700 mt-1">
                        <div>智能体: ${log.data.agent_name} (${log.data.agent_id})</div>
                        <div class="mt-1">响应内容: ${log.data.response_content}</div>
                    </div>`;
                break;
            case 'agent_response_error':
                bgColor = 'bg-red-50'; borderColor = 'border-red-200';
                icon = 'fas fa-times text-red-600';
                content = `<div class="font-semibold text-red-800">智能体响应错误</div>
                    <div class="text-sm text-red-700 mt-1">
                        <div>智能体: ${log.data.agent_name} (${log.data.agent_id})</div>
                        <div class="mt-1">错误: ${log.data.error}</div>
                    </div>`;
                break;
            case 'integration_complete':
                bgColor = 'bg-indigo-50'; borderColor = 'border-indigo-200';
                icon = 'fas fa-check-double text-indigo-600';
                content = `<div class="font-semibold text-indigo-800">结果整合完成</div>
                    <div class="text-sm text-indigo-700 mt-1"><div>最终响应: ${log.data.final_response}</div></div>`;
                break;
            default:
                content = `<div class="font-semibold text-gray-800">${step}</div>
                    <div class="text-sm text-gray-700 mt-1">${JSON.stringify(log.data, null, 2)}</div>`;
        }

        return `
            <div class="protocol-log-entry ${bgColor} ${borderColor} border rounded-lg p-3">
                <div class="flex items-start justify-between">
                    <div class="flex items-center">
                        <i class="${icon} mr-2"></i>
                        <div class="flex-1">${content}</div>
                    </div>
                    <div class="text-xs text-gray-500 ml-4">${time}</div>
                </div>
            </div>
        `;
    }

    clearProtocolLog() {
        this.protocolLogs = [];
        this.renderProtocolLog();
    }

    logProtocol(data) {
        this.addProtocolLog('agent_coordination', {
            session_id: this.sessionId,
            agents_contacted: Object.keys(data.agent_results || {}),
            coordination_results: data.agent_results,
            timestamp: new Date().toISOString()
        });
    }

    async loadSessionInfo() {
        if (!this.sessionId) return;
        try {
            const response = await axios.get(`${this.apiUrl}/session/${this.sessionId}`);
            this.addProtocolLog('session_info', {
                session_id: this.sessionId,
                message_count: response.data.message_count,
                context: response.data.context,
                timestamp: new Date().toISOString()
            });
        } catch (error) {
            console.log('获取会话信息失败:', error.message);
        }
    }
}

// 等待 DOM 加载完成后初始化客户端
document.addEventListener('DOMContentLoaded', async function () {
    console.log('DOM loaded, initializing client...');
    const client = new PathologyAssistantClient();
    await client.init();
});