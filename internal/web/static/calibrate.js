// Stationary calibration wizard (More → Calibrate, #/calibrate).
// Cabin accel = six-face; cabin gyro = one still dwell; pod mag = six-face.
(function () {
  'use strict';

  const POLL_MS = 200;
  let mountEl = null;
  let pollTimer = null;
  let locking = false;
  let screen = 'home'; // home | prep | face | review

  async function api(path, opts) {
    const res = await fetch('/api/calibrate' + path, opts);
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || res.statusText);
    }
    return res.json();
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPoll() {
    stopPoll();
    pollTimer = setInterval(() => {
      void refresh();
    }, POLL_MS);
  }

  function esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function faceCubeSVG(face) {
    const faces = {
      '+Z': 'top',
      '-Z': 'bottom',
      '+X': 'right',
      '-X': 'left',
      '+Y': 'front',
      '-Y': 'back',
    };
    const hi = faces[face] || '';
    const cls = (name) => (name === hi ? 'cal-cube-face hi' : 'cal-cube-face');
    return (
      `<svg class="cal-cube" viewBox="0 0 120 110" aria-hidden="true">` +
      `<polygon class="${cls('back')}" points="30,20 70,10 100,30 60,40"/>` +
      `<polygon class="${cls('top')}" points="30,20 60,40 60,80 30,60"/>` +
      `<polygon class="${cls('right')}" points="60,40 100,30 100,70 60,80"/>` +
      `<polygon class="${cls('front')}" points="30,60 60,80 60,100 30,80"/>` +
      `<polygon class="${cls('left')}" points="10,50 30,60 30,80 10,70"/>` +
      `<polygon class="${cls('bottom')}" points="60,80 100,70 100,90 60,100"/>` +
      `<text x="60" y="108" text-anchor="middle" class="cal-cube-label">${esc(face)}</text>` +
      `</svg>`
    );
  }

  function metricBar(value, still, faceOK, mode, readyProgress, readyRem, dominance) {
    // mode: 'accel' | 'gyro' | 'mag'
    let label;
    if (mode === 'mag') {
      label = `‖B‖ ${value.toFixed(2)} µT · variance ${still ? 'still' : 'moving'}`;
    } else if (mode === 'gyro') {
      label = `Accel variance gate · ${still ? 'still' : 'moving'} (orientation ignored)`;
    } else {
      label =
        `Off-axis residual ${value.toFixed(3)} m/s²` +
        (dominance ? ` · dominance ${(dominance * 100).toFixed(0)}%` : '') +
        ` (info)`;
    }
    let status = '';
    if (readyProgress > 0) {
      status =
        `<div class="cal-ready-msg">Still — capturing in ${readyRem.toFixed(1)}s</div>` +
        `<div class="cal-ready-bar"><div class="cal-ready-fill" style="width:${Math.round(readyProgress * 100)}%"></div></div>`;
    } else if (still && faceOK) {
      status = `<div class="cal-ready-msg">Still — keep holding</div>`;
    }
    const faceGate =
      mode === 'gyro'
        ? `<span class="ok">Any orientation</span>`
        : `<span class="${faceOK ? 'ok' : ''}">${faceOK ? 'Face detected' : 'Place on a face'}</span>`;
    return (
      `<div class="cal-metric">` +
      `<div class="cal-metric-label">${esc(label)}</div>` +
      `<div class="cal-bar">` +
      `<div class="cal-bar-fill" style="width:${Math.min(100, mode === 'mag' ? (value / Math.max(value * 1.2, 1)) * 100 : (value / Math.max(value, 0.5, 0.4)) * 100)}%"></div>` +
      `</div>` +
      `<div class="cal-gates">` +
      faceGate +
      `<span class="${still ? 'ok' : ''}">${still ? 'Still' : 'Hold still…'}</span>` +
      `</div>${status}</div>`
    );
  }

  function renderHome(st) {
    const cabin = st.history_cabin;
    const pod = st.history_pod;
    let hist = '<p class="dim">No saved calibration yet.</p>';
    if (cabin || pod) {
      hist = '<div class="cal-hist">';
      if (cabin) {
        const aUTC = cabin.accel_fitted_utc || cabin.fitted_utc;
        const gUTC = cabin.gyro_fitted_utc || '';
        hist +=
          `<div><strong>Cabin accel</strong> ${esc(aUTC || '—')}` +
          `<div class="dim">scale [${(cabin.accel_scale || []).map((x) => Number(x).toFixed(4)).join(', ')}] ` +
          `RMS ${Number(cabin.residual_rms_ms2 || 0).toFixed(4)} m/s²` +
          (cabin.accel_offuser_applied || cabin.offuser_applied ? ' · OFFUSER' : '') +
          `</div></div>`;
        hist +=
          `<div><strong>Cabin gyro</strong> ${esc(gUTC || (cabin.gyro_offuser_applied || cabin.offuser_applied ? cabin.fitted_utc : '—'))}` +
          `<div class="dim">` +
          (cabin.gyro_bias
            ? `bias @ ${Number(cabin.temp_cal_c || 0).toFixed(1)} °C` +
              (cabin.gyro_offuser_applied || cabin.offuser_applied ? ' · OFFUSER @ T_ref' : '')
            : 'not calibrated yet') +
          `</div></div>`;
      }
      if (pod) {
        hist +=
          `<div><strong>Pod mag</strong> ${esc(pod.fitted_utc)}` +
          `<div class="dim">‖B‖≈${Number(pod.mean_norm_ut || 0).toFixed(1)} µT ` +
          `RMS ${Number(pod.residual_rms_ut || 0).toFixed(2)} µT</div></div>`;
      }
      hist += '</div>';
    }
    return (
      `<div class="cal-panel">` +
      `<h2>Calibrate</h2>` +
      `<p class="dim">Place-and-hold on a table. Flight DB stays raw; coeffs save to config.</p>` +
      `<div class="cal-actions">` +
      `<button type="button" data-cal-start="cabin_accel">Cabin accel (6-face)</button>` +
      `<button type="button" data-cal-start="cabin_gyro">Cabin gyro (still)</button>` +
      `<button type="button" data-cal-start="pod_mag">Pod magnetometer</button>` +
      `</div>` +
      `<h3>History</h3>${hist}</div>`
    );
  }

  function renderPrep(target) {
    const titles = {
      cabin_accel: 'Cabin accelerometer',
      cabin_gyro: 'Cabin gyro',
      pod_mag: 'Pod magnetometer',
      cabin_imu: 'Cabin accelerometer',
    };
    const title = titles[target] || target;
    let tips;
    if (target === 'pod_mag') {
      tips = `<ul class="cal-tips">
            <li>Place the <strong>pod</strong> on each of six faces (labels on the case), any order.</li>
            <li>Hold still on a table — no hand tipping; green countdown then auto-capture.</li>
            <li>Keep the Pi/cabin fixed.</li>
          </ul>`;
    } else if (target === 'cabin_gyro') {
      tips = `<ul class="cal-tips">
            <li>Place the <strong>cabin case</strong> on the table in <strong>any</strong> stable orientation.</li>
            <li>Do <strong>not</strong> hold or tip — stillness only (accel variance gate).</li>
            <li>When still, a ~30&nbsp;s average captures gyro bias; Accept bakes OFFUSER to T<sub>ref</sub>.</li>
          </ul>`;
    } else {
      tips = `<ul class="cal-tips">
            <li>Level-ish table; place the <strong>cabin case</strong> on each face, any order.</li>
            <li>Do <strong>not</strong> tip or hold in your hands — motion corrupts g₀.</li>
            <li>The app detects which face is down; imperfect case/sensor alignment is OK.</li>
            <li>When still, a countdown runs and averaging starts automatically (~8&nbsp;s/face).</li>
          </ul>`;
    }
    return (
      `<div class="cal-panel">` +
      `<h2>${esc(title)}</h2>` +
      tips +
      `<p class="dim">Raw sensor tables are not rewritten.</p>` +
      `<div class="cal-actions">` +
      `<button type="button" data-cal-begin>Begin</button>` +
      `<button type="button" class="cal-secondary" data-cal-home>Back</button>` +
      `</div></div>`
    );
  }

  function faceChecklist(st) {
    const locked = new Set(st.locked || []);
    const det = (st.seek && st.seek.detected_face) || st.face || '';
    const faces =
      st.target === 'cabin_gyro' ? ['still'] : ['+Z', '-Z', '+X', '-X', '+Y', '-Y'];
    return (
      `<div class="cal-checklist">` +
      faces
        .map((f) => {
          let cls = 'cal-chip';
          if (locked.has(f)) cls += ' done';
          else if (f === det) cls += ' current';
          return `<span class="${cls}">${esc(f)}</span>`;
        })
        .join('') +
      `</div>`
    );
  }

  function renderFace(st) {
    const seek = st.seek || {};
    const isMag = st.target === 'pod_mag';
    const isGyro = st.target === 'cabin_gyro';
    const mode = isMag ? 'mag' : isGyro ? 'gyro' : 'accel';
    const n = (st.locked || []).length;
    const progress = st.phase === 'locking' ? st.lock_progress || 0 : 0;
    const readyP = st.ready_hold_progress || 0;
    const readyRem = st.ready_hold_remaining_s || 0;
    const ready = !!st.can_lock || readyP > 0 || st.phase === 'locking';
    const faceShown = st.face || (seek.face_ok ? seek.detected_face : '?');
    const dur = st.lock_duration_s || (isGyro ? 30 : 8);
    let title;
    if (isMag) title = `Face ${st.face_index + 1} / ${st.faces_total}`;
    else if (isGyro) title = 'Gyro still dwell';
    else title = `${n} / ${st.faces_total} faces`;
    const cube =
      isGyro
        ? `<div class="cal-cube-placeholder">Any orientation — hold still</div>`
        : seek.face_ok || isMag
          ? faceCubeSVG(faceShown)
          : `<div class="cal-cube-placeholder">Place on a face…</div>`;
    const label = isGyro
      ? st.face_label || 'Any stable orientation on the table'
      : seek.already_locked
        ? (seek.detected_face || '') + ' already captured'
        : st.face_label || (seek.face_ok ? 'Detected ' + seek.detected_face : 'No clear face yet');
    return (
      `<div class="cal-panel${ready ? ' cal-panel-ready' : ''}">` +
      `<div class="cal-face-head">` +
      `<h2>${esc(title)}</h2>` +
      `<span class="dim">${isMag ? n + ' locked' : isGyro ? `~${Math.round(dur)} s average` : 'any order'}</span></div>` +
      faceChecklist(st) +
      cube +
      `<p class="cal-face-label">${esc(label)}</p>` +
      `<p class="cal-status-hint">${esc(st.status_hint || '')}</p>` +
      metricBar(
        isMag ? seek.norm || 0 : seek.lateral_ms2 || 0,
        !!seek.still,
        !!seek.face_ok,
        mode,
        readyP,
        readyRem,
        seek.dominance || 0,
      ) +
      (st.phase === 'locking'
        ? (() => {
            const live = st.lock_live || {};
            const mf = Number(live.motion_frac || 0);
            const stillNow = live.still_now !== false;
            let motionMsg = '';
            if (mf > 0.02 || !stillNow) {
              motionMsg =
                `<p class="cal-err">Motion detected — ${(mf * 100).toFixed(0)}% of window not still` +
                (live.peak_gyro_dps != null
                  ? ` · peak ‖ω‖ ${Number(live.peak_gyro_dps).toFixed(1)} °/s`
                  : '') +
                `. Keep going; Accept will warn but still allow save.</p>`;
            } else {
              motionMsg = `<p class="dim">Still — good.</p>`;
            }
            return (
              `<p class="cal-progress">Averaging… ${Math.round(progress * 100)}% — keep holding (~${Math.round(dur)}s)</p>` +
              motionMsg
            );
          })()
        : `<p class="dim cal-hint">${
            isGyro
              ? 'Hands off — capture starts when still.'
              : 'Hands off — capture starts when a face is detected and still.'
          }</p>`) +
      (st.error ? `<p class="cal-err">${esc(st.error)}</p>` : '') +
      `<div class="cal-actions">` +
      `<button type="button" class="cal-secondary" data-cal-cancel ${st.phase === 'locking' ? 'disabled' : ''}>Cancel</button>` +
      `</div></div>`
    );
  }

  function renderReview(st) {
    const isMag = st.target === 'pod_mag';
    const isGyro = st.target === 'cabin_gyro';
    let body = '';
    if (isMag && st.mag_fit) {
      const f = st.mag_fit;
      body =
        `<div class="cal-fit">` +
        `<div>Hard-iron [µT]: [${f.hard_iron_ut.map((x) => x.toFixed(2)).join(', ')}]</div>` +
        `<div>Soft-iron diag: [${f.soft_iron_diag.map((x) => x.toFixed(4)).join(', ')}]</div>` +
        `<div>Mean ‖B_corr‖: ${Number(f.mean_norm_ut).toFixed(2)} µT · RMS ${Number(f.residual_rms_ut).toFixed(3)}</div>` +
        `</div>`;
      if (f.warnings && f.warnings.length) {
        body += `<ul class="cal-warn">${f.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>`;
      }
    } else if (isGyro && st.imu_fit) {
      const f = st.imu_fit;
      const dps = (f.gyro_bias || []).map((x) => (Number(x) * 180) / Math.PI);
      const still = (st.face_samples && st.face_samples.still) || {};
      body =
        `<div class="cal-fit">` +
        `<div>Gyro bias @ ${Number(f.temp_cal_c || 0).toFixed(1)} °C: [${dps.map((x) => x.toFixed(4)).join(', ')}] °/s</div>` +
        (f.gyro_bias_at_ref
          ? `<div>Gyro bias @ T_ref (→ OFFUSER): [${f.gyro_bias_at_ref
              .map((x) => ((Number(x) * 180) / Math.PI).toFixed(4))
              .join(', ')}] °/s</div>`
          : `<div class="dim">Accept bakes OFFUSER to calibration.gyro_tco.t_ref_c via Δb(T).</div>`) +
        `<div class="dim">Samples: ${still.samples || '—'} · ${still.duration_s || st.lock_duration_s || 30}s` +
        (still.motion_frac
          ? ` · motion ${(Number(still.motion_frac) * 100).toFixed(0)}% of window`
          : '') +
        `</div>` +
        (f.gyro_offuser_applied
          ? `<div class="dim">On-chip OFFUSER at T_ref; UI boldface peels Δb(T) only.</div>`
          : '') +
        `</div>`;
      if (f.warnings && f.warnings.length) {
        body += `<ul class="cal-warn">${f.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>`;
      }
    } else if (st.imu_fit) {
      const f = st.imu_fit;
      body =
        `<div class="cal-fit">` +
        `<div>Accel scale (k): [${f.accel_scale.map((x) => x.toFixed(5)).join(', ')}] <span class="dim">(software)</span></div>` +
        `<div>Accel bias (l): [${f.accel_bias.map((x) => x.toFixed(4)).join(', ')}] m/s² → OFFUSER</div>` +
        `<div>Mean ‖a_corr‖: ${Number(f.mean_norm_ms2).toFixed(4)} · RMS ${Number(f.residual_rms_ms2).toFixed(4)} m/s²</div>` +
        (f.accel_offuser_applied || f.offuser_applied
          ? `<div class="dim">On-chip accel OFFUSER programmed (scale stays software).</div>`
          : `<div class="dim">Accept programs accel bias into ICM-45686 calibbias; scale stays software.</div>`) +
        `</div>`;
      if (f.warnings && f.warnings.length) {
        body += `<ul class="cal-warn">${f.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>`;
      }
    }
    const faces = Object.keys(st.face_samples || {}).sort();
    const retake =
      `<div class="cal-retake">` +
      faces
        .map(
          (f) =>
            `<button type="button" class="cal-secondary" data-cal-retake="${esc(f)}">Retake ${esc(f)}</button>`,
        )
        .join('') +
      `</div>`;
    return (
      `<div class="cal-panel">` +
      `<h2>Review</h2>${body}${retake}` +
      `<div class="cal-actions">` +
      `<button type="button" data-cal-save>Accept &amp; save</button>` +
      `<button type="button" class="cal-secondary" data-cal-cancel>Discard</button>` +
      `</div></div>`
    );
  }

  function wire(root) {
    const bind = (sel, fn) => {
      root.querySelectorAll(sel).forEach((el) => {
        if (window.KFTap) KFTap.bindTap(el, fn);
        else el.addEventListener('click', fn);
      });
    };
    bind('[data-cal-start]', async (ev) => {
      const t = ev.currentTarget.getAttribute('data-cal-start');
      screen = 'prep';
      mountEl._prepTarget = t;
      paint(null);
    });
    bind('[data-cal-begin]', async () => {
      try {
        await api('/session', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target: mountEl._prepTarget }),
        });
        screen = 'face';
        startPoll();
        await refresh();
      } catch (e) {
        alert(e.message);
      }
    });
    bind('[data-cal-home]', () => {
      screen = 'home';
      stopPoll();
      void refresh();
    });
    bind('[data-cal-cancel]', async () => {
      stopPoll();
      try {
        await api('/cancel', { method: 'POST' });
      } catch (_) {}
      screen = 'home';
      locking = false;
      await refresh();
    });
    bind('[data-cal-save]', async () => {
      try {
        await api('/save', { method: 'POST' });
        try {
          const cfgR = await fetch('/api/config');
          if (cfgR.ok && typeof state !== 'undefined') {
            state.config = await cfgR.json();
          }
        } catch (_) {}
        screen = 'home';
        await refresh();
      } catch (e) {
        alert(e.message);
      }
    });
    bind('[data-cal-retake]', async (ev) => {
      const face = ev.currentTarget.getAttribute('data-cal-retake');
      try {
        await api('/retake', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ face }),
        });
        screen = 'face';
        startPoll();
        await refresh();
      } catch (e) {
        alert(e.message);
      }
    });
  }

  function paint(st) {
    if (!mountEl) return;
    let html;
    if (screen === 'prep') {
      html = renderPrep(mountEl._prepTarget || 'cabin_accel');
    } else if (!st || (!st.active && screen === 'home')) {
      html = renderHome(st || {});
    } else if (st.phase === 'review' || screen === 'review') {
      screen = 'review';
      html = renderReview(st);
    } else if (st.active) {
      screen = 'face';
      html = renderFace(st);
    } else {
      screen = 'home';
      html = renderHome(st);
    }
    mountEl.innerHTML = html;
    wire(mountEl);
  }

  async function refresh() {
    if (!mountEl) return;
    try {
      const st = await api('/session');
      mountEl._last = st;
      if (st.active && st.phase === 'review') screen = 'review';
      else if (st.active && screen !== 'prep') screen = 'face';
      else if (!st.active && screen !== 'prep') screen = 'home';
      paint(st);
      if (st.active && (st.phase === 'seeking' || st.phase === 'locking') && !pollTimer) startPoll();
      if ((!st.active || st.phase === 'review') && screen !== 'face') stopPoll();
    } catch (e) {
      mountEl.innerHTML = `<p class="cal-err" style="padding:1rem">${esc(e.message)}</p>`;
    }
  }

  async function show(mount) {
    const resume = mountEl === mount;
    mountEl = mount;
    if (!resume) {
      screen = 'home';
      locking = false;
    }
    await refresh();
  }

  function hide() {
    stopPoll();
    mountEl = null;
    screen = 'home';
    locking = false;
  }

  window.KFCalibrate = { show, hide };
})();
