/**
 * Limca Bottle Defect Detection System - Frontend Interactive Controller
 * Handles live polling, HUD updates, camera controls, manual capture,
 * ESP32 hardware dispatch, and history table management.
 */

document.addEventListener('DOMContentLoaded', () => {
  // UI Elements
  const btnStartCam = document.getElementById('btnStartCam');
  const btnStopCam = document.getElementById('btnStopCam');
  const btnCapture = document.getElementById('btnCapture');
  const liveBadge = document.getElementById('liveBadge');
  const fpsDisplay = document.getElementById('fpsDisplay');
  const cameraStatusPill = document.getElementById('cameraStatusPill');
  const hardwareStatusPill = document.getElementById('hardwareStatusPill');
  const bannerText = document.getElementById('bannerText');
  const bannerIcon = document.getElementById('bannerIcon');
  const webcamStream = document.getElementById('webcamStream');

  // Prediction elements
  const predictionCard = document.getElementById('predictionCard');
  const statusIcon = document.getElementById('statusIcon');
  const statusTitle = document.getElementById('statusTitle');
  const statusDesc = document.getElementById('statusDesc');
  const confBadge = document.getElementById('confBadge');
  const confValueText = document.getElementById('confValueText');
  const meterFill = document.getElementById('meterFill');
  const probNonDef = document.getElementById('probNonDef');
  const probDef = document.getElementById('probDef');

  // Statistics
  const statTotal = document.getElementById('statTotal');
  const statNonDef = document.getElementById('statNonDef');
  const statDef = document.getElementById('statDef');
  const statDefectRate = document.getElementById('statDefectRate');

  // Model Metrics
  const metricArch = document.getElementById('metricArch');
  const metricAcc = document.getElementById('metricAcc');
  const metricLoss = document.getElementById('metricLoss');
  const btnRefreshMetrics = document.getElementById('btnRefreshMetrics');

  // History & Hardware
  const historyTableBody = document.getElementById('historyTableBody');
  const btnClearHistory = document.getElementById('btnClearHistory');
  const hwToggle = document.getElementById('hwToggle');
  const hwModeTag = document.getElementById('hwModeTag');
  const btnHwTestDefect = document.getElementById('btnHwTestDefect');
  const btnHwTestPass = document.getElementById('btnHwTestPass');

  // Modal
  const imageModal = document.getElementById('imageModal');
  const modalImg = document.getElementById('modalImg');
  const modalTitle = document.getElementById('modalTitle');
  const modalClose = document.getElementById('modalClose');
  const modalBackdrop = document.getElementById('modalBackdrop');

  let pollInterval = null;
  let isCameraRunning = false;

  // SVG Icons
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
      bannerText.textContent = 'Starting webcam...';
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
        bannerText.textContent = 'MacBook Webcam Active';
        // Reload stream source
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
    pollInterval = setInterval(pollFeedState, 180);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  async function pollFeedState() {
    try {
      const [camRes, predRes] = await Promise.all([
        fetch('/api/camera/status'),
        fetch('/api/predict/current')
      ]);

      const camData = await camRes.json();
      const predData = await predRes.json();

      if (camData.fps !== undefined) {
        fpsDisplay.textContent = `${camData.fps} FPS`;
      }

      if (camData.error) {
        bannerIcon.textContent = '⚠️';
        bannerText.textContent = camData.error;
      }

      updatePredictionUI(predData);
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

    // Remove old classes
    predictionCard.classList.remove('status-non-defective', 'status-defective', 'status-uncertain');

    if (status === 'NON-DEFECTIVE') {
      predictionCard.classList.add('status-non-defective');
      statusIcon.innerHTML = ICONS.check;
      statusTitle.textContent = 'BOTTLE IS NON-DEFECTIVE';
      statusDesc.textContent = 'Clear bottle, intact surface - Passes quality standards';
      meterFill.style.background = '#10b981';
      bannerText.textContent = 'Detected: NON-DEFECTIVE (' + conf.toFixed(1) + '%)';
      bannerIcon.textContent = '✅';
    } else if (status === 'DEFECTIVE') {
      predictionCard.classList.add('status-defective');
      statusIcon.innerHTML = ICONS.alert;
      statusTitle.textContent = 'BOTTLE IS DEFECTIVE';
      statusDesc.textContent = 'Flaw detected on bottle body - Divert to collection bin';
      meterFill.style.background = '#ef4444';
      bannerText.textContent = 'Detected: DEFECTIVE (' + conf.toFixed(1) + '%)';
      bannerIcon.textContent = '🚨';
    } else if (status === 'UNCERTAIN') {
      predictionCard.classList.add('status-uncertain');
      statusIcon.innerHTML = ICONS.uncertain;
      statusTitle.textContent = 'UNCERTAIN – PLEASE REPOSITION BOTTLE';
      statusDesc.textContent = 'Low confidence. Align bottle within the green reticle box';
      meterFill.style.background = '#f59e0b';
      bannerText.textContent = 'UNCERTAIN - Please center bottle';
      bannerIcon.textContent = '⚠️';
    } else {
      // NO_BOTTLE
      statusIcon.innerHTML = ICONS.search;
      statusTitle.textContent = 'NO BOTTLE DETECTED';
      statusDesc.textContent = 'Place a Limca bottle in front of the laptop webcam';
      meterFill.style.background = '#6b7280';
      meterFill.style.width = '0%';
      confBadge.textContent = '-- %';
      confValueText.textContent = '0.0%';
      bannerText.textContent = 'Waiting for bottle in frame...';
      bannerIcon.textContent = '🔍';
    }
  }

  function resetPredictionDisplay() {
    predictionCard.classList.remove('status-non-defective', 'status-defective', 'status-uncertain');
    statusIcon.innerHTML = ICONS.search;
    statusTitle.textContent = 'NO BOTTLE DETECTED';
    statusDesc.textContent = 'Position a Limca bottle in front of the laptop webcam';
    confBadge.textContent = '-- %';
    confValueText.textContent = '0.0%';
    meterFill.style.width = '0%';
    probNonDef.textContent = '0.0%';
    probDef.textContent = '0.0%';
  }

  // ==================== MANUAL CAPTURE & ANALYZE ====================

  async function captureAndAnalyze() {
    try {
      btnCapture.disabled = true;
      btnCapture.textContent = 'Analyzing...';

      // Visual flash on container
      const container = document.getElementById('videoContainer');
      container.style.filter = 'brightness(1.5)';
      setTimeout(() => { container.style.filter = 'none'; }, 150);

      const res = await fetch('/api/capture', { method: 'POST' });
      const data = await res.json();

      if (data.success) {
        updateStats(data.stats);
        prependHistoryRow(data.record);
        updatePredictionUI(data.record);
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
        Capture and Analyze`;
    }
  }

  // ==================== STATISTICS & HISTORY ====================

  function updateStats(stats) {
    if (!stats) return;
    statTotal.textContent = stats.total_analyzed;
    statNonDef.textContent = stats.total_non_defective;
    statDef.textContent = stats.total_defective;

    if (stats.total_analyzed > 0) {
      const rate = (stats.total_defective / stats.total_analyzed) * 100;
      statDefectRate.textContent = `${rate.toFixed(1)}%`;
    } else {
      statDefectRate.textContent = '0.0%';
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch('/api/history');
      const data = await res.json();
      updateStats(data.stats);

      if (data.history && data.history.length > 0) {
        historyTableBody.innerHTML = '';
        data.history.forEach(item => prependHistoryRow(item, false));
      }
    } catch (err) {
      console.error('Failed to load history:', err);
    }
  }

  function prependHistoryRow(item, prepend = true) {
    // Remove empty row if present
    const emptyRow = historyTableBody.querySelector('.empty-row');
    if (emptyRow) emptyRow.remove();

    const row = document.createElement('tr');

    let badgeClass = 'badge-gray';
    let icon = '•';
    if (item.status === 'NON-DEFECTIVE') {
      badgeClass = 'badge-green';
      icon = '✓';
    } else if (item.status === 'DEFECTIVE') {
      badgeClass = 'badge-red';
      icon = '⚠';
    } else if (item.status === 'UNCERTAIN') {
      badgeClass = 'badge-amber';
      icon = '?';
    }

    row.innerHTML = `
      <td>
        <img src="${item.image_url}" class="history-thumb" alt="Thumbnail" data-full="${item.image_url}" data-title="${item.id}">
      </td>
      <td><code>${item.id}</code></td>
      <td>${item.timestamp}</td>
      <td>
        <span class="badge ${badgeClass}">${icon} ${item.status}</span>
      </td>
      <td><strong>${item.confidence.toFixed(1)}%</strong></td>
      <td><small style="color: #9ca3af;">${item.hardware_action || 'Logged'}</small></td>
    `;

    // Attach click event for thumbnail zoom
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
    if (!confirm('Are you sure you want to clear the detection history?')) return;
    try {
      await fetch('/api/history', { method: 'DELETE' });
      historyTableBody.innerHTML = `
        <tr class="empty-row">
          <td colspan="6">No bottles analyzed yet. Click <strong>"Capture and Analyze"</strong> to log inspection snapshots.</td>
        </tr>`;
      updateStats({ total_analyzed: 0, total_non_defective: 0, total_defective: 0 });
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
        metricLoss.textContent = pm.test_loss.toFixed(4);
      }
    } catch (err) {
      console.error('Error fetching metrics:', err);
    }
  }

  // ==================== HARDWARE SERIAL ====================

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
    hwToggle.checked = hw.enabled;
    if (hw.enabled) {
      hardwareStatusPill.className = 'status-pill active';
      hardwareStatusPill.querySelector('.pill-text').textContent = hw.connected ? `ESP32: ${hw.port}` : 'ESP32: Simulated';
      hwModeTag.textContent = hw.connected ? `CONNECTED: ${hw.port}` : 'MODE: SIMULATED (MOCK SERIAL)';
    } else {
      hardwareStatusPill.className = 'status-pill';
      hardwareStatusPill.querySelector('.pill-text').textContent = 'ESP32: Standby';
      hwModeTag.textContent = 'MODE: STANDBY (ACTUATION DISABLED)';
    }
  }

  hwToggle.addEventListener('change', async () => {
    try {
      const res = await fetch('/api/hardware/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enable: hwToggle.checked })
      });
      const data = await res.json();
      updateHardwareUI(data);
    } catch (err) {
      console.error(err);
    }
  });

  btnHwTestDefect.addEventListener('click', async () => {
    await fetch('/api/hardware/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'DEFECT' })
    });
    alert('Sent test DEFECT command to diverter servo.');
  });

  btnHwTestPass.addEventListener('click', async () => {
    await fetch('/api/hardware/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'PASS' })
    });
    alert('Sent test PASS command to conveyor motor.');
  });

  // ==================== MODAL ====================

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

  // ==================== EVENT LISTENERS ====================

  btnStartCam.addEventListener('click', startCamera);
  btnStopCam.addEventListener('click', stopCamera);
  btnCapture.addEventListener('click', captureAndAnalyze);
  btnClearHistory.addEventListener('click', clearHistory);
  btnRefreshMetrics.addEventListener('click', loadMetrics);

  // Initial loads
  loadHistory();
  loadMetrics();
  loadHardwareStatus();
});
