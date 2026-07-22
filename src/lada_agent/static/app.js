let socket = null;
        let currentConfig = { monitor_directories: [], lada_options: {} };
        let currentLogType = 'lada';
        let isDirty = false;
        let isSaving = false;
        let actionInFlight = null;
        let autoRefresh = true;
        let suppressDirty = false;
        let processingProgress = {};
        let processedFiles = [];
        let selectedProcessedHash = null;
        let selectedHistoryHashes = new Set();
        let comparisonMode = 'side';
        let resetComparisonPlayback = () => {};

        function switchPage(page) {
            const target = document.querySelector(`[data-page-panel="${page}"]`);
            if (!target) return;
            document.querySelectorAll('[data-page-panel]').forEach(el => el.classList.toggle('active', el === target));
            document.querySelectorAll('[data-page]').forEach(el => el.classList.toggle('active', el.dataset.page === page));
            history.replaceState(null, '', `#${page}`);
            if (page === 'library') loadProcessed();
            if (page === 'logs') loadLogs();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function formatBytes(bytes, decimals = 1) {
            const value = Number(bytes) || 0;
            if (!value) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
            return `${(value / Math.pow(1024, index)).toFixed(index ? decimals : 0)} ${units[index]}`;
        }

        function formatDuration(seconds) {
            if (seconds == null || !Number.isFinite(Number(seconds))) return 'Calculating…';
            const total = Math.max(0, Math.round(Number(seconds)));
            if (total < 60) return `${total}s`;
            const hours = Math.floor(total / 3600);
            const minutes = Math.floor((total % 3600) / 60);
            return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
        }

        function renderMetrics(metrics = {}) {
            const pipelineTotal = Number(metrics.total_bytes) || 0;
            const remaining = Number(metrics.remaining_bytes) || 0;
            const completed = pipelineTotal > 0 ? Math.max(0, Math.min(100, (1 - remaining / pipelineTotal) * 100)) : 0;
            const hasEta = metrics.eta_seconds != null && Number.isFinite(Number(metrics.eta_seconds));
            const noWork = remaining <= 0 && pipelineTotal <= 0;
            const totalEtaEl = document.getElementById('totalEta');
            const etaHintEl = document.getElementById('etaHint');
            if (noWork) {
                totalEtaEl.textContent = '—';
                etaHintEl.textContent = 'No work in queue';
            } else if (hasEta) {
                totalEtaEl.textContent = formatDuration(metrics.eta_seconds);
                etaHintEl.textContent = `Based on ${formatBytes(metrics.throughput_bytes_per_second)}/s observed throughput`;
            } else {
                totalEtaEl.textContent = 'Calculating…';
                etaHintEl.textContent = 'ETA appears after measurable progress';
            }
            document.getElementById('totalVideoSize').textContent = formatBytes(metrics.total_video_bytes);
            document.getElementById('processingBytes').textContent = formatBytes(metrics.processing_bytes);
            document.getElementById('queuedBytes').textContent = `${formatBytes(metrics.queued_bytes)} waiting`;
            document.getElementById('processingSize').textContent = `${formatBytes(metrics.processing_bytes)} active`;
            document.getElementById('waitingSize').textContent = `${formatBytes(metrics.queued_bytes)} queued`;
            document.getElementById('historySize').textContent = `${formatBytes(metrics.history_bytes)} recorded`;
            document.getElementById('videoLibrarySummary').textContent = `${Number(metrics.history_count) || 0} history · ${Number(metrics.ready_count) || 0} ready · ${Number(metrics.missing_count) || 0} missing`;
            document.getElementById('throughput').textContent = metrics.throughput_bytes_per_second
                ? `${formatBytes(metrics.throughput_bytes_per_second)}/s` : '—';
            document.getElementById('overallPercent').textContent = `${Math.round(completed)}%`;
            document.getElementById('overallRing').style.setProperty('--progress', `${completed * 3.6}deg`);
        }

        function createIcon(className) {
            const icon = document.createElement('i');
            icon.className = className;
            return icon;
        }

        function createEmptyState(text, padding = '20px') {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.style.textAlign = 'center';
            empty.style.color = 'var(--text-muted)';
            empty.style.padding = padding;
            empty.textContent = text;
            return empty;
        }

        function createListHeader(label, count) {
            const header = document.createElement('div');
            header.className = 'list-section';

            const labelEl = document.createElement('span');
            labelEl.textContent = label;
            const countEl = document.createElement('span');
            countEl.textContent = String(count);

            header.append(labelEl, countEl);
            return header;
        }

        function createPathText(text, maxWidth) {
            const span = document.createElement('span');
            span.className = 'path-text';
            span.title = text;
            span.textContent = text;
            if (maxWidth) span.style.maxWidth = maxWidth;
            return span;
        }

        function createBadge(className, text) {
            const badge = document.createElement('span');
            badge.className = `badge ${className}`;
            badge.textContent = text;
            return badge;
        }

        function createIconButton(iconClass, title, onClick, danger = false) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = danger ? 'icon-btn danger' : 'icon-btn';
            button.title = title;
            button.setAttribute('aria-label', title);
            button.appendChild(createIcon(iconClass));
            button.addEventListener('click', onClick);
            return button;
        }

        function initSocket() {
            if (typeof io === 'undefined') {
                setConnectionState(false);
                showNotification('Socket.IO client failed to load', 'error');
                return;
            }

            socket = io();
            socket.on('connect', () => setConnectionState(true));
            socket.on('disconnect', () => {
                setConnectionState(false);
                showNotification('Connection lost. Retrying...', 'error');
            });
            socket.on('connect_error', () => {
                setConnectionState(false);
                showNotification('WebSocket connection failed. Check Socket.IO version.', 'error');
            });

            socket.on('status_update', data => updateStatus(data, true));
            socket.on('queue_updated', updateQueue);

            socket.on('processing_started', data => {
                showNotification(`Started: ${data.file}`, 'success');
                if (data?.file) {
                    processingProgress[data.file] = 0;
                    updateProgressRow(data.file, 0);
                }
                refreshStatus();
            });

            socket.on('processing_completed', data => {
                showNotification(`Completed: ${data.file}`, 'success');
                refreshStatus();
            });

            socket.on('processing_failed', data => {
                showNotification(`Failed: ${data.file || 'task'}`, 'error');
                refreshStatus();
                loadQueue(true);
            });

            socket.on('processing_progress', data => {
                if (!data?.file) return;
                processingProgress[data.file] = data.percent ?? 0;
                updateProgressRow(data.file, data.percent ?? 0);
                loadQueue(true);
            });
        }

        function setConnectionState(online) {
            const chip = document.getElementById('connectionStatus');
            const text = document.getElementById('connectionText');
            chip.classList.toggle('online', online);
            chip.classList.toggle('offline', !online);
            text.textContent = online ? 'Online' : 'Offline';
        }

        function setLastUpdate() {
            const lastUpdateEl = document.getElementById('lastUpdate');
            const now = new Date();
            lastUpdateEl.textContent = `Last update: ${now.toLocaleTimeString()}`;
        }

        function withSuppressDirty(fn) {
            suppressDirty = true;
            try {
                fn();
            } finally {
                suppressDirty = false;
            }
        }

        function markDirty() {
            if (suppressDirty) return;
            setDirty(true);
        }

        function setDirty(value) {
            isDirty = value;
            document.querySelectorAll('[data-unsaved-indicator]').forEach(el => {
                el.style.display = isDirty ? 'inline-flex' : 'none';
            });
            document.querySelectorAll('.js-save').forEach(btn => {
                if (!btn.dataset.label) btn.dataset.label = btn.innerHTML;
                btn.disabled = !isDirty || isSaving;
                btn.classList.toggle('btn-warning', isDirty);
                btn.classList.toggle('btn-primary', !isDirty);
            });
            document.querySelectorAll('.js-reset').forEach(btn => {
                btn.disabled = !isDirty || isSaving;
            });
        }

        function setSaveButtonsLoading(loading) {
            isSaving = loading;
            document.querySelectorAll('.js-save').forEach(btn => {
                if (!btn.dataset.label) btn.dataset.label = btn.innerHTML;
                btn.disabled = loading || !isDirty;
                btn.innerHTML = loading
                    ? '<span class="spinner" style="width:14px;height:14px;"></span> Saving'
                    : btn.dataset.label;
            });
            document.querySelectorAll('.js-reset').forEach(btn => {
                btn.disabled = loading || !isDirty;
            });
        }

        function bindInputHandlers() {
            document.querySelectorAll('.input').forEach(el => {
                el.addEventListener('input', markDirty);
            });
            document.querySelectorAll('input[type="checkbox"]').forEach(el => {
                el.addEventListener('change', markDirty);
            });
        }

        function setActionLoading(action, loading) {
            actionInFlight = loading ? action : null;
            const startBtn = document.getElementById('startBtn');
            const stopBtn = document.getElementById('stopBtn');
            const forceStopBtn = document.getElementById('forceStopBtn');

            if (!startBtn.dataset.label) startBtn.dataset.label = startBtn.innerHTML;
            if (!stopBtn.dataset.label) stopBtn.dataset.label = stopBtn.innerHTML;
            if (!forceStopBtn.dataset.label) forceStopBtn.dataset.label = forceStopBtn.innerHTML;

            if (loading) {
                startBtn.disabled = true;
                stopBtn.disabled = true;
                forceStopBtn.disabled = true;
                if (action === 'start') {
                    startBtn.innerHTML = '<span class="spinner" style="width:14px;height:14px;"></span> Starting';
                } else {
                    stopBtn.innerHTML = '<span class="spinner" style="width:14px;height:14px;"></span> Stopping';
                }
                return;
            }

            startBtn.innerHTML = startBtn.dataset.label;
            stopBtn.innerHTML = stopBtn.dataset.label;
            forceStopBtn.innerHTML = forceStopBtn.dataset.label;
            const isRunning = document.getElementById('statusValue').textContent === 'Running';
            startBtn.disabled = isRunning;
            stopBtn.disabled = !isRunning;
            forceStopBtn.disabled = !isRunning;
        }

        function shouldAutoRefresh() {
            return autoRefresh && !document.hidden;
        }

        // Core Functions
        function refreshStatus(isAuto = false) {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => updateStatus(data, isAuto))
                .catch(() => {
                    if (!isAuto) showNotification('Failed to fetch status', 'error');
                });
        }

        function updateStatus(data, isAuto = false) {
            const statusEl = document.getElementById('statusValue');
            const indicatorEl = document.getElementById('statusIndicator');
            const startBtn = document.getElementById('startBtn');
            const stopBtn = document.getElementById('stopBtn');
            const forceStopBtn = document.getElementById('forceStopBtn');

            if (data.is_running) {
                statusEl.textContent = 'Running';
                statusEl.style.color = 'var(--success)';
                indicatorEl.className = 'stat-indicator running-indicator';
                if (!actionInFlight) {
                    startBtn.disabled = true;
                    stopBtn.disabled = false;
                    forceStopBtn.disabled = false;
                }
            } else {
                statusEl.textContent = 'Stopped';
                statusEl.style.color = 'var(--text-main)';
                indicatorEl.className = 'stat-indicator stopped-indicator';
                if (!actionInFlight) {
                    startBtn.disabled = false;
                    stopBtn.disabled = true;
                    forceStopBtn.disabled = true;
                }
            }

            document.getElementById('queueSize').textContent = data.queue_size;
            document.getElementById('processingCount').textContent = data.processing_files.length;
            document.getElementById('processedCount').textContent = data.processed_count;
            renderMetrics(data.metrics || {});

            setLastUpdate();

            // Trigger queue update as well
            loadQueue(isAuto);
        }

        function loadQueue(isAuto = false) {
            fetch('/api/queue')
                .then(r => r.json())
                .then(updateQueue)
                .catch(() => {
                    if (!isAuto) showNotification('Failed to fetch queue', 'error');
                });
        }

        function updateQueue(data) {
            const listEl = document.getElementById('activeList');
            listEl.innerHTML = '';

            let hasItems = false;
            const processingList = data.processing ?? data.processing_files ?? [];
            const queueList = data.queue ?? [];
            if (data.processing_progress) {
                processingProgress = { ...processingProgress, ...data.processing_progress };
            }
            const progressLookup = processingProgress || {};

            // Processing items
            if (processingList.length) {
                hasItems = true;
                listEl.appendChild(createListHeader('Processing', processingList.length));
                processingList.forEach(item => {
                    const file = typeof item === 'string' ? item : item.name;
                    const percent = Math.max(0, Math.min(100, typeof item === 'string' ? (progressLookup[file] ?? 0) : (item.percent ?? 0)));
                    const fileKey = encodeURIComponent(file);
                    const el = document.createElement('div');
                    el.className = 'list-item processing-item';
                    el.dataset.file = fileKey;

                    const body = document.createElement('div');
                    body.className = 'task-body';

                    const row = document.createElement('div');
                    row.className = 'task-row';
                    const name = createPathText(file);
                    name.classList.add('task-name');
                    row.append(name, createBadge('badge-processing', `${percent}%`));

                    const track = document.createElement('div');
                    track.className = 'progress-track';
                    const fill = document.createElement('div');
                    fill.className = 'progress-fill';
                    fill.style.width = `${percent}%`;
                    track.appendChild(fill);

                    body.append(row, track);
                    if (typeof item !== 'string') {
                        const meta = document.createElement('div');
                        meta.className = 'task-meta';
                        meta.innerHTML = `<span>${formatBytes(item.size)}</span><span>Elapsed ${formatDuration(item.elapsed_seconds)}</span><span>ETA ${formatDuration(item.eta_seconds)}</span>`;
                        body.appendChild(meta);
                    }
                    el.append(body);
                    listEl.appendChild(el);
                });
            }

            // Queue items
            if (queueList.length) {
                hasItems = true;
                listEl.appendChild(createListHeader('Queue', queueList.length));
                queueList.forEach((item, idx) => {
                    const file = typeof item === 'string' ? item : item.name;
                    const el = document.createElement('div');
                    el.className = 'list-item';
                    const queueMeta = typeof item === 'string' ? `#${idx + 1}` : `${formatBytes(item.size)} · #${idx + 1}`;
                    el.append(createPathText(file), createBadge('badge-queue', queueMeta));
                    listEl.appendChild(el);
                });
            }

            if (!hasItems) {
                listEl.appendChild(createEmptyState('No active tasks'));
            }

            if (typeof data.queue_size !== 'undefined') {
                document.getElementById('queueSize').textContent = data.queue_size;
            } else {
                document.getElementById('queueSize').textContent = queueList.length;
            }
            document.getElementById('processingCount').textContent = processingList.length;
            renderMetrics(data);

            const activeSet = new Set(processingList.map(item => typeof item === 'string' ? item : item.name));
            Object.keys(processingProgress).forEach(file => {
                if (!activeSet.has(file)) delete processingProgress[file];
            });
        }

        function updateProgressRow(file, percent) {
            const key = encodeURIComponent(file);
            const row = document.querySelector(`[data-file="${key}"]`);
            if (!row) return;
            const fill = row.querySelector('.progress-fill');
            if (fill) fill.style.width = `${percent}%`;
            const badge = row.querySelector('.badge-processing');
            if (badge) badge.textContent = `${percent}%`;
        }

        // Config Functions
        function loadConfig() {
            fetch('/api/config')
                .then(r => r.json())
                .then(data => {
                    currentConfig = data;
                    if (!currentConfig.lada_options) currentConfig.lada_options = {};
                    withSuppressDirty(() => {
                        renderDirs();
                        renderSettings();
                        setDirty(false);
                    });
                })
                .catch(() => showNotification('Failed to load config', 'error'));
        }

        function renderSettings() {
            const opts = currentConfig.lada_options || {};

            document.getElementById('scanInterval').value = currentConfig.scan_interval ?? 10;
            document.getElementById('maxConcurrent').value = currentConfig.max_concurrent_processes ?? 1;
            document.getElementById('ladaCliPath').value = currentConfig.lada_cli_path ?? 'lada-cli.exe';
            document.getElementById('autoStart').checked = Boolean(currentConfig.auto_start);

            document.getElementById('device').value = opts.device ?? 'cuda:0';
            document.getElementById('restorationModel').value = opts.mosaic_restoration_model ?? 'basicvsrpp-v1.2';
            document.getElementById('maxClipLength').value = opts.max_clip_length ?? 180;
            document.getElementById('fp16').checked = opts.fp16 !== false;

            document.getElementById('detectionModel').value = opts.mosaic_detection_model ?? 'v4-fast';
            document.getElementById('detectFaceMosaics').checked = Boolean(opts.detect_face_mosaics);

            document.getElementById('encodingPreset').value = opts.encoding_preset ?? 'hevc-nvidia-gpu-hq';
            document.getElementById('encoder').value = opts.encoder ?? '';
            document.getElementById('encoderOptions').value = opts.encoder_options ?? '';
            document.getElementById('mp4FastStart').checked = Boolean(opts.mp4_fast_start);

            document.getElementById('temporaryDirectory').value = opts.temporary_directory ?? '';
            document.getElementById('outputPattern').value = opts.output_file_pattern ?? '{orig_file_name}.restored.mp4';
            document.getElementById('restorationConfigPath').value = opts.mosaic_restoration_config_path ?? '';

            const uptime = currentConfig.uptime_kuma || {};
            document.getElementById('uptimeEnabled').checked = Boolean(uptime.enabled);
            document.getElementById('uptimePushUrl').value = uptime.push_url ?? '';
            document.getElementById('uptimeInterval').value = uptime.interval_seconds ?? 60;
            document.getElementById('uptimeStatus').value = uptime.status ?? 'up';
            document.getElementById('uptimeMessage').value = uptime.message ?? 'LADA Agent online';
        }

        function collectSettings() {
            currentConfig.scan_interval = Math.max(1, parseInt(document.getElementById('scanInterval').value, 10) || 10);
            currentConfig.max_concurrent_processes = Math.max(1, parseInt(document.getElementById('maxConcurrent').value, 10) || 1);
            currentConfig.lada_cli_path = document.getElementById('ladaCliPath').value.trim() || 'lada-cli.exe';
            currentConfig.auto_start = document.getElementById('autoStart').checked;

            if (!currentConfig.lada_options) currentConfig.lada_options = {};
            currentConfig.lada_options.device = document.getElementById('device').value.trim() || 'cuda:0';
            currentConfig.lada_options.mosaic_restoration_model = document.getElementById('restorationModel').value.trim();
            currentConfig.lada_options.max_clip_length = Math.max(1, parseInt(document.getElementById('maxClipLength').value, 10) || 180);
            currentConfig.lada_options.fp16 = document.getElementById('fp16').checked;

            currentConfig.lada_options.mosaic_detection_model = document.getElementById('detectionModel').value.trim();
            currentConfig.lada_options.detect_face_mosaics = document.getElementById('detectFaceMosaics').checked;

            currentConfig.lada_options.encoding_preset = document.getElementById('encodingPreset').value.trim();
            currentConfig.lada_options.encoder = document.getElementById('encoder').value.trim();
            currentConfig.lada_options.encoder_options = document.getElementById('encoderOptions').value.trim();
            currentConfig.lada_options.mp4_fast_start = document.getElementById('mp4FastStart').checked;

            currentConfig.lada_options.temporary_directory = document.getElementById('temporaryDirectory').value.trim();
            currentConfig.lada_options.output_file_pattern = document.getElementById('outputPattern').value.trim() || '{orig_file_name}.restored.mp4';
            currentConfig.lada_options.mosaic_restoration_config_path = document.getElementById('restorationConfigPath').value.trim();

            if (!currentConfig.uptime_kuma) currentConfig.uptime_kuma = {};
            currentConfig.uptime_kuma.enabled = document.getElementById('uptimeEnabled').checked;
            currentConfig.uptime_kuma.push_url = document.getElementById('uptimePushUrl').value.trim();
            currentConfig.uptime_kuma.interval_seconds = Math.max(1, parseInt(document.getElementById('uptimeInterval').value, 10) || 60);
            currentConfig.uptime_kuma.status = document.getElementById('uptimeStatus').value.trim() || 'up';
            currentConfig.uptime_kuma.message = document.getElementById('uptimeMessage').value.trim() || 'LADA Agent online';
        }

        function renderDirs() {
            const listEl = document.getElementById('monitorDirsList');
            listEl.innerHTML = '';

            if (!currentConfig.monitor_directories?.length) {
                const empty = createEmptyState('No directories configured', '12px');
                empty.style.fontSize = '0.9rem';
                listEl.appendChild(empty);
                return;
            }

            currentConfig.monitor_directories.forEach((dir, idx) => {
                const el = document.createElement('div');
                el.className = 'list-item';

                const folderInfo = document.createElement('div');
                folderInfo.style.display = 'flex';
                folderInfo.style.alignItems = 'center';
                folderInfo.style.gap = '8px';
                const folderIcon = createIcon('ri-folder-3-line');
                folderIcon.style.color = 'var(--primary)';
                folderInfo.append(folderIcon, createPathText(dir, '140px'));

                const actions = document.createElement('div');
                actions.className = 'dir-actions';
                actions.append(
                    createIconButton('ri-folder-open-line', 'Open folder', () => openPathRaw(dir)),
                    createIconButton('ri-folder-open-fill', 'Open Restored', () => openPathRaw(getRestoredPath(dir), true)),
                    createIconButton('ri-delete-bin-line', 'Remove', () => removeFolder(idx), true)
                );

                el.append(folderInfo, actions);
                listEl.appendChild(el);
            });
        }

        function addFolder() {
            fetch('/api/select_folder', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.success && !currentConfig.monitor_directories.includes(data.path)) {
                        currentConfig.monitor_directories.push(data.path);
                        renderDirs();
                        setDirty(true);
                        showNotification('Folder added. Remember to save!', 'success');
                    } else if (data.success && currentConfig.monitor_directories.includes(data.path)) {
                        showNotification('Folder already added', 'error');
                    } else if (data.message && data.message !== '用户取消选择') {
                        showNotification(data.message, 'error');
                    }
                });
        }

        function removeFolder(idx) {
            currentConfig.monitor_directories.splice(idx, 1);
            renderDirs();
            setDirty(true);
            showNotification('Folder removed. Remember to save!', 'success');
        }

        function saveConfig() {
            if (isSaving) return;
            collectSettings();

            setSaveButtonsLoading(true);
            fetch('/api/config/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentConfig)
            })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        setDirty(false);
                        showNotification(data.message || 'Configuration saved successfully', 'success');
                    } else {
                        showNotification(data.message || 'Save failed', 'error');
                    }
                    refreshStatus();
                })
                .catch(() => showNotification('Save failed', 'error'))
                .finally(() => setSaveButtonsLoading(false));
        }

        function resetConfig() {
            if (isSaving) return;
            if (isDirty && !confirm('Discard unsaved changes?')) {
                return;
            }
            loadConfig();
            showNotification('Changes discarded', 'success');
        }

        function getRestoredPath(dir) {
            const trimmed = dir.replace(/[\\/]+$/, '');
            const sep = trimmed.includes('\\') ? '\\' : '/';
            return `${trimmed}${sep}Restored`;
        }

        function openPathRaw(path, create = false) {
            return fetch('/api/open_path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path, create })
            })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        showNotification('Opened: ' + data.path, 'success');
                        return true;
                    }
                    showNotification(data.message || 'Unable to open path', 'error');
                    return false;
                })
                .catch(() => {
                    showNotification('Unable to open path', 'error');
                    return false;
                });
        }

        function openPath(encoded) {
            const path = decodeURIComponent(encoded);
            return openPathRaw(path);
        }

        function openRestoredFor(encoded) {
            const dir = decodeURIComponent(encoded);
            const restored = getRestoredPath(dir);
            return openPathRaw(restored, true);
        }

        // Video review
        function loadProcessed() {
            const list = document.getElementById('processedList');
            if (!list) return;
            fetch('/api/processed')
                .then(r => r.json())
                .then(data => {
                    processedFiles = Array.isArray(data.files) ? data.files : [];
                    const validHashes = new Set(processedFiles.map(file => file.hash));
                    selectedHistoryHashes = new Set([...selectedHistoryHashes].filter(hash => validHashes.has(hash)));
                    renderProcessedList();
                    if (selectedProcessedHash) {
                        const selected = processedFiles.find(file => file.hash === selectedProcessedHash);
                        if (selected) selectProcessed(selected.hash);
                        else resetViewer();
                    }
                })
                .catch(() => {
                    list.replaceChildren(createEmptyState('Unable to load completed videos'));
                    showNotification('Failed to load video library', 'error');
                });
        }

        function renderProcessedList() {
            const list = document.getElementById('processedList');
            if (!list) return;
            const query = (document.getElementById('videoSearch')?.value || '').trim().toLowerCase();
            const visible = processedFiles.filter(file => (file.name || file.original_path || '').toLowerCase().includes(query));
            list.innerHTML = '';
            if (!visible.length) {
                list.appendChild(createEmptyState(query ? 'No matching videos' : 'No completed videos yet'));
                updateHistoryActions(visible);
                return;
            }
            visible.forEach(file => {
                const hasOriginal = Boolean(file.original_available);
                const hasRestored = Boolean(file.output_available);
                const availability = hasOriginal && hasRestored
                    ? { key: 'ready', label: 'Ready' }
                    : hasOriginal || hasRestored
                        ? { key: 'partial', label: hasOriginal ? 'Original only' : 'Restored only' }
                        : { key: 'missing', label: 'Missing' };
                const row = document.createElement('div');
                row.className = 'processed-row';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'history-select';
                checkbox.checked = selectedHistoryHashes.has(file.hash);
                checkbox.setAttribute('aria-label', `Select ${file.name || 'video'} history record`);
                checkbox.addEventListener('change', () => {
                    if (checkbox.checked) selectedHistoryHashes.add(file.hash);
                    else selectedHistoryHashes.delete(file.hash);
                    updateHistoryActions(visible);
                });
                const button = document.createElement('button');
                button.type = 'button';
                button.className = `processed-item status-${availability.key}${file.hash === selectedProcessedHash ? ' active' : ''}`;
                button.dataset.hash = file.hash;
                const heading = document.createElement('span');
                heading.className = 'processed-item-heading';
                const title = document.createElement('strong');
                title.textContent = file.name || 'Untitled video';
                const status = document.createElement('span');
                status.className = `processed-status processed-status-${availability.key}`;
                status.textContent = availability.label;
                const meta = document.createElement('span');
                meta.className = 'processed-meta';
                const date = file.processed_time ? new Date(file.processed_time).toLocaleString() : 'Unknown date';
                meta.textContent = `${formatBytes(file.file_size)} · ${date}`;
                heading.append(title, status);
                button.append(heading, meta);
                button.setAttribute('aria-label', `${title.textContent}, ${availability.label}`);
                button.addEventListener('click', () => selectProcessed(file.hash));
                row.append(checkbox, button);
                list.appendChild(row);
            });
            updateHistoryActions(visible);
        }

        function updateHistoryActions(visibleFiles = processedFiles) {
            const selectedCount = selectedHistoryHashes.size;
            const visibleHashes = visibleFiles.map(file => file.hash);
            const allVisibleSelected = visibleHashes.length > 0 && visibleHashes.every(hash => selectedHistoryHashes.has(hash));
            const missingCount = processedFiles.filter(file => !file.original_available && !file.output_available).length;
            document.getElementById('historySelectionCount').textContent = `${selectedCount} selected`;
            document.getElementById('deleteSelectedBtn').disabled = selectedCount === 0;
            document.getElementById('selectVisibleBtn').textContent = allVisibleSelected ? 'Clear visible' : 'Select all';
            const cleanButton = document.getElementById('cleanMissingBtn');
            cleanButton.disabled = missingCount === 0;
            cleanButton.innerHTML = `<i class="ri-eraser-line"></i>Clean missing (${missingCount})`;
        }

        function toggleSelectVisible() {
            const query = (document.getElementById('videoSearch')?.value || '').trim().toLowerCase();
            const visible = processedFiles.filter(file => (file.name || file.original_path || '').toLowerCase().includes(query));
            const allSelected = visible.length > 0 && visible.every(file => selectedHistoryHashes.has(file.hash));
            visible.forEach(file => allSelected
                ? selectedHistoryHashes.delete(file.hash)
                : selectedHistoryHashes.add(file.hash));
            renderProcessedList();
        }

        function deleteSelectedHistory() {
            if (!selectedHistoryHashes.size) return;
            deleteHistoryRecords({ hashes: [...selectedHistoryHashes] }, `${selectedHistoryHashes.size} selected`);
        }

        function cleanMissingHistory() {
            const missingCount = processedFiles.filter(file => !file.original_available && !file.output_available).length;
            if (!missingCount) return;
            deleteHistoryRecords({ missing_only: true }, `${missingCount} missing`);
        }

        function deleteHistoryRecords(payload, label) {
            if (!window.confirm(`Remove ${label} history records? Video files will not be deleted.`)) return;
            const buttons = [document.getElementById('deleteSelectedBtn'), document.getElementById('cleanMissingBtn')];
            buttons.forEach(button => { button.disabled = true; });
            fetch('/api/processed', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            })
                .then(async response => {
                    const data = await response.json();
                    if (!response.ok || !data.success) throw new Error(data.message || 'Unable to update history');
                    const deleted = new Set(data.deleted_hashes || []);
                    selectedHistoryHashes = new Set([...selectedHistoryHashes].filter(hash => !deleted.has(hash)));
                    if (deleted.has(selectedProcessedHash)) resetViewer();
                    showNotification(`Removed ${data.deleted} history record${data.deleted === 1 ? '' : 's'}`, 'success');
                    loadProcessed();
                    refreshStatus(true);
                })
                .catch(error => {
                    showNotification(error.message || 'Failed to clean history', 'error');
                    updateHistoryActions();
                });
        }

        function selectProcessed(hash) {
            const file = processedFiles.find(item => item.hash === hash);
            if (!file) return;
            selectedProcessedHash = hash;
            renderProcessedList();
            document.getElementById('viewerEmpty').hidden = true;
            document.getElementById('viewerContent').hidden = false;
            document.getElementById('viewerTitle').textContent = file.name || 'Untitled video';
            document.getElementById('viewerDate').textContent = file.processed_time
                ? `Completed ${new Date(file.processed_time).toLocaleString()}` : 'Completion date unavailable';
            document.getElementById('viewerSizes').textContent = `${formatBytes(file.file_size)} original · ${formatBytes(file.output_size)} restored`;
            const syncButton = document.getElementById('syncPlayBtn');
            const hasPlayableVideo = file.original_available || file.output_available;
            const canCompare = file.original_available && file.output_available;
            document.getElementById('compareModeBtn').disabled = !canCompare;
            if (!canCompare) comparisonMode = 'side';
            syncButton.disabled = !hasPlayableVideo;
            syncButton.innerHTML = hasPlayableVideo
                ? '<i class="ri-play-line"></i>Play both'
                : '<i class="ri-forbid-line"></i>No playable files';
            setVideoSource('originalVideo', 'originalUnavailable', file.original_available ? file.original_url : '');
            setVideoSource('restoredVideo', 'restoredUnavailable', file.output_available ? file.output_url : '');
            setComparisonMode(comparisonMode);
        }

        function resetViewer() {
            selectedProcessedHash = null;
            comparisonMode = 'side';
            setVideoSource('originalVideo', 'originalUnavailable', '');
            setVideoSource('restoredVideo', 'restoredUnavailable', '');
            setRawVideoSource('compareOriginalVideo', '');
            setRawVideoSource('compareRestoredVideo', '');
            document.getElementById('viewerContent').hidden = true;
            document.getElementById('viewerEmpty').hidden = false;
            document.getElementById('compareModeBtn').disabled = true;
            renderProcessedList();
        }

        function setVideoSource(videoId, unavailableId, source) {
            const video = document.getElementById(videoId);
            const unavailable = document.getElementById(unavailableId);
            video.pause();
            video.onerror = null;
            video.removeAttribute('src');
            // Calling load() after removing src unloads the previous resource
            // and clears its last rendered frame before the next record opens.
            video.load();
            if (source) {
                video.onerror = () => {
                    video.onerror = null;
                    video.pause();
                    video.removeAttribute('src');
                    video.load();
                    video.hidden = true;
                    unavailable.textContent = 'Video exists but cannot be played in this browser';
                    unavailable.hidden = false;
                };
                video.src = source;
                video.hidden = false;
                unavailable.hidden = true;
                video.load();
            } else {
                video.hidden = true;
                unavailable.textContent = videoId === 'originalVideo'
                    ? 'Original file is unavailable'
                    : 'Restored file is unavailable';
                unavailable.hidden = false;
            }
        }

        function setRawVideoSource(videoId, source) {
            const video = document.getElementById(videoId);
            video.pause();
            video.onerror = null;
            video.removeAttribute('src');
            video.load();
            if (!source) return;
            video.onerror = () => {
                video.onerror = null;
                setComparisonMode('side');
                showNotification('Swipe comparison is unavailable for this video', 'error');
            };
            video.src = source;
            video.load();
        }

        function setComparisonMode(mode) {
            const file = processedFiles.find(item => item.hash === selectedProcessedHash);
            const canCompare = Boolean(file?.original_available && file?.output_available);
            comparisonMode = mode === 'compare' && canCompare ? 'compare' : 'side';
            const isCompare = comparisonMode === 'compare';
            const compareView = document.getElementById('compareView');
            if (!isCompare && (document.fullscreenElement === compareView || document.webkitFullscreenElement === compareView)) {
                const exitFullscreen = document.exitFullscreen || document.webkitExitFullscreen;
                if (exitFullscreen) {
                    const result = exitFullscreen.call(document);
                    if (result?.catch) result.catch(() => {});
                }
            }
            if (!isCompare && compareView.classList.contains('compare-stage--fullscreen')) {
                compareView.classList.remove('compare-stage--fullscreen');
                document.body.classList.remove('compare-fullscreen-fallback');
                updateCompareFullscreenButton();
            }
            if (!isCompare) resetComparisonPlayback();
            document.getElementById('sideBySideView').hidden = isCompare;
            document.getElementById('compareView').hidden = !isCompare;
            document.getElementById('sideBySideBtn').classList.toggle('active', !isCompare);
            document.getElementById('compareModeBtn').classList.toggle('active', isCompare);
            ['originalVideo', 'restoredVideo', 'compareOriginalVideo', 'compareRestoredVideo']
                .map(id => document.getElementById(id))
                .forEach(video => video.pause());

            if (isCompare) {
                setRawVideoSource('compareOriginalVideo', file.original_url);
                setRawVideoSource('compareRestoredVideo', file.output_url);
            } else {
                setRawVideoSource('compareOriginalVideo', '');
                setRawVideoSource('compareRestoredVideo', '');
            }
            const syncButton = document.getElementById('syncPlayBtn');
            if (!syncButton.disabled) {
                syncButton.innerHTML = isCompare
                    ? '<i class="ri-play-line"></i>Play comparison'
                    : '<i class="ri-play-line"></i>Play both';
            }
        }

        function initializeComparePlayer() {
            const stage = document.getElementById('compareView');
            const slider = document.getElementById('compareSlider');
            const original = document.getElementById('compareOriginalVideo');
            const restored = document.getElementById('compareRestoredVideo');
            original.muted = true;
            restored.muted = true;
            slider.addEventListener('input', () => stage.style.setProperty('--comparison', `${slider.value}%`));

            const bufferingIndicator = document.getElementById('compareBuffering');
            let comparisonHold = false;
            let resumeAfterHold = false;
            let holdTimeout = null;

            const setBufferingState = active => {
                bufferingIndicator.hidden = !active;
                stage.classList.toggle('is-buffering', active);
                stage.setAttribute('aria-busy', String(active));
                const syncButton = document.getElementById('syncPlayBtn');
                if (active) {
                    syncButton.disabled = true;
                } else {
                    const file = processedFiles.find(item => item.hash === selectedProcessedHash);
                    syncButton.disabled = !(file?.original_available || file?.output_available);
                }
            };

            resetComparisonPlayback = () => {
                comparisonHold = false;
                resumeAfterHold = false;
                clearTimeout(holdTimeout);
                holdTimeout = null;
                setBufferingState(false);
                original.pause();
                restored.pause();
            };

            const alignFollower = (force = false) => {
                if (!restored.src || !Number.isFinite(original.currentTime)) return false;
                const drift = original.currentTime - restored.currentTime;
                if ((force && Math.abs(drift) > .04) || Math.abs(drift) > .75) {
                    restored.currentTime = original.currentTime;
                    restored.playbackRate = original.playbackRate;
                    return true;
                }
                restored.playbackRate = original.playbackRate;
                return false;
            };

            const releaseComparisonHold = shouldResume => {
                comparisonHold = false;
                resumeAfterHold = false;
                clearTimeout(holdTimeout);
                holdTimeout = null;
                setBufferingState(false);
                restored.playbackRate = original.playbackRate;
                if (!shouldResume) {
                    if (comparisonMode === 'compare') {
                        document.getElementById('syncPlayBtn').innerHTML = '<i class="ri-play-line"></i>Play comparison';
                    }
                    return;
                }
                const originalPlay = original.play();
                const restoredPlay = restored.play();
                if (originalPlay?.catch) originalPlay.catch(() => {});
                if (restoredPlay?.catch) restoredPlay.catch(() => {});
            };

            const tryReleaseComparisonHold = () => {
                if (!comparisonHold) return;
                if (comparisonMode !== 'compare') {
                    releaseComparisonHold(false);
                    return;
                }
                if (original.seeking || restored.seeking || original.readyState < 3 || restored.readyState < 3) return;
                if (alignFollower(true)) return;
                releaseComparisonHold(resumeAfterHold);
            };

            const holdComparisonPlayback = shouldResume => {
                resumeAfterHold = resumeAfterHold || shouldResume;
                comparisonHold = true;
                setBufferingState(true);
                original.pause();
                restored.pause();
                clearTimeout(holdTimeout);
                holdTimeout = setTimeout(() => {
                    if (!comparisonHold) return;
                    releaseComparisonHold(false);
                    showNotification('Comparison is still buffering. Press play to retry.', 'error');
                }, 8000);
            };

            const gentlyCorrectFollower = () => {
                if (comparisonHold || original.seeking || restored.seeking || original.paused || restored.paused) return;
                const drift = original.currentTime - restored.currentTime;
                if (Math.abs(drift) > .75) {
                    holdComparisonPlayback(true);
                    alignFollower(true);
                    return;
                }
                const correction = Math.max(-.08, Math.min(.08, drift * .12));
                restored.playbackRate = Math.max(.25, Math.min(4, original.playbackRate + correction));
            };
            original.addEventListener('play', () => {
                if (comparisonHold) return;
                if (alignFollower(true)) {
                    holdComparisonPlayback(true);
                    return;
                }
                const playResult = restored.play();
                if (playResult?.catch) playResult.catch(() => {});
                if (comparisonMode === 'compare') {
                    document.getElementById('syncPlayBtn').innerHTML = '<i class="ri-pause-line"></i>Pause comparison';
                }
            });
            original.addEventListener('pause', () => {
                if (comparisonHold) return;
                restored.pause();
                if (comparisonMode === 'compare') {
                    document.getElementById('syncPlayBtn').innerHTML = '<i class="ri-play-line"></i>Play comparison';
                }
            });
            original.addEventListener('seeking', () => {
                const shouldResume = comparisonHold ? resumeAfterHold : !original.paused;
                holdComparisonPlayback(shouldResume);
            });
            original.addEventListener('seeked', () => {
                alignFollower(true);
                tryReleaseComparisonHold();
            });
            const handleWaiting = () => {
                if (comparisonMode !== 'compare' || (!comparisonHold && original.paused && restored.paused)) return;
                holdComparisonPlayback(comparisonHold ? resumeAfterHold : true);
            };
            original.addEventListener('waiting', handleWaiting);
            restored.addEventListener('waiting', handleWaiting);
            original.addEventListener('canplay', tryReleaseComparisonHold);
            restored.addEventListener('canplay', tryReleaseComparisonHold);
            restored.addEventListener('seeked', tryReleaseComparisonHold);
            original.addEventListener('timeupdate', gentlyCorrectFollower);
            original.addEventListener('ratechange', () => {
                if (!restored.seeking) restored.playbackRate = original.playbackRate;
            });
            document.addEventListener('fullscreenchange', updateCompareFullscreenButton);
            document.addEventListener('webkitfullscreenchange', updateCompareFullscreenButton);
            document.addEventListener('keydown', event => {
                if (event.key === 'Escape' && stage.classList.contains('compare-stage--fullscreen')) {
                    stage.classList.remove('compare-stage--fullscreen');
                    document.body.classList.remove('compare-fullscreen-fallback');
                    updateCompareFullscreenButton();
                }
            });
        }

        function initializeSideBySideSeeking() {
            const original = document.getElementById('originalVideo');
            const restored = document.getElementById('restoredVideo');
            let seekSource = null;
            let synchronizing = false;
            let resumeSource = false;
            let resumeTarget = false;

            const bindSeekSync = (source, target) => {
                source.addEventListener('seeking', () => {
                    if (synchronizing || comparisonMode !== 'side' || !target.src) return;
                    if (!seekSource) {
                        seekSource = source;
                        resumeSource = !source.paused;
                        resumeTarget = !target.paused;
                        source.pause();
                        target.pause();
                    }
                });
                source.addEventListener('seeked', () => {
                    if (synchronizing || seekSource !== source) return;
                    if (comparisonMode !== 'side' || !target.src) {
                        seekSource = null;
                        resumeSource = false;
                        resumeTarget = false;
                        return;
                    }
                    synchronizing = true;
                    target.currentTime = source.currentTime;
                    target.playbackRate = source.playbackRate;

                    const finishSync = () => {
                        synchronizing = false;
                        seekSource = null;
                        const sourcePlay = resumeSource ? source.play() : null;
                        const targetPlay = resumeTarget ? target.play() : null;
                        if (sourcePlay?.catch) sourcePlay.catch(() => {});
                        if (targetPlay?.catch) targetPlay.catch(() => {});
                        resumeSource = false;
                        resumeTarget = false;
                    };
                    const finishWhenReady = () => {
                        if (target.seeking) return;
                        if (target.readyState < 3) {
                            target.addEventListener('canplay', finishWhenReady, { once: true });
                            return;
                        }
                        finishSync();
                    };
                    if (target.seeking) {
                        target.addEventListener('seeked', finishWhenReady, { once: true });
                    } else {
                        finishWhenReady();
                    }
                });
            };

            bindSeekSync(original, restored);
            bindSeekSync(restored, original);
        }

        function updateCompareFullscreenButton() {
            const stage = document.getElementById('compareView');
            const button = document.getElementById('compareFullscreenBtn');
            const isFullscreen = document.fullscreenElement === stage
                || document.webkitFullscreenElement === stage
                || stage.classList.contains('compare-stage--fullscreen');
            button.title = isFullscreen ? 'Exit fullscreen comparison' : 'Enter fullscreen comparison';
            button.setAttribute('aria-label', button.title);
            button.innerHTML = isFullscreen
                ? '<i class="ri-fullscreen-exit-line"></i><span>Exit fullscreen</span>'
                : '<i class="ri-fullscreen-line"></i><span>Fullscreen compare</span>';
        }

        function toggleCompareFullscreen() {
            const stage = document.getElementById('compareView');
            const isNativeFullscreen = document.fullscreenElement === stage || document.webkitFullscreenElement === stage;
            const isFallbackFullscreen = stage.classList.contains('compare-stage--fullscreen');
            const enterFallbackFullscreen = () => {
                stage.classList.add('compare-stage--fullscreen');
                document.body.classList.add('compare-fullscreen-fallback');
                updateCompareFullscreenButton();
            };
            if (isNativeFullscreen) {
                const exitFullscreen = document.exitFullscreen || document.webkitExitFullscreen;
                if (exitFullscreen) {
                    const result = exitFullscreen.call(document);
                    if (result?.catch) result.catch(() => showNotification('Unable to exit fullscreen', 'error'));
                }
                return;
            }
            if (isFallbackFullscreen) {
                stage.classList.remove('compare-stage--fullscreen');
                document.body.classList.remove('compare-fullscreen-fallback');
                updateCompareFullscreenButton();
                return;
            }
            const requestFullscreen = stage.requestFullscreen || stage.webkitRequestFullscreen;
            if (!requestFullscreen) {
                enterFallbackFullscreen();
                return;
            }
            const result = requestFullscreen.call(stage);
            if (result?.catch) result.catch(enterFallbackFullscreen);
        }

        function toggleSynchronizedPlayback() {
            if (comparisonMode === 'compare') {
                const original = document.getElementById('compareOriginalVideo');
                const restored = document.getElementById('compareRestoredVideo');
                if (!original.src || !restored.src) return;
                if (original.paused) {
                    restored.currentTime = original.currentTime;
                    original.play().catch(() => {
                        restored.pause();
                        document.getElementById('syncPlayBtn').innerHTML = '<i class="ri-play-line"></i>Play comparison';
                        showNotification('Unable to start comparison playback', 'error');
                    });
                } else {
                    original.pause();
                    restored.pause();
                }
                return;
            }
            const ids = comparisonMode === 'compare'
                ? ['compareOriginalVideo', 'compareRestoredVideo']
                : ['originalVideo', 'restoredVideo'];
            const videos = ids
                .map(id => document.getElementById(id))
                .filter(video => video && !video.hidden && video.src);
            if (!videos.length) return;
            const shouldPlay = videos.every(video => video.paused);
            if (shouldPlay) {
                const anchor = videos[0].currentTime;
                videos.forEach(video => { if (Math.abs(video.currentTime - anchor) > .25) video.currentTime = anchor; });
                Promise.allSettled(videos.map(video => video.play()));
            } else {
                videos.forEach(video => video.pause());
            }
            const button = document.getElementById('syncPlayBtn');
            const subject = comparisonMode === 'compare' ? 'comparison' : 'both';
            button.innerHTML = shouldPlay
                ? `<i class="ri-pause-line"></i>Pause ${subject}`
                : `<i class="ri-play-line"></i>Play ${subject}`;
        }

        // Logs
        function switchLogType(type) {
            currentLogType = type;
            document.querySelectorAll('[data-tab]').forEach(b => b.classList.remove('active'));
            document.getElementById(type === 'lada' ? 'logTypeLada' : 'logTypeWeb').classList.add('active');
            loadLogs();
        }

        function loadLogs(isAuto = false) {
            const logBox = document.getElementById('logsContent');
            if (isAuto && !shouldAutoRefresh()) return;
            if (!isAuto) logBox.innerHTML = '<span style="color:var(--primary)">> Loading logs...</span>';

            fetch(`/api/logs?type=${currentLogType}`)
                .then(r => r.json())
                .then(data => {
                    if (data.logs?.length) {
                        const isAtBottom = Math.abs(logBox.scrollHeight - logBox.clientHeight - logBox.scrollTop) < 20;
                        logBox.textContent = data.logs.join('\n');
                        if (isAtBottom || !isAuto) logBox.scrollTop = logBox.scrollHeight;
                    } else {
                        if (!isAuto) logBox.textContent = '> No logs available.';
                    }
                })
                .catch(() => {
                    if (!isAuto) logBox.textContent = '> Failed to load logs.';
                });
        }

        function toggleAutoRefresh() {
            autoRefresh = !autoRefresh;
            const btn = document.getElementById('autoRefreshBtn');
            btn.classList.toggle('active', autoRefresh);
            btn.innerHTML = autoRefresh
                ? '<i class="ri-radio-button-line"></i> Auto'
                : '<i class="ri-pause-circle-line"></i> Paused';

            if (autoRefresh) {
                refreshStatus();
                loadQueue();
                loadLogs(true);
            }
        }

        // Monitoring Control
        function startMonitoring() {
            if (actionInFlight) return;
            if (isDirty && !confirm('You have unsaved changes. Start with the last saved config?')) {
                return;
            }
            if (!currentConfig.monitor_directories?.length) {
                showNotification('Please configure at least one folder', 'error');
                return;
            }

            setActionLoading('start', true);
            fetch('/api/start', { method: 'POST' })
                .then(r => r.json())
                .then(d => {
                    d.success ? showNotification('Monitoring started', 'success') : showNotification(d.message, 'error');
                    setTimeout(refreshStatus, 500);
                })
                .catch(() => showNotification('Failed to start monitoring', 'error'))
                .finally(() => setActionLoading('start', false));
        }

        function stopMonitoring(terminateRunning = false) {
            if (actionInFlight) return;
            if (terminateRunning && !confirm('Stop monitoring and terminate running LADA processes?')) {
                return;
            }
            setActionLoading('stop', true);
            fetch('/api/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ terminate_running: terminateRunning })
            })
                .then(r => r.json())
                .then(d => {
                    d.success ? showNotification('Monitoring stopped', 'success') : showNotification(d.message, 'error');
                    setTimeout(refreshStatus, 500);
                })
                .catch(() => showNotification('Failed to stop monitoring', 'error'))
                .finally(() => setActionLoading('stop', false));
        }

        function openOutput() {
            if (!currentConfig.monitor_directories?.length) {
                showNotification('No folders configured', 'error');
                return;
            }
            const targetDir = currentConfig.monitor_directories[currentConfig.monitor_directories.length - 1];
            openPathRaw(getRestoredPath(targetDir), true).then(success => {
                if (success && currentConfig.monitor_directories.length > 1) {
                    showNotification('Opened Restored for the most recent folder. Use folder actions for others.', 'success');
                }
            });
        }

        // UI Utils
        function showNotification(msg, type) {
            const notif = document.getElementById('notification');
            const icon = document.getElementById('notifIcon');
            const txt = document.getElementById('notifMessage');
            const notificationType = type === 'success' ? 'success' : 'error';

            notif.className = `notification ${notificationType} show`;
            icon.className = notificationType === 'success' ? 'ri-checkbox-circle-fill' : 'ri-error-warning-fill';
            txt.textContent = msg;

            setTimeout(() => notif.classList.remove('show'), 3000);
        }

        // Init
        document.addEventListener('DOMContentLoaded', () => {
            document.body.classList.add('loaded');
            bindInputHandlers();
            initializeComparePlayer();
            initializeSideBySideSeeking();
            setDirty(false);
            initSocket();
            refreshStatus();
            loadConfig();
            loadLogs();
            loadQueue();
            const initialPage = location.hash.replace('#', '');
            if (['overview', 'library', 'logs', 'settings'].includes(initialPage)) switchPage(initialPage);

            setInterval(() => {
                if (shouldAutoRefresh()) loadLogs(true);
            }, 5000);
            setInterval(() => {
                if (shouldAutoRefresh()) loadQueue(true);
            }, 5000);
            setInterval(() => {
                if (shouldAutoRefresh()) refreshStatus(true);
            }, 10000);

            document.addEventListener('visibilitychange', () => {
                if (shouldAutoRefresh()) {
                    refreshStatus(true);
                    loadQueue(true);
                    loadLogs(true);
                }
            });
        });
