/**
 * Limca Bottle Defect Detection System - Frontend Interactive Controller
 * Real-time polling, camera HUD, ESP32 COM5 serial management,
 * In-Transit conveyor pipeline visualizer, and end-to-end sorting lifecycle.
 */

document.addEventListener('DOMContentLoaded', () => {
  // Camera & Stream Elements
  const btnStartCam = document.getElementById('btnStartCam');
  const btnStopCam = document.getElementById('btnStopCam');
  const btnCapture = document.getElementById('btnCapture');
  const liveBadge = document.getElementById('liveBadge');
  const fpsDisplay = document.getElementById('fpsDisplay');
  const cameraStatusPill = document.getElementById('cameraStatusPill');
  const conveyorStatusPill = document.getElementById('conveyorStatusPill');
  const hardwareStatusPill = document.getElementById('hardwareStatusPill');
  const bannerText = document.getElementById('bannerText');
  const bannerIcon = document.getElementById('bannerIcon');
  const webcamStream = document.getElementById('webcamStream');

  // Real-Time Prediction Elements
  const predictionCard = document.getElementById('predictionCard');
  const statusIcon = document.getElementById('statusIcon');
  const statusTitle = document.getElementById('statusTitle');
  const statusDesc = document.getElementById('statusDesc');
  const confBadge = document.getElementById('confBadge');
  const confValueText = document.getElementById('confValueText');
  const meterFill = document.getElementById('meterFill');
  const probNonDef = document.getElementById('probNonDef');
  const probDef = document.getElementById('probDef');

  // Inspection Statistics Elements (6 cards)
  const statTotal = document.getElementById('statTotal');
  const statNonDef = document.getElementById('statNonDef');
  const statDef = document.getElementById('statDef');
  const statUncertain = document.getElementById('statUncertain');
  const statSortedDef = document.getElementById('statSortedDef');
  const statMissing = document.getElementById('statMissing');

  // Hardware Telemetry Elements
  const telemetryConveyor = document.getElementById('telemetryConveyor');
  const telemetryIR = document.getElementById('telemetryIR');
  const telemetryServo = document.getElementById('telemetryServo');
  const telemetryQueue = document.getElementById('telemetryQueue');

  // Conveyor In-Transit Pipeline
  const pipelineContainer = document.getElementById('pipelineContainer');
  const pipelineCountBadge = document.getElementById('pipelineCountBadge');

  // Architecture Metrics
  const metricArch = document.getElementById('metricArch');
  const metricAcc = document.getElementById('metricAcc');
  const metricLatency = document.getElementById('metricLatency');
  const metricTravel = document.getElementById('metricTravel');
  const btnRefreshMetrics = document.getElementById('btnRefreshMetrics');

  // History & Hardware Controls
  const historyTableBody = document.getElementById('historyTableBody');
  const btnClearHistory = document.getElementById('btnClearHistory');
  const hwToggle = document.getElementById('hwToggle');
  const hwModeTag = document.getElementById('hwModeTag');
  const hwPortSelect = document.getElementById('hwPortSelect');
  const btnHwConnect = document.getElementById('btnHwConnect');
  const btnHwDisconnect = document.getElementById('btnHwDisconnect');
  const btnHwRefreshPorts = document.getElementById('btnHwRefreshPorts');
  const btnHwTestDefect = document.getElementById('btnHwTestDefect');
  const btnHwClearQueue = document.getElementById('btnHwClearQueue');
  const btnHwStatus = document.getElementById('btnHwStatus');

  // Modal Elements
  const imageModal = document.getElementById('imageModal');
  const modalImg = document.getElementById('modalImg');
  const modalTitle = document.getElementById('modalTitle');
  const modalClose = document.getElementById('modalClose');
  const modalBackdrop = document.getElementById('modalBackdrop');

  let pollInterval = null;
  let historyPollInterval = null;
  let isCameraRunning = false;

  const ICONS = {
    check: `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <path d="M20 6L9 17l-5-5"></path>
      </svg>`,
    alert: `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
        <line x1="12" y1="9" x2="12" y2="13"></line>
        <line x1="12" y1="17" x2="12.01" y2="17"></line>
      </svg>`,
    uncertain: `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="12" cy="12" r="10"></circle>
        <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>
        <line x1="12" y1="17" x2="12.01" y2="17"></line>
      </svg>`,
    search: `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>`
  };

  // ==================== CAMERA CONTROLS ====================

  async function startCamera() {
    try {
      btnStartCam.disabled = true;
      bannerText.textContent = 'Starting conveyor webcam...';
      const res = await fetch('/api/camera/start', { method: 'POST' });
      const data = await res.json();

      if (data.success) {
        isCameraRunning = true;
        btnStartCam.disabled = true;
        btnStopCam.disabled = false;
        liveBadge.style.display = 'inline-block';
        cameraStatusPill.className = 'status-pill active';
        cameraStatusPill.querySelector('.pill-text').textContent = 'Camera Live';
        bannerIcon.textContent = '🟢';
        bannerText.textContent = 'Laptop Webcam Active - Automated Conveyor Inspection Running';
        webcamStream.src = '/video_feed?t=' + Date.now();
        startPolling();
      } else {
        btnStartCam.disabled = false;
        bannerIcon.textContent = '⚠️';
        bannerText.textContent = data.message || 'Error starting camera';
      }
    } catch (err) {
      console.error(err);
      btnStartCam.disabled = false;
      bannerText.textContent = 'Connection error starting camera';
    }
  }

  async function stopCamera() {
    try {
      btnStopCam.disabled = true;
      const res = await fetch('/api/camera/stop', { method: 'POST' });
      const data = await res.json();

      isCameraRunning = false;
      btnStartCam.disabled = false;
      btnStopCam.disabled = true;
      liveBadge.style.display = 'none';
      fpsDisplay.textContent = '0 FPS';
      cameraStatusPill.className = 'status-pill';
      cameraStatusPill.querySelector('.pill-text').textContent = 'Camera Standby';
      bannerIcon.textContent = 'ℹ️';
      bannerText.textContent = 'Camera Stopped';
      stopPolling();
      resetPredictionDisplay();
    } catch (err) {
      console.error(err);
      btnStopCam.disabled = false;
    }
  }

  // ==================== REAL-TIME POLLING ====================

  function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollFeedState, 150);

    if (historyPollInterval) clearInterval(historyPollInterval);
    historyPollInterval = setInterval(loadHistory, 700);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
    if (historyPollInterval) {
      clearInterval(historyPollInterval);
      historyPollInterval = null;
    }
  }

  async function pollFeedState() {
    try {
      const [camRes, predRes, hwRes] = await Promise.all([
        fetch('/api/camera/status'),
        fetch('/api/predict/current'),
        fetch('/api/hardware/status')
      ]);

      const camData = await camRes.json();
      const predData = await predRes.json();
      const hwData = await hwRes.json();

      if (camData.fps !== undefined) {
        fpsDisplay.textContent = `${camData.fps} FPS`;
      }

      if (camData.error) {
        bannerIcon.textContent = '⚠️';
        bannerText.textContent = camData.error;
      }

      updatePredictionUI(predData);
      updateHardwareUI(hwData);
    } catch (e) {
      // transient network hitch
    }
  }

  function updatePredictionUI(pred) {
    if (!pred || !isCameraRunning) return;

    const status = pred.status || 'NO_BOTTLE';
    const conf = pred.confidence || 0.0;
    const probs = pred.probabilities || { Defective: 0.0, Non_Defective: 0.0 };

    confBadge.textContent = `${conf.toFixed(1)}%`;
    confValueText.textContent = `${conf.toFixed(1)}%`;
    meterFill.style.width = `${Math.min(100, Math.max(0, conf))}%`;

    probNonDef.textContent = `${(probs.Non_Defective * 100).toFixed(1)}%`;
    probDef.textContent = `${(probs.Defective * 100).toFixed(1)}%`;

    predictionCard.classList.remove('status-non-defective', 'status-defective', 'status-uncertain');

    if (status === 'NON-DEFECTIVE') {
      predictionCard.classList.add('status-non-defective');
      statusIcon.innerHTML = ICONS.check;
      statusTitle.textContent = 'BOTTLE IS NON-DEFECTIVE';
      statusDesc.textContent = 'Clean surface, label intact - Verdict: PASS';
      meterFill.style.background = '#10b981';
      bannerText.textContent = 'Detected: NON-DEFECTIVE (' + conf.toFixed(1) + '%) -> PASS enqueued';
      bannerIcon.textContent = '✅';
    } else if (status === 'DEFECTIVE') {
      predictionCard.classList.add('status-defective');
      statusIcon.innerHTML = ICONS.alert;
      statusTitle.textContent = 'BOTTLE IS DEFECTIVE';
      statusDesc.textContent = 'Surface or body flaw detected - Verdict: DEFECT (Servo 180°)';
      meterFill.style.background = '#ef4444';
      bannerText.textContent = 'Detected: DEFECTIVE (' + conf.toFixed(1) + '%) -> DEFECT enqueued';
      bannerIcon.textContent = '🚨';
    } else if (status === 'UNCERTAIN') {
      predictionCard.classList.add('status-uncertain');
      statusIcon.innerHTML = ICONS.uncertain;
      statusTitle.textContent = 'UNCERTAIN – PLEASE REPOSITION BOTTLE';
      statusDesc.textContent = 'Confidence below threshold. Servo stays at 0° (safe bypass)';
      meterFill.style.background = '#f59e0b';
      bannerText.textContent = 'UNCERTAIN - Reposition bottle for clear view';
      bannerIcon.textContent = '⚠️';
    } else {
      statusIcon.innerHTML = ICONS.search;
      statusTitle.textContent = 'NO BOTTLE DETECTED';
      statusDesc.textContent = 'Conveyor running. Position bottle within webcam reticle';
      meterFill.style.background = '#6b7280';
      meterFill.style.width = '0%';
      confBadge.textContent = '-- %';
      confValueText.textContent = '0.0%';
      bannerText.textContent = 'Waiting for bottle on conveyor...';
      bannerIcon.textContent = '🔍';
    }
  }

  function resetPredictionDisplay() {
    predictionCard.classList.remove('status-non-defective', 'status-defective', 'status-uncertain');
    statusIcon.innerHTML = ICONS.search;
    statusTitle.textContent = 'NO BOTTLE DETECTED';
    statusDesc.textContent = 'Start camera to inspect bottles moving on conveyor';
    confBadge.textContent = '-- %';
    confValueText.textContent = '0.0%';
    meterFill.style.width = '0%';
    probNonDef.textContent = '0.0%';
    probDef.textContent = '0.0%';
  }

  // ==================== MANUAL TEST CAPTURE ====================

  async function captureAndAnalyze() {
    try {
      btnCapture.disabled = true;
      btnCapture.textContent = 'Analyzing...';

      const container = document.getElementById('videoContainer');
      container.style.filter = 'brightness(1.5)';
      setTimeout(() => { container.style.filter = 'none'; }, 150);

      const res = await fetch('/api/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enqueue: false }) // Does NOT disrupt automatic FIFO queue
      });
      const data = await res.json();

      if (data.success) {
        updateStats(data.stats);
        updatePredictionUI(data.record);
        prependHistoryRow(data.record, true);
      }
    } catch (err) {
      console.error('Capture error:', err);
    } finally {
      btnCapture.disabled = false;
      btnCapture.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
          <circle cx="12" cy="13" r="4"></circle>
        </svg>
        Capture Test Snapshot`;
    }
  }

  // ==================== STATISTICS & HISTORY ====================

  function updateStats(s) {
    if (!s) return;
    if (statTotal) statTotal.textContent = s.total_inspected ?? 0;
    if (statNonDef) statNonDef.textContent = s.total_non_defective ?? 0;
    if (statDef) statDef.textContent = s.total_defective ?? 0;
    if (statUncertain) statUncertain.textContent = s.total_uncertain ?? 0;
    if (statSortedDef) statSortedDef.textContent = s.total_sorted_defective ?? 0;
    if (statMissing) statMissing.textContent = s.total_missing_verdicts ?? 0;
  }

  async function loadHistory() {
    try {
      const res = await fetch('/api/history');
      const data = await res.json();
      updateStats(data.stats);

      if (data.history && data.history.length > 0) {
        historyTableBody.innerHTML = '';
        data.history.forEach(item => prependHistoryRow(item, false));
      } else {
        historyTableBody.innerHTML = `
          <tr class="empty-row">
            <td colspan="6">No bottles analyzed yet. Start the camera and run bottles along the conveyor to see real-time sorting.</td>
          </tr>`;
      }
    } catch (err) {
      console.error('Failed to load history:', err);
    }
  }

  function getStatusBadge(status) {
    switch (status) {
      case 'SORTED':
        return '<span class="badge badge-green" style="font-weight: 700;">🎯 SORTED</span>';
      case 'PASSED':
        return '<span class="badge badge-green">✓ PASSED</span>';
      case 'SORTING':
        return '<span class="badge badge-red" style="animation: pulse 1s infinite;">⚡ SORTING (180°)</span>';
      case 'IR DETECTED':
        return '<span class="badge" style="background: rgba(147, 51, 234, 0.2); color: #c084fc; border: 1px solid rgba(147, 51, 234, 0.4);">👁️ IR DETECTED</span>';
      case 'VERDICT QUEUED':
        return '<span class="badge badge-amber">⏱️ VERDICT QUEUED</span>';
      case 'UNCERTAIN':
        return '<span class="badge badge-amber">❓ UNCERTAIN</span>';
      case 'MISSING VERDICT':
        return '<span class="badge badge-red">🚨 MISSING VERDICT</span>';
      case 'AI CLASSIFIED':
        return '<span class="badge badge-blue">🧠 AI CLASSIFIED</span>';
      default:
        return `<span class="badge badge-gray">${status}</span>`;
    }
  }

  function prependHistoryRow(item, prepend = true) {
    const emptyRow = historyTableBody.querySelector('.empty-row');
    if (emptyRow) emptyRow.remove();

    const row = document.createElement('tr');

    let verdictBadge = '';
    if (item.verdict === 'DEFECT' || item.status === 'DEFECTIVE') {
      verdictBadge = '<span class="badge badge-red">DEFECT</span>';
    } else if (item.verdict === 'PASS' || item.status === 'NON-DEFECTIVE') {
      verdictBadge = '<span class="badge badge-green">PASS</span>';
    } else {
      verdictBadge = '<span class="badge badge-amber">UNCERTAIN</span>';
    }

    const confStr = item.confidence ? `${item.confidence.toFixed(1)}%` : '--';
    const statusBadge = getStatusBadge(item.status);

    let hwActionHtml = `<small style="color: #9ca3af;">${item.hardware_action || 'Enqueued'}</small>`;
    if (item.sort_confirmed) {
      hwActionHtml = `<span style="color: var(--accent-green); font-weight: 600;">✓ Defect Pushed (180°)</span>`;
    }

    const travelText = item.travel_time_sec ? ` (~${item.travel_time_sec}s travel)` : '';

    row.innerHTML = `
      <td>
        <img src="${item.image_url}" class="history-thumb" alt="Thumbnail" data-full="${item.image_url}" data-title="${item.id}">
      </td>
      <td><code>${item.id}</code></td>
      <td>${item.timestamp}${travelText}</td>
      <td>
        ${verdictBadge} <strong>${confStr}</strong>
      </td>
      <td>${statusBadge}</td>
      <td>${hwActionHtml}</td>
    `;

    const thumb = row.querySelector('.history-thumb');
    thumb.addEventListener('click', () => {
      openModal(thumb.dataset.full, thumb.dataset.title);
    });

    if (prepend) {
      historyTableBody.insertBefore(row, historyTableBody.firstChild);
    } else {
      historyTableBody.appendChild(row);
    }
  }

  async function clearHistory() {
    if (!confirm('Reset inspection history and clear hardware FIFO queues?')) return;
    try {
      await fetch('/api/history', { method: 'DELETE' });
      historyTableBody.innerHTML = `
        <tr class="empty-row">
          <td colspan="6">History reset. Start camera to inspect new bottles.</td>
        </tr>`;
      updateStats({
        total_inspected: 0,
        total_non_defective: 0,
        total_defective: 0,
        total_uncertain: 0,
        total_sorted_defective: 0,
        total_missing_verdicts: 0
      });
      loadHardwareStatus();
    } catch (err) {
      console.error(err);
    }
  }

  // ==================== MODEL METRICS ====================

  async function loadMetrics() {
    try {
      const res = await fetch('/api/metrics');
      if (!res.ok) return;
      const data = await res.json();

      if (data.performance_metrics) {
        const pm = data.performance_metrics;
        metricAcc.textContent = `${(pm.test_accuracy * 100).toFixed(1)}%`;
      }
    } catch (err) {
      console.error('Error fetching metrics:', err);
    }
  }

  // ==================== HARDWARE SERIAL ====================

  async function loadHardwarePorts() {
    try {
      const res = await fetch('/api/hardware/ports');
      const data = await res.json();
      const currentSelected = hwPortSelect.value;
      hwPortSelect.innerHTML = '<option value="">Auto-Detect Port (Preferred: COM5)</option>';

      let com5Found = false;
      if (data.ports && data.ports.length > 0) {
        data.ports.forEach(port => {
          const opt = document.createElement('option');
          opt.value = port;
          if (port === 'COM5') com5Found = true;
          const detail = (data.details || []).find(d => d.port === port);
          const desc = detail && detail.description ? ` (${detail.description.substring(0, 22)}...)` : '';
          opt.textContent = `${port}${desc}`;
          hwPortSelect.appendChild(opt);
        });
      }

      if (data.connected_port) {
        hwPortSelect.value = data.connected_port;
      } else if (com5Found) {
        hwPortSelect.value = 'COM5';
      } else if (currentSelected) {
        hwPortSelect.value = currentSelected;
      }
    } catch (err) {
      console.error('Error fetching COM ports:', err);
    }
  }

  async function loadHardwareStatus() {
    try {
      const res = await fetch('/api/hardware/status');
      const data = await res.json();
      updateHardwareUI(data);
    } catch (err) {
      console.error(err);
    }
  }

  function updateHardwareUI(hw) {
    if (!hw) return;

    hwToggle.checked = !!hw.enabled;

    // Conveyor status
    telemetryConveyor.textContent = 'CONTINUOUS';

    // IR Sensor Active LOW telemetry
    if (hw.ir_sensor_active) {
      telemetryIR.textContent = 'BEAM BROKEN (LOW)';
      telemetryIR.style.color = '#ef4444';
    } else {
      telemetryIR.textContent = 'CLEAR (HIGH)';
      telemetryIR.style.color = '#10b981';
    }

    // Servo angle
    if (hw.servo_angle >= 90) {
      telemetryServo.textContent = `${hw.servo_angle}° (PUSHING)`;
      telemetryServo.style.color = '#ef4444';
    } else {
      telemetryServo.textContent = '0° (REST)';
      telemetryServo.style.color = '#9ca3af';
    }

    // Queue count
    telemetryQueue.textContent = `${hw.hw_queue_count || hw.in_transit_count || 0} In-Flight`;

    // Latency & Travel
    if (hw.last_prediction_latency > 0 && metricLatency) {
      metricLatency.textContent = `${(hw.last_prediction_latency * 1000).toFixed(0)} ms`;
    }
    if (hw.last_travel_time > 0 && metricTravel) {
      metricTravel.textContent = `${hw.last_travel_time.toFixed(1)}s`;
    }

    // Pipeline visualization
    if (pipelineContainer && pipelineCountBadge) {
      const pipeline = hw.in_transit_pipeline || [];
      pipelineCountBadge.textContent = `${pipeline.length} in transit`;

      if (pipeline.length === 0) {
        pipelineContainer.innerHTML = '<span style="color: var(--text-muted); font-size: 0.8rem; font-style: italic;">No bottles currently in transit between camera and IR sensor.</span>';
      } else {
        pipelineContainer.innerHTML = '';
        pipeline.forEach(b => {
          const chip = document.createElement('div');
          chip.style.cssText = `
            background: rgba(30, 41, 59, 0.9);
            border: 1px solid ${b.verdict === 'DEFECT' ? 'rgba(239, 68, 68, 0.5)' : 'rgba(16, 185, 129, 0.5)'};
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 0.78rem;
            display: flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
          `;
          const badgeClass = b.verdict === 'DEFECT' ? 'badge-red' : 'badge-green';
          chip.innerHTML = `
            <strong>${b.id}</strong>
            <span class="badge ${badgeClass}" style="font-size: 0.7rem; padding: 1px 6px;">${b.verdict}</span>
            <small style="color: #94a3b8;">${b.age_sec}s</small>
          `;
          pipelineContainer.appendChild(chip);
        });
      }
    }

    // Statistics update
    if (hw.stats) {
      updateStats(hw.stats);
    }

    // Connection button states
    if (hw.connected) {
      btnHwConnect.style.display = 'none';
      btnHwDisconnect.style.display = 'inline-block';
      hardwareStatusPill.className = hw.enabled ? 'status-pill active' : 'status-pill';
      hardwareStatusPill.querySelector('.pill-text').textContent = `ESP32: ${hw.port} (${hw.mode})`;

      if (hw.enabled) {
        hwModeTag.textContent = `MODE: PHYSICAL (${hw.port} ACTIVE)`;
        hwModeTag.style.color = 'var(--accent-green)';
      } else {
        hwModeTag.textContent = `MODE: STANDBY (CONNECTED TO ${hw.port})`;
        hwModeTag.style.color = 'var(--accent-amber)';
      }
    } else {
      btnHwConnect.style.display = 'inline-block';
      btnHwDisconnect.style.display = 'none';
      hardwareStatusPill.className = 'status-pill';
      hardwareStatusPill.querySelector('.pill-text').textContent = 'ESP32: Disconnected';
      hwModeTag.textContent = 'MODE: STANDBY (DISCONNECTED)';
      hwModeTag.style.color = 'var(--text-muted)';
      hwToggle.checked = false;
    }
  }

  // ==================== EVENT LISTENERS ====================

  hwToggle.addEventListener('change', async () => {
    try {
      const targetState = hwToggle.checked;
      const res = await fetch('/api/hardware/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enable: targetState })
      });
      const data = await res.json();
      if (!data.success && targetState) {
        alert(data.message || 'Cannot enable hardware: Please connect to ESP32 COM port (e.g. COM5) first.');
      }
      updateHardwareUI(data.status || data);
    } catch (err) {
      console.error(err);
      hwToggle.checked = !hwToggle.checked;
    }
  });

  btnHwConnect.addEventListener('click', async () => {
    try {
      btnHwConnect.disabled = true;
      btnHwConnect.textContent = 'Connecting...';
      const port = hwPortSelect.value || null;
      const res = await fetch('/api/hardware/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ port })
      });
      const data = await res.json();
      alert(data.message);
      updateHardwareUI(data.status || {});
    } catch (err) {
      alert('Connection error: ' + err);
    } finally {
      btnHwConnect.disabled = false;
      btnHwConnect.textContent = 'Connect';
    }
  });

  btnHwDisconnect.addEventListener('click', async () => {
    try {
      btnHwDisconnect.disabled = true;
      const res = await fetch('/api/hardware/disconnect', { method: 'POST' });
      const data = await res.json();
      alert(data.message);
      updateHardwareUI(data.status || {});
    } catch (err) {
      console.error(err);
    } finally {
      btnHwDisconnect.disabled = false;
    }
  });

  btnHwRefreshPorts.addEventListener('click', async () => {
    await loadHardwarePorts();
  });

  btnHwTestDefect.addEventListener('click', async () => {
    const res = await fetch('/api/hardware/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'SERVO_TEST' })
    });
    const data = await res.json();
    alert(`Diverter 180° Test: ${data.message}`);
    loadHardwareStatus();
  });

  btnHwClearQueue.addEventListener('click', async () => {
    const res = await fetch('/api/hardware/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'CLEAR_QUEUE' })
    });
    const data = await res.json();
    alert(`FIFO Queue: ${data.message}`);
    loadHardwareStatus();
  });

  btnHwStatus.addEventListener('click', async () => {
    await loadHardwareStatus();
  });

  // Modal handlers
  function openModal(imgSrc, title) {
    modalImg.src = imgSrc;
    modalTitle.textContent = `Inspection Snapshot: ${title}`;
    imageModal.classList.add('active');
  }

  function closeModal() {
    imageModal.classList.remove('active');
    modalImg.src = '';
  }

  modalClose.addEventListener('click', closeModal);
  modalBackdrop.addEventListener('click', closeModal);

  // Button bindings
  btnStartCam.addEventListener('click', startCamera);
  btnStopCam.addEventListener('click', stopCamera);
  btnCapture.addEventListener('click', captureAndAnalyze);
  btnClearHistory.addEventListener('click', clearHistory);
  btnRefreshMetrics.addEventListener('click', loadMetrics);

  // Initial Boot Loads
  loadHistory();
  loadMetrics();
  loadHardwarePorts();
  loadHardwareStatus();
});
