// Kingfisher UI — mobile-first overview + sensor detail, hash routing.

const state = {
  devices: new Map(),
  deviceNames: new Set(),
  activeSensor: null,
  routeView: 'sensors',
  attrs: new Map(),
  deviceLocation: new Map(),
  iioDevices: new Set(),
  podLink: null,
  clock: null,
  serverConnected: false,
  paused: false,
  config: null,
};

const viewOverviewEl = document.getElementById('viewOverview');
const viewDetailEl = document.getElementById('viewDetail');
const viewInstrumentsEl = document.getElementById('viewInstruments');
const detailPanelEl = document.getElementById('detailPanel');
const detailTitleEl = document.getElementById('detailTitle');
const detailBackEl = document.getElementById('detailBack');
const statusChipsEl = document.getElementById('statusChips');
const hdrTailEl = document.getElementById('hdrTail');
const recDotEl = document.getElementById('recDot');
const recSizeEl = document.getElementById('recSize');
const recLabelEl = document.getElementById('recLabel');
const recBlockEl = document.querySelector('#hdr .rec');
const pauseBtn = document.getElementById('pauseBtn');
const menuBtn = document.getElementById('menuBtn');
const moreSheet = document.getElementById('moreSheet');
const moreBufStatEl = document.getElementById('moreBufStat');
const statusDrawer = document.getElementById('statusDrawer');
const statusDrawerClock = document.getElementById('statusDrawerClock');
const statusDrawerPod = document.getElementById('statusDrawerPod');
const settingsDlg = document.getElementById('settingsDlg');

const DERIVED_DEVICES = ['ahrs', 'press_alt', 'compass', 'airspeed'];
const CALC_DEVICES = new Set([...DERIVED_DEVICES, 'geo']);
const POD_TELEMETRY_DEVICES = new Set(['bmp581', 'mmc5983', 'ms4525', 'bq27441']);
const SKIP_ATTRS_FETCH = new Set(['compass', 'ahrs', 'geo', 'press_alt', 'gps', 'airspeed']);

function isPodTelemetry(name) {
  return deviceTabGroup(name) === 'pod';
}

function inferDeviceLocation(name) {
  if (CALC_DEVICES.has(name)) return 'calc';
  if (name === 'gps') return 'hub';
  if (POD_TELEMETRY_DEVICES.has(name)) return 'pod';
  if (state.iioDevices.has(name)) return 'hub';
  return null;
}

function deviceTabGroup(name) {
  const loc = state.deviceLocation.get(name) || inferDeviceLocation(name);
  return loc || 'hub';
}

function applyDeviceLocation(name, loc) {
  if (!loc) return;
  if (CALC_DEVICES.has(name)) loc = 'calc';
  const prev = state.deviceLocation.get(name);
  if (prev === loc) return;
  state.deviceLocation.set(name, loc);
}

function ensureDevice(name) {
  if (name === 'pod') return;
  if (state.deviceNames.has(name)) return;
  state.deviceNames.add(name);
  const loc = inferDeviceLocation(name);
  if (loc) applyDeviceLocation(name, loc);
}

function parseRoute() {
  const raw = (location.hash || '#/').replace(/^#/, '');
  const path = raw.startsWith('/') ? raw : '/' + raw;
  if (path === '/instruments') return { view: 'instruments', sensor: null };
  const m = path.match(/^\/sensor\/(.+)$/);
  if (m) return { view: 'sensors', sensor: decodeURIComponent(m[1]) };
  return { view: 'sensors', sensor: null };
}

function applyRoute() {
  const r = parseRoute();
  state.routeView = r.view;
  state.activeSensor = r.sensor;

  const onSensors = r.view === 'sensors';
  const onDetail = onSensors && r.sensor;
  viewOverviewEl.classList.toggle('view-active', onSensors && !onDetail);
  viewDetailEl.classList.toggle('view-active', !!onDetail);
  viewInstrumentsEl.classList.toggle('view-active', r.view === 'instruments');

  for (const btn of document.querySelectorAll('#bottomNav .bottomNavBtn')) {
    const nav = btn.dataset.nav;
    let active = false;
    if (nav === 'sensors') active = onSensors;
    else if (nav === 'instruments') active = r.view === 'instruments';
    btn.classList.toggle('active', active);
  }

  if (onDetail) {
    openSensorDetail(r.sensor);
  } else if (onSensors) {
    renderOverview();
  } else if (r.view === 'instruments') {
    renderInstruments();
  }
}

function renderInstruments() {
  const mount = document.getElementById('pfdMount');
  if (!mount) return;
  KFPFD.renderPanel(mount, {
    ahrs: state.devices.get('ahrs'),
    compass: state.devices.get('compass'),
    airspeed: state.devices.get('airspeed'),
    pressAlt: state.devices.get('press_alt'),
    gps: state.devices.get('gps'),
  });
}

function renderOverview() {
  KFOverview.render(viewOverviewEl, (name) => state.devices.get(name));
  // The render above rewrites innerHTML, wiping any kf-stale classes the
  // 1 Hz markStaleness pass applied. Re-apply in the same frame so stale
  // tiles stay dimmed instead of flickering back to full brightness on
  // every ~10 Hz WS tick.
  markStaleness();
}

let panelRegions = null;

function rebuildDetailPanel() {
  detailPanelEl.innerHTML =
    '<div id="liveKV"></div><div class="detailHistoryStub dim">History charts — coming soon</div><div id="attrsBox"></div>';
  panelRegions = {
    kv: document.getElementById('liveKV'),
    attrs: document.getElementById('attrsBox'),
  };
  panelRegions.attrs.removeAttribute('data-compass-wired');
  panelRegions.attrs.removeAttribute('data-airspeed-wired');
  return panelRegions;
}

function openSensorDetail(name) {
  if (!state.deviceNames.has(name)) {
    location.hash = '#/';
    return;
  }
  const loc = state.deviceLocation.get(name) || inferDeviceLocation(name) || '';
  const label = KFDisplay.overviewDeviceName(name);
  detailTitleEl.textContent = loc ? `${label} (${loc})` : label;
  rebuildDetailPanel();
  // Tag the (persistent) detail container with the source device whose
  // freshness should dim this view, so markStaleness covers the in-flight
  // glance tabs (compass/airspeed/any sensor), not just overview tiles.
  // compass/airspeed are multi-source panels keyed to their derived device.
  detailPanelEl.dataset.device = name;
  if (name === 'compass') {
    if (!state.attrs.has('compass')) state.attrs.set('compass', []);
  } else if (name === 'airspeed') {
    if (!state.attrs.has('airspeed')) state.attrs.set('airspeed', []);
  } else if (!SKIP_ATTRS_FETCH.has(name) &&
    (isPodTelemetry(name) || state.iioDevices.has(name) || !state.attrs.has(name))) {
    loadAttrs(name);
  }
  renderActiveSensor();
  renderAttrs();
}

function renderLiveValues() {
  const name = state.activeSensor;
  if (!name || !panelRegions) return;
  const sm = state.devices.get(name);
  if (!sm) { panelRegions.kv.innerHTML = ''; return; }
  const vals = sm.values || {};
  let html = '';
  const keys = KFDisplay.sortKeys(name, Object.keys(vals));
  for (const k of keys) {
    const out = KFDisplay.formatValue(name, k, vals[k], vals);
    const vCell = out.html ?? escapeHtml(String(out.text ?? ''));
    const label = escapeHtml(KFDisplay.channelLabel(name, k));
    const rowCls = KFDisplay.rowClass(name, k);
    html += `<div class="kv${rowCls}"><div class="k">${label}</div><div class="v">${vCell}</div></div>`;
  }
  html += KFDisplay.gpsFootnote(name);
  if (name === 'bq27441') {
    html = KFDisplay.bq27441Footnote(vals) + html;
  }
  panelRegions.kv.innerHTML = html;
}

function renderAttrs() {
  const name = state.activeSensor;
  if (!name || !panelRegions) {
    return;
  }
  const attrs = state.attrs.get(name);
  const loc = state.deviceLocation.get(name);
  const locLine = loc ? `<div class="dim">location: ${escapeHtml(loc)}</div>` : '';
  let html = `<section class="attrs"><h3>Settings</h3>${locLine}`;
  if (name === 'compass') {
    html += renderCompassSettings();
    panelRegions.attrs.removeAttribute('data-compass-wired');
    panelRegions.attrs.innerHTML = html + `</section>`;
    wireCompassSettings();
    return;
  }
  if (name === 'airspeed') {
    html += renderAirspeedSettings();
    panelRegions.attrs.removeAttribute('data-airspeed-wired');
    panelRegions.attrs.innerHTML = html + `</section>`;
    wireAirspeedSettings();
    return;
  }
  if (!attrs) {
    html += `<div class="dim">(loading…)</div>`;
  } else if (attrs.length === 0) {
    html += `<div class="dim">(no editable attributes)</div>`;
  } else {
    for (const a of attrs) {
      const label = attrLabel(name, a);
      html += `<div class="attrRow"><div class="k">${label}</div><div class="v">${renderAttrInput(a)}</div></div>`;
    }
  }
  html += `</section>`;
  panelRegions.attrs.innerHTML = html;
  wireAttrEdits(name);
}

function magDeviceOptions() {
  const names = [...state.deviceNames].filter((n) => {
    if (DERIVED_DEVICES.includes(n) || n === 'gps' || n === 'geo' || n === 'pod') return false;
    const sm = state.devices.get(n);
    if (!sm?.values) return false;
    const keys = Object.keys(sm.values);
    return keys.some((k) => /^magn_[xyz]$/.test(k) || /^mag_[xyz]_ut$/.test(k));
  });
  return names.sort();
}

function deviceHasAccelInState(name) {
  if (!name) return false;
  const v = state.devices.get(name)?.values;
  if (!v) return false;
  return v.accel_x != null && v.accel_y != null && v.accel_z != null;
}

function compassAlignMethodEffective() {
  const cfg = state.config?.compass ?? {};
  const sel = document.getElementById('compassAlignMethod');
  const fromSel = sel?.value ?? '';
  if (fromSel === 'wmm' || fromSel === 'accel') return fromSel;
  if (cfg.align_method === 'wmm' || cfg.align_method === 'accel') return cfg.align_method;
  const mag = cfg.magnetometer ?? '';
  if (mag && !deviceHasAccelInState(mag)) return 'wmm';
  return 'accel';
}

function compassAlignReadinessText(method) {
  if (method === 'wmm') {
    return 'WMM align needs GPS fix, geo field, and cabin AHRS or accel.';
  }
  return 'Accel align needs mag and accel on the same device (or set accel_device).';
}

function compassMountSummary() {
  const cfg = state.config?.compass ?? {};
  const mag = cfg.magnetometer ?? '';
  if (!mag) return 'Sensor mount: auto (no fixed magnetometer selected)';
  const map = cfg.sensor_mount_r ?? {};
  if (map && map[mag]) return `Sensor mount (${mag}): from config sensor_mount_r`;
  if (/^mmc5983/i.test(mag)) return `Sensor mount (${mag}): default provisional mmc5983 (z inverted)`;
  return `Sensor mount (${mag}): default identity`;
}

function renderCompassSettings() {
  const cfg = state.config?.compass ?? {};
  const cur = cfg.magnetometer ?? '';
  const alignCur = cfg.align_method ?? '';
  const opts = magDeviceOptions();
  let sel = `<option value="">(auto)</option>`;
  for (const n of opts) {
    sel += `<option value="${escapeAttr(n)}"${n === cur ? ' selected' : ''}>${escapeHtml(n)}</option>`;
  }
  const alignSel =
    `<option value=""${alignCur === '' ? ' selected' : ''}>(auto)</option>` +
    `<option value="wmm"${alignCur === 'wmm' ? ' selected' : ''}>WMM field + attitude</option>` +
    `<option value="accel"${alignCur === 'accel' ? ' selected' : ''}>Gravity + mag (same IMU)</option>`;
  const method = compassAlignMethodEffective();
  return (
    `<div class="attrRow"><div class="k">Magnetometer</div>` +
    `<div class="v"><select id="compassMagSel">${sel}</select></div></div>` +
    `<div class="attrRow"><div class="k">Align method</div>` +
    `<div class="v"><select id="compassAlignMethod">${alignSel}</select></div></div>` +
    `<div class="attrRow"><div class="k">Align</div><div class="v">` +
    `<button type="button" id="compassAlignGps">GPS track (taxi)</button> ` +
    `<button type="button" id="compassAlignManual">Manual °M</button> ` +
    `<input id="compassManualHdg" type="number" step="0.1" min="-180" max="180" placeholder="°M" style="width:5em"/>` +
    `</div></div>` +
    `<div id="compassAlignHint" class="dim">${escapeHtml(compassAlignReadinessText(method))}</div>` +
    `<div id="compassMountHint" class="dim">${escapeHtml(compassMountSummary())}</div>` +
    `<div id="compassAlignMsg" class="dim"></div>`
  );
}

function wireCompassSettings() {
  if (panelRegions.attrs.dataset.compassWired === '1') return;
  panelRegions.attrs.dataset.compassWired = '1';
  const saveCompassCfg = async () => {
    await loadConfig();
    const cfg = state.config ?? {};
    cfg.compass = cfg.compass ?? { enabled: true };
    const magSel = document.getElementById('compassMagSel');
    const alignSel = document.getElementById('compassAlignMethod');
    if (magSel) cfg.compass.magnetometer = magSel.value;
    if (alignSel) cfg.compass.align_method = alignSel.value;
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    if (r.ok) state.config = await r.json();
    const hint = document.getElementById('compassAlignHint');
    if (hint) hint.textContent = compassAlignReadinessText(compassAlignMethodEffective());
    const mountHint = document.getElementById('compassMountHint');
    if (mountHint) mountHint.textContent = compassMountSummary();
  };
  const sel = document.getElementById('compassMagSel');
  if (sel) sel.addEventListener('change', saveCompassCfg);
  const alignMethodSel = document.getElementById('compassAlignMethod');
  if (alignMethodSel) alignMethodSel.addEventListener('change', saveCompassCfg);
  const gpsBtn = document.getElementById('compassAlignGps');
  const manBtn = document.getElementById('compassAlignManual');
  const msg = document.getElementById('compassAlignMsg');
  const doAlign = async (body) => {
    if (msg) msg.textContent = 'Aligning…';
    const payload = { ...body, align_method: compassAlignMethodEffective() };
    try {
      const r = await fetch('/api/compass/align', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const t = await r.text();
      if (!r.ok) throw new Error(t || r.statusText);
      if (msg) msg.textContent = 'Alignment saved.';
    } catch (e) {
      if (msg) msg.textContent = String(e.message || e);
    }
  };
  if (gpsBtn) {
    gpsBtn.addEventListener('click', () => doAlign({ manual_heading_deg: null }));
  }
  if (manBtn) {
    manBtn.addEventListener('click', () => {
      const inp = document.getElementById('compassManualHdg');
      const v = inp ? Number(inp.value) : NaN;
      if (!Number.isFinite(v)) {
        if (msg) msg.textContent = 'Enter a magnetic heading in degrees.';
        return;
      }
      doAlign({ manual_heading_deg: v });
    });
  }
}

function airspeedCfg() {
  return state.config?.airspeed ?? {};
}

function renderAirspeedSettings() {
  const cfg = airspeedCfg();
  const zero = cfg.dp_zero_pa ?? 0;
  const floor = cfg.low_speed_floor_kt ?? 5;
  const emaOn = cfg.ema_enabled !== false;
  const tau = cfg.ema_tau_s ?? 0.5;
  return (
    `<div class="kv"><div class="k">Zero offset (Pa)</div>` +
    `<div class="v"><input id="airspeedDpZero" type="number" step="0.01" value="${escapeAttr(zero)}" style="width:7em"/> ` +
    `<button type="button" id="airspeedZeroBtn">Zero now (15 s avg)</button></div></div>` +
    `<div class="kv"><div class="k">Low-speed floor (kt)</div>` +
    `<div class="v"><input id="airspeedFloor" type="number" step="0.5" min="0" value="${escapeAttr(floor)}" style="width:5em"/></div></div>` +
    `<div class="kv"><div class="k">EMA filter</div>` +
    `<div class="v"><label><input id="airspeedEmaEnabled" type="checkbox"${emaOn ? ' checked' : ''}/> Enable</label></div></div>` +
    `<div class="kv"><div class="k">EMA τ (s)</div>` +
    `<div class="v"><input id="airspeedEmaTau" type="number" step="0.05" min="0.05" value="${escapeAttr(tau)}" style="width:5em"/></div></div>` +
    `<div id="airspeedSettingsMsg" class="dim"></div>`
  );
}

async function saveAirspeedSettings() {
  const msg = document.getElementById('airspeedSettingsMsg');
  if (!state.config) await loadConfig();
  const cfg = state.config ?? {};
  cfg.airspeed = cfg.airspeed ?? {};
  const zeroInp = document.getElementById('airspeedDpZero');
  const floorInp = document.getElementById('airspeedFloor');
  const emaChk = document.getElementById('airspeedEmaEnabled');
  const tauInp = document.getElementById('airspeedEmaTau');
  if (zeroInp) cfg.airspeed.dp_zero_pa = Number(zeroInp.value) || 0;
  if (floorInp) cfg.airspeed.low_speed_floor_kt = Number(floorInp.value);
  if (emaChk) cfg.airspeed.ema_enabled = emaChk.checked;
  if (tauInp) cfg.airspeed.ema_tau_s = Number(tauInp.value) || 0.5;
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    const t = await r.text();
    if (!r.ok) throw new Error(t || r.statusText);
    state.config = cfg;
    if (msg) msg.textContent = 'Settings saved.';
  } catch (e) {
    if (msg) msg.textContent = String(e.message || e);
  }
}

function wireAirspeedSettings() {
  if (panelRegions.attrs.dataset.airspeedWired === '1') return;
  panelRegions.attrs.dataset.airspeedWired = '1';
  const zeroBtn = document.getElementById('airspeedZeroBtn');
  const msg = document.getElementById('airspeedSettingsMsg');
  const saveDebounced = debounce(() => saveAirspeedSettings(), 400);
  for (const id of ['airspeedDpZero', 'airspeedFloor', 'airspeedEmaTau']) {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', saveDebounced);
  }
  const emaChk = document.getElementById('airspeedEmaEnabled');
  if (emaChk) emaChk.addEventListener('change', saveDebounced);
  if (zeroBtn) {
    zeroBtn.addEventListener('click', async () => {
      const durationS = 15;
      if (msg) msg.textContent = `Sampling… ${durationS}s remaining`;
      zeroBtn.disabled = true;
      let left = durationS;
      const tick = setInterval(() => {
        left -= 1;
        if (left > 0 && msg) msg.textContent = `Sampling… ${left}s remaining`;
      }, 1000);
      try {
        const r = await fetch('/api/airspeed/zero', {
          method: 'POST',
          signal: AbortSignal.timeout((durationS + 10) * 1000),
        });
        const t = await r.text();
        let body = {};
        try { body = JSON.parse(t); } catch { /* plain-text error */ }
        if (!r.ok) throw new Error(body.error || t || r.statusText);
        const cfgR = await fetch('/api/config');
        state.config = await cfgR.json();
        const zeroInp = document.getElementById('airspeedDpZero');
        if (zeroInp && body.dp_zero_pa != null) {
          zeroInp.value = Number(body.dp_zero_pa).toFixed(2);
        }
        const n = body.samples != null ? `, ${body.samples} samples` : '';
        if (msg) msg.textContent = `Zero saved (${Number(body.dp_zero_pa).toFixed(2)} Pa${n}).`;
      } catch (e) {
        if (msg) msg.textContent = String(e.message || e);
      } finally {
        clearInterval(tick);
        zeroBtn.disabled = false;
      }
    });
  }
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function attrLabel(device, a) {
  if (device === 'press_alt' && !a.channel && a.attr === 'kollsman_inhg') {
    return 'Kollsman (inHg)';
  }
  if (device === 'bq27441' && !a.channel && a.attr === 'design_capacity_mah') {
    return 'Design capacity (mAh)';
  }
  return a.channel ? `${a.channel} ${a.attr}` : a.attr;
}

function renderAttrInput(a) {
  if (!a.writable) {
    return `<span class="ro">${escapeHtml(a.value)}</span>`;
  }
  const dataAttrs = `data-channel="${escapeAttr(a.channel)}" data-attr="${escapeAttr(a.attr)}"`;
  if (Array.isArray(a.options) && a.options.length > 0) {
    let opts = '';
    let matched = false;
    for (const opt of a.options) {
      const sel = nearlyEqual(opt, a.value);
      if (sel) matched = true;
      opts += `<option value="${escapeAttr(opt)}"${sel ? ' selected' : ''}>${escapeHtml(opt)}</option>`;
    }
    if (!matched) {
      opts = `<option value="${escapeAttr(a.value)}" selected>${escapeHtml(a.value)} (current)</option>` + opts;
    }
    return `<select ${dataAttrs}>${opts}</select>`;
  }
  return `<input ${dataAttrs} value="${escapeAttr(a.value)}">`;
}

function nearlyEqual(opt, val) {
  if (opt === val) return true;
  const a = parseFloat(opt);
  const b = parseFloat(val);
  if (!isFinite(a) || !isFinite(b)) return false;
  return Math.abs(a - b) < 1e-12 * Math.max(1, Math.abs(a));
}

function podLinkLabel(pod) {
  if (!pod || !pod.enabled) return 'Pod ingest off';
  if (pod.power_mode === 'sleeping') return `Sleeping (${pod.sleep_reason || 'battery'})`;
  if (pod.power_mode === 'sleep_pending') return `Sleep pending (${pod.sleep_reason || 'battery'})`;
  if (!pod.connected) return 'No recent pod traffic';
  if ((pod.rx_dropped || 0) > 0) return 'Link up (batch gaps)';
  return 'Link OK';
}

function formatRssi(dbm) {
  if (dbm == null || !Number.isFinite(dbm)) return '—';
  return `${dbm} dBm`;
}

function rssiClass(dbm) {
  if (!Number.isFinite(dbm)) return '';
  if (dbm >= -65) return 'rssi-good';
  if (dbm >= -78) return 'rssi-warn';
  return 'rssi-bad';
}

function battSocClass(soc) {
  if (!Number.isFinite(soc)) return '';
  if (soc < 10) return 'batt-bad';
  if (soc < 20) return 'batt-warn';
  return '';
}

function formatBatteryHeadline(p) {
  if (p.has_battery_telemetry) {
    // For the compact chip a pilot glances at: SOC + estimated time
    // remaining is the dispatch-relevant pair. Voltage is in the full
    // panel for diagnostics.
    const parts = [
      `${Number(p.battery_v).toFixed(2)} V`,
      p.battery_gauge_learned ? `${Math.round(p.battery_soc_pct)}%` : null,
      p.battery_gauge_learned ? formatBatteryTimeRemainCompact(p.battery_time_remain_s) : null,
    ].filter(Boolean);
    return parts.join(' ');
  }
  if (p.has_battery) {
    return `${Number(p.battery_v).toFixed(2)} V`;
  }
  return '—';
}

function formatBatteryTimeRemainCompact(s) {
  if (!Number.isFinite(s) || s <= 0) return null;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `~${h}h${String(m).padStart(2, '0')}`;
  return `~${m}m`;
}

function formatBatteryHeadlineFull(p) {
  if (p.has_battery_telemetry) {
    const parts = [
      `${Number(p.battery_v).toFixed(2)} V`,
      p.battery_gauge_learned ? `${Math.round(p.battery_soc_pct)}%` : null,
      formatBatteryCurrent(p.battery_current_a),
      formatBatteryPower(p.battery_power_w),
      p.battery_gauge_learned ? formatBatteryCapacity(p.battery_capacity_remain_mah) : null,
      p.battery_gauge_learned ? formatBatteryTimeRemain(p.battery_time_remain_s) : null,
    ].filter(Boolean);
    return parts.join(' · ');
  }
  if (p.has_battery) {
    return `${Number(p.battery_v).toFixed(2)} V`;
  }
  return '—';
}

function formatBatteryCurrent(a) {
  if (!Number.isFinite(a)) return '—';
  const ma = a * 1000;
  const sign = ma >= 0 ? '+' : '−';
  return `${sign}${Math.abs(ma).toFixed(0)} mA`;
}

function formatBatteryPower(w) {
  if (!Number.isFinite(w)) return '—';
  return `${w.toFixed(2)} W`;
}

function formatBatteryCapacity(mah) {
  if (!Number.isFinite(mah)) return '—';
  return `${Math.round(mah)} mAh`;
}

function formatBatteryTimeRemain(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '—';
  if (sec >= 3600) return `${(sec / 3600).toFixed(1)} h`;
  if (sec >= 60) return `${Math.round(sec / 60)} m`;
  return `${Math.round(sec)} s`;
}

function formatTimeOffsetNs(ns) {
  if (!Number.isFinite(ns)) return '—';
  const sign = ns >= 0 ? '+' : '−';
  const abs = Math.abs(ns);
  if (abs < 1000) return `${sign}${abs.toFixed(0)} ns`;
  if (abs < 1e6) return `${sign}${(abs / 1000).toFixed(1)} µs`;
  if (abs < 1e9) return `${sign}${(abs / 1e6).toFixed(2)} ms`;
  return `${sign}${(abs / 1e9).toFixed(2)} s`;
}

function formatClockOffsetMs(ms) {
  if (!Number.isFinite(ms)) return '—';
  return formatTimeOffsetNs(ms * 1e6);
}

function formatAgeSeconds(sec) {
  if (!Number.isFinite(sec)) return '—';
  if (sec >= 10) return `${sec.toFixed(0)} s ago`;
  if (sec >= 1) return `${sec.toFixed(1)} s ago`;
  return `${sec.toFixed(2)} s ago`;
}

function disciplineSourceLabel(d) {
  if (!d) return '—';
  if (d.source_label) return d.source_label;
  switch (d.source) {
    case 'pps': return 'PPS';
    case 'gps': return 'GPS';
    case 'ntp': return 'NTP';
    case 'local': return 'Local';
    default: return 'Unknown';
  }
}

function clockBadgeClass(clock) {
  const d = clock?.discipline;
  const g = clock?.gps_check;
  if (d?.available && d.synced) {
    if (d.pps_steering) return 'ok';
    if (d.source === 'gps') return 'warn';
    if (d.source === 'ntp') return 'warn';
    return 'ok';
  }
  if (g?.state === 'waiting_for_fix') return 'off';
  if (g?.state === 'stale_fix' || g?.state === 'offset_high') return 'warn';
  if (g?.disciplined) return 'ok';
  return 'warn';
}

function clockUnsyncedDetail(d, resync) {
  const parts = [];
  if (d.gps_state === 'error' && Number.isFinite(d.gps_offset_ms)) {
    parts.push(`GPS refclock error (${formatOffsetMs(d.gps_offset_ms)})`);
  } else if (d.gps_state === 'error') {
    parts.push('GPS refclock error');
  }
  if (d.pps_present && !d.pps_steering) {
    if (d.pps_state === 'error') {
      parts.push('PPS idle (lock GPS)');
    } else if (d.pps_state === 'unreachable') {
      parts.push('PPS unreachable');
    } else if (d.pps_present) {
      parts.push('PPS wired, not steering');
    }
  }
  if (d.gps_state === 'error' && Math.abs(d.gps_offset_ms || 0) > 200) {
    parts.push('Use Restart time services to auto-correct GPS offset (requires Pi setup)');
  }
  if (resync?.last_result && resync.last_result.includes('error')) {
    const err = resync.last_result.split(':error:').pop();
    if (err) parts.push(`Last retry failed: ${err}`);
  }
  return parts;
}

function formatOffsetMs(ms) {
  if (!Number.isFinite(ms)) return '—';
  const sign = ms >= 0 ? '+' : '';
  if (Math.abs(ms) >= 1) return `${sign}${ms.toFixed(0)} ms`;
  return `${sign}${ms.toFixed(2)} ms`;
}

function formatResyncAutoLine(resync) {
  if (!resync?.auto_enabled) return '';
  if (Number.isFinite(resync.next_auto_eligible_s) && resync.next_auto_eligible_s > 0) {
    const mins = Math.ceil(resync.next_auto_eligible_s / 60);
    return `Auto-retry in ${mins} min`;
  }
  if (resync.last_attempt_utc) {
    if (resync.last_result && resync.last_result.includes('synced')) {
      return 'Auto-retry succeeded';
    }
    return 'Auto-retry attempted recently';
  }
  return 'Auto-retry enabled';
}

let clockResyncBusy = false;

async function clockResync(level) {
  if (clockResyncBusy) return null;
  clockResyncBusy = true;
  try {
    const r = await fetch('/api/clock/resync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level }),
    });
    const ct = r.headers.get('content-type') || '';
    let body = {};
    if (ct.includes('application/json')) {
      body = await r.json().catch(() => ({}));
    } else if (!r.ok) {
      throw new Error((await r.text().catch(() => '')) || r.statusText);
    }
    if (!r.ok) {
      throw new Error(body.error || r.statusText);
    }
    if (body.clock) state.clock = body.clock;
    else if (body.discipline && state.clock) {
      state.clock.discipline = body.discipline;
    }
    await refreshStatus();
    return body;
  } finally {
    clockResyncBusy = false;
    renderClockStatusFull(statusDrawerClock);
    renderStatusChips();
  }
}

function wireClockResyncButtons(root) {
  if (!root) return;
  const retryBtn = root.querySelector('[data-clock-resync="light"]');
  const fullBtn = root.querySelector('[data-clock-resync="full"]');
  if (retryBtn) {
    retryBtn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      retryBtn.disabled = true;
      try {
        await clockResync('light');
      } catch (e) {
        alert(String(e.message || e));
      }
    });
  }
  if (fullBtn) {
    fullBtn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      const ok = confirm(
        'Restart chronyd and gpsd? If GPS offset is far off, the offset will be auto-corrected first. Discipline may pause for a few seconds.'
      );
      if (!ok) return;
      fullBtn.disabled = true;
      try {
        await clockResync('full');
      } catch (e) {
        alert(String(e.message || e));
      }
    });
  }
}

function renderClockStatusFull(el) {
  if (!el) return;
  if (!state.serverConnected) {
    el.className = 'clockStatus clockStatus-offline';
    el.innerHTML = '<span class="clockStatusItem"><span class="lbl">Pi time</span> server offline</span>';
    el.title = 'No connection to kingfisher';
    return;
  }
  const c = state.clock;
  if (!c) {
    el.innerHTML = '';
    return;
  }
  el.className = `clockStatus clockStatus-${clockBadgeClass(c)}`;
  const d = c.discipline || {};
  const g = c.gps_check || {};
  const parts = [];
  if (d.available && d.synced) {
    const src = disciplineSourceLabel(d);
    parts.push(`<span class="clockStatusItem"><span class="lbl">Pi time</span> <span class="clockSource clockSource-${escapeAttr(d.source || 'unknown')}">${escapeHtml(src)}</span> synced</span>`);
    if (Number.isFinite(d.last_offset_ns)) {
      parts.push(`<span class="clockStatusItem"><span class="lbl">Correction</span> ${escapeHtml(formatTimeOffsetNs(d.last_offset_ns))}</span>`);
    }
  } else if (d.available) {
    const resync = c.resync || {};
    parts.push('<span class="clockStatusItem"><span class="lbl">Pi time</span> not synced</span>');
    for (const line of clockUnsyncedDetail(d, resync)) {
      parts.push(`<span class="clockStatusItem clockWarn"><span class="lbl">Cause</span> ${escapeHtml(line)}</span>`);
    }
    const autoLine = formatResyncAutoLine(resync);
    if (autoLine) {
      parts.push(`<span class="clockStatusItem dim">${escapeHtml(autoLine)}</span>`);
    }
    const btnDisabled = clockResyncBusy ? ' disabled' : '';
    parts.push(
      '<span class="clockResyncActions">' +
      `<button type="button" class="clockResyncBtn" data-clock-resync="light"${btnDisabled}>Retry sync</button>`
    );
    if (resync.full_available) {
      parts.push(
        `<button type="button" class="clockResyncBtn clockResyncBtn-warn" data-clock-resync="full"${btnDisabled}>Restart time services</button>`
      );
    }
    parts.push('</span>');
  } else if (g.has_fix) {
    parts.push('<span class="clockStatusItem"><span class="lbl">Pi time</span> GPS fix only</span>');
  } else {
    parts.push('<span class="clockStatusItem"><span class="lbl">Pi time</span> no GPS fix</span>');
  }
  if (g.has_fix && Number.isFinite(g.fix_age_s)) {
    const stale = g.fresh === false;
    parts.push(`<span class="clockStatusItem${stale ? ' clockWarn' : ''}"><span class="lbl">GPS data</span> ${escapeHtml(formatAgeSeconds(g.fix_age_s))}</span>`);
  }
  el.title = c.detail || c.startup_reason || '';
  el.innerHTML = parts.join('');
  wireClockResyncButtons(el);
}

function renderPodStatusFull(el) {
  if (!el) return;
  if (!state.serverConnected) {
    el.className = 'podStatus podStatus-offline';
    el.innerHTML = '<span class="dim">Server offline</span>';
    el.title = 'No connection to kingfisher';
    return;
  }
  const p = state.podLink;
  if (!p || !p.enabled) {
    el.innerHTML = '<span class="dim">Pod ingest disabled</span>';
    return;
  }
  const dropped = (p.rx_dropped || 0) + (p.dropped_readings || 0) + (p.ts_clamped || 0);
  const linkCls = !p.connected ? 'off' : (dropped > 0 ? 'warn' : 'ok');
  let rssiText = '—';
  let rssiCls = '';
  if (p.has_rssi) {
    rssiText = formatRssi(p.rssi_dbm);
    rssiCls = rssiClass(p.rssi_dbm);
  }
  const battText = formatBatteryHeadlineFull(p);
  const battCls = p.has_battery_telemetry && p.battery_gauge_learned
    ? battSocClass(p.battery_soc_pct)
    : '';
  el.className = `podStatus podStatus-${linkCls}`;
  const droppedReadCls = (p.dropped_readings || 0) > 0 ? 'warn' : '';
  const tsClampCls = (p.ts_clamped || 0) > 0 ? 'warn' : '';
  el.innerHTML =
    `<span class="podStatusItem"><span class="lbl">Pod</span> ${escapeHtml(podLinkLabel(p))}</span>` +
    `<span class="podStatusItem ${rssiCls}"><span class="lbl">RSSI</span> ${escapeHtml(rssiText)}</span>` +
    `<span class="podStatusItem ${battCls}"><span class="lbl">Batt</span> ${escapeHtml(battText)}</span>` +
    `<span class="podStatusItem"><span class="lbl">Buf</span> ${escapeHtml(String(p.buffer_depth ?? '—'))}</span>` +
    `<span class="podStatusItem ${droppedReadCls}" title="Pod sensor buffer overruns"><span class="lbl">Drop</span> ${escapeHtml(String(p.dropped_readings ?? 0))}</span>` +
    `<span class="podStatusItem ${tsClampCls}" title="Pod readings whose timestamp was clamped to recv time"><span class="lbl">TsClamp</span> ${escapeHtml(String(p.ts_clamped ?? 0))}</span>`;
}

function compactClockChip() {
  if (!state.serverConnected) {
    return '<button type="button" class="statusChip statusChip-offline" data-open-status="clock">Time offline</button>';
  }
  const c = state.clock;
  if (!c) return '';
  const d = c.discipline || {};
  const cls = clockBadgeClass(c);
  let text = 'Time ?';
  if (d.available && d.synced) {
    const src = disciplineSourceLabel(d);
    text = `${src} ✓`;
  } else if (d.available) {
    text = 'Time unsynced ↻';
  } else {
    text = 'No sync';
  }
  return `<button type="button" class="statusChip statusChip-${cls}" data-open-status="clock">${escapeHtml(text)}</button>`;
}

function compactPodChip() {
  const p = state.podLink;
  if (!p || !p.enabled) return '';
  if (!state.serverConnected) {
    return '<button type="button" class="statusChip statusChip-offline" data-open-status="pod">Pod offline</button>';
  }
  const dropped = (p.rx_dropped || 0) + (p.dropped_readings || 0) + (p.ts_clamped || 0);
  const linkCls = !p.connected ? 'off' : (dropped > 0 ? 'warn' : 'ok');
  let parts = ['Pod'];
  if (p.has_rssi && Number.isFinite(p.rssi_dbm)) {
    parts.push(`${p.rssi_dbm} dBm`);
  } else if (!p.connected) {
    parts.push('off');
  }
  if (p.has_battery_telemetry || p.has_battery) {
    parts.push(formatBatteryHeadline(p));
  }
  return `<button type="button" class="statusChip statusChip-${linkCls}" data-open-status="pod">${escapeHtml(parts.join(' '))}</button>`;
}

function renderStatusChips() {
  if (!statusChipsEl) return;
  const chips = [compactClockChip(), compactPodChip()].filter(Boolean);
  statusChipsEl.innerHTML = chips.join('') || '<span class="dim">Status loading…</span>';
  for (const btn of statusChipsEl.querySelectorAll('[data-open-status]')) {
    btn.addEventListener('click', openStatusDrawer);
  }
}

function openStatusDrawer() {
  renderClockStatusFull(statusDrawerClock);
  renderPodStatusFull(statusDrawerPod);
  statusDrawer.showModal();
}

function renderActiveSensor() {
  if (!state.activeSensor || !panelRegions) return;
  const name = state.activeSensor;
  if (name === 'compass') {
    renderCompassPanel();
  } else if (name === 'airspeed') {
    renderAirspeedPanel();
  } else {
    renderLiveValues();
  }
}

function renderAirspeedPanel() {
  const ms4525 = state.devices.get('ms4525');
  const bmp581 = state.devices.get('bmp581');
  const airspeed = state.devices.get('airspeed');
  const pressAlt = state.devices.get('press_alt');
  KFAirspeed.renderPanel(panelRegions.kv, ms4525, bmp581, airspeed, pressAlt);
}

function renderCompassPanel() {
  const geo = state.devices.get('geo');
  const compass = state.devices.get('compass');
  KFCompass.renderPanel(panelRegions.kv, geo, compass, compassAlignMethodEffective());
}

function onWsTick() {
  if (state.routeView === 'sensors' && !state.activeSensor) {
    renderOverview();
  } else if (state.activeSensor) {
    renderActiveSensor();
  } else if (state.routeView === 'instruments') {
    renderInstruments();
  }
}

function escapeAttr(s) { return String(s ?? '').replace(/"/g, '&quot;'); }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

function wireAttrEdits(name) {
  for (const inp of panelRegions.attrs.querySelectorAll('input,select')) {
    const handler = async () => {
      const channel = inp.dataset.channel || '';
      const attr = inp.dataset.attr;
      const value = inp.value;
      inp.disabled = true;
      try {
        const r = await fetch(`/api/devices/${encodeURIComponent(name)}/attrs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel, attr, value }),
        });
        if (r.ok) {
          const body = await r.json();
          applyAttrsResponse(name, body);
          renderAttrs();
          return;
        }
        const err = await r.text();
        inp.classList.add('err');
        inp.title = err;
      } finally {
        inp.disabled = false;
      }
    };
    inp.addEventListener('change', handler);
  }
}

function applyAttrsResponse(name, body) {
  if (body && Array.isArray(body.attrs)) {
    if (body.location) applyDeviceLocation(name, body.location);
    state.attrs.set(name, body.attrs);
    return;
  }
  if (Array.isArray(body)) {
    state.attrs.set(name, body);
  }
}

async function loadAttrs(name) {
  if (SKIP_ATTRS_FETCH.has(name)) return;
  try {
    const r = await fetch(`/api/devices/${encodeURIComponent(name)}/attrs`);
    if (!r.ok) {
      state.attrs.set(name, []);
      return;
    }
    applyAttrsResponse(name, await r.json());
    if (state.activeSensor === name) renderAttrs();
  } catch {}
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    setServerConnected(true);
    refreshStatus();
  };
  ws.onmessage = (ev) => {
    setServerConnected(true);
    let snap;
    try { snap = JSON.parse(ev.data); } catch { return; }
    if (!snap || !snap.devices) return;
    let battUpdated = false;
    for (const [name, sample] of Object.entries(snap.devices)) {
      state.devices.set(name, sample);
      ensureDevice(name);
      if (name === 'bq27441') battUpdated = true;
      if (state.activeSensor === name && !SKIP_ATTRS_FETCH.has(name) && !state.attrs.has(name)) {
        loadAttrs(name);
      }
    }
    if (battUpdated) renderStatusChips();
    onWsTick();
  };
  ws.onerror = () => setServerConnected(false);
  ws.onclose = () => {
    setServerConnected(false);
    setTimeout(connect, 1000);
  };
}

function setServerConnected(connected) {
  if (state.serverConnected === connected) return;
  state.serverConnected = connected;
  updateRecordingUI();
  renderStatusChips();
}

function updateRecordingUI() {
  if (!state.serverConnected) {
    if (recBlockEl) recBlockEl.classList.add('rec-offline');
    if (recBlockEl) recBlockEl.classList.remove('rec-error');
    if (recDotEl) {
      recDotEl.classList.remove('live', 'paused', 'degraded');
      recDotEl.classList.add('offline');
    }
    if (pauseBtn) {
      pauseBtn.disabled = true;
      pauseBtn.title = 'Server unavailable';
    }
    return;
  }
  if (recBlockEl) recBlockEl.classList.remove('rec-offline');
  if (recDotEl) recDotEl.classList.remove('offline');
  const degraded = !!(state.recording && state.recording.degraded);
  if (recBlockEl) recBlockEl.classList.toggle('rec-error', degraded);
  if (recDotEl) recDotEl.classList.toggle('degraded', degraded);
  if (pauseBtn) {
    pauseBtn.disabled = false;
    pauseBtn.textContent = state.paused ? '▶' : '⏸';
    pauseBtn.title = state.paused ? 'Resume recording' : 'Pause recording';
  }
  if (recDotEl) {
    recDotEl.classList.toggle('paused', state.paused && !degraded);
    recDotEl.classList.toggle('live', !state.paused && !degraded);
  }
  if (recLabelEl) {
    if (degraded) {
      const e = state.recording && state.recording.last_error ? ` (${state.recording.last_error})` : '';
      recLabelEl.textContent = `REC ERROR${e}`;
      recLabelEl.title = `${state.recording.consecutive_failures} consecutive flush failures${e}`;
      recLabelEl.hidden = false;
    } else {
      recLabelEl.textContent = '';
      recLabelEl.title = '';
      recLabelEl.hidden = true;
    }
  }
}

function setPausedUI(paused) {
  state.paused = paused;
  updateRecordingUI();
}

pauseBtn?.addEventListener('click', async () => {
  if (!state.serverConnected) return;
  const next = !state.paused;
  try {
    const r = await fetch('/api/recording', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused: next }),
    });
    if (r.ok) {
      const j = await r.json();
      setPausedUI(!!j.paused);
    }
  } catch {}
});

function formatPodFooter(pod) {
  if (!pod || !pod.enabled) return '';
  const dropped = pod.rx_dropped || 0;
  const rx = pod.rx_packets || 0;
  const sent = rx + dropped;
  return ` · Pod: ${dropped} dropped / ${sent} sent`;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatRecSize(n) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}K`;
  return `${(n / 1024 / 1024).toFixed(1)}M`;
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) {
      setServerConnected(false);
      renderStatusChips();
      return;
    }
    setServerConnected(true);
    const s = await r.json();
    if (s.db_size_bytes != null && recSizeEl) {
      recSizeEl.textContent = formatRecSize(s.db_size_bytes);
      recSizeEl.title = s.db_path ? `Flight DB: ${s.db_path} (${formatBytes(s.db_size_bytes)})` : formatBytes(s.db_size_bytes);
    }
    let bufText = 'Buffered: — rows';
    if (s.buffered_rows) {
      const total = Object.values(s.buffered_rows).reduce((a, b) => a + b, 0);
      bufText = `Buffered: ${total} rows`;
    }
    bufText += formatPodFooter(s.pod);
    if (moreBufStatEl) moreBufStatEl.textContent = bufText;
    if (s.aircraft && hdrTailEl) hdrTailEl.textContent = s.aircraft;
    if (typeof s.recording_paused === 'boolean') setPausedUI(s.recording_paused);
    state.recording = s.recording || null;
    updateRecordingUI();
    state.podLink = s.pod || null;
    state.clock = s.clock || null;
    renderStatusChips();
  } catch {
    setServerConnected(false);
    renderStatusChips();
  }
}

let locationsPreloaded = false;

async function preloadDeviceLocations() {
  if (locationsPreloaded) return;
  const names = [...state.deviceNames];
  if (names.length === 0) return;
  locationsPreloaded = true;
  await Promise.all(names.map(async (name) => {
    try {
      const r = await fetch(`/api/devices/${encodeURIComponent(name)}/attrs`);
      if (!r.ok) return;
      applyAttrsResponse(name, await r.json());
    } catch {}
  }));
}

async function loadIIODevices() {
  try {
    const r = await fetch('/api/devices');
    if (!r.ok) return;
    const devices = await r.json();
    const iio = (devices || []).map((d) => d.name).sort();
    state.iioDevices = new Set(iio);
    for (const name of iio) {
      applyDeviceLocation(name, 'hub');
      ensureDevice(name);
    }
    if (state.activeSensor && !state.attrs.has(state.activeSensor)) {
      loadAttrs(state.activeSensor);
    }
  } catch {}
}

async function loadConfig() {
  try {
    const r = await fetch('/api/config');
    if (r.ok) state.config = await r.json();
  } catch {}
}

function openSettingsDialog() {
  fetch('/api/config').then((cfgR) => cfgR.json()).then((cfg) => {
    document.getElementById('cfgAircraft').value = cfg.aircraft || '';
    document.getElementById('cfgNotes').value = cfg.notes || '';
    document.getElementById('cfgFlush').value = cfg.flush_seconds || 5;
    const bt = document.getElementById('cfgBigText');
    if (bt) bt.checked = bigTextEnabled();
    settingsDlg._cfg = cfg;
    settingsDlg.showModal();
  });
}

const BIGTEXT_KEY = 'kf.bigtext';
function bigTextEnabled() {
  try { return localStorage.getItem(BIGTEXT_KEY) === '1'; } catch { return false; }
}
function setBigText(on) {
  try { localStorage.setItem(BIGTEXT_KEY, on ? '1' : '0'); } catch {}
  document.body.classList.toggle('bigtext', !!on);
}
setBigText(bigTextEnabled());

// A value is "stale" once its source sample's ts_ns is older than this.
// Lower thresholds (e.g. 1 s) cause flicker on slower sensors (gps at 1 Hz,
// derive devices at 5 Hz); 3 s comfortably covers normal cadence.
const STALENESS_MS = 3000;

function markStaleness() {
  const now = Date.now();
  for (const [name, sample] of state.devices) {
    if (!sample || !sample.ts_ns) continue;
    const ageMs = now - sample.ts_ns / 1e6;
    const stale = ageMs > STALENESS_MS;
    for (const el of document.querySelectorAll(`[data-device="${CSS.escape(name)}"]`)) {
      el.classList.toggle('kf-stale', stale);
      if (stale) {
        const ageS = Math.round(ageMs / 1000);
        el.dataset.staleSec = String(ageS);
      } else {
        delete el.dataset.staleSec;
      }
    }
  }
  // Disconnect banner shows the WORST-case staleness so a pilot sees a
  // single signal ("values from N s ago") rather than per-tile maths.
  const banner = document.getElementById('staleBanner');
  if (!banner) return;
  if (state.serverConnected) {
    banner.hidden = true;
    return;
  }
  let oldestMs = 0;
  for (const [, sample] of state.devices) {
    if (!sample || !sample.ts_ns) continue;
    const age = now - sample.ts_ns / 1e6;
    if (age > oldestMs) oldestMs = age;
  }
  if (oldestMs < 2000) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  banner.textContent = `Disconnected — values ${Math.round(oldestMs / 1000)} s old`;
}
setInterval(markStaleness, 1000);

detailBackEl?.addEventListener('click', () => {
  location.hash = '#/';
});

window.addEventListener('hashchange', applyRoute);

for (const btn of document.querySelectorAll('#bottomNav .bottomNavBtn')) {
  btn.addEventListener('click', () => {
    const nav = btn.dataset.nav;
    if (nav === 'sensors') {
      location.hash = '#/';
    } else if (nav === 'instruments') {
      location.hash = '#/instruments';
    } else if (nav === 'more') {
      refreshStatus();
      moreSheet.showModal();
    }
  });
}

menuBtn?.addEventListener('click', () => {
  refreshStatus();
  moreSheet.showModal();
});

document.getElementById('moreTerminalBtn')?.addEventListener('click', () => {
  moreSheet.close();
  location.href = '/terminal';
});

document.getElementById('moreSettingsBtn')?.addEventListener('click', () => {
  moreSheet.close();
  openSettingsDialog();
});

document.getElementById('moreStatusBtn')?.addEventListener('click', () => {
  moreSheet.close();
  openStatusDrawer();
});

document.getElementById('cfgSave')?.addEventListener('click', async (e) => {
  e.preventDefault();
  const cfg = settingsDlg._cfg || {};
  cfg.aircraft = document.getElementById('cfgAircraft').value;
  cfg.notes = document.getElementById('cfgNotes').value;
  cfg.flush_seconds = parseInt(document.getElementById('cfgFlush').value, 10) || 5;
  const bt = document.getElementById('cfgBigText');
  if (bt) setBigText(bt.checked);
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  if (hdrTailEl) hdrTailEl.textContent = cfg.aircraft || '';
  settingsDlg.close();
});

(async function init() {
  updateRecordingUI();
  renderStatusChips();
  await loadConfig();
  if (window.KF_INITIAL_DEVICES) {
    for (const d of window.KF_INITIAL_DEVICES) ensureDevice(d);
  }
  await loadIIODevices();
  for (const d of POD_TELEMETRY_DEVICES) ensureDevice(d);
  for (const d of CALC_DEVICES) ensureDevice(d);
  ensureDevice('gps');
  await preloadDeviceLocations();
  if (!location.hash || location.hash === '#') {
    location.hash = '#/';
  }
  applyRoute();
  connect();
  refreshStatus();
})();

setInterval(refreshStatus, 2000);
