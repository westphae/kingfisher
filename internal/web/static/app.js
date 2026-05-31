// Kingfisher UI — vanilla JS, no build step. Maintains a per-device latest-
// value map updated from the /ws snapshot stream and re-renders the active
// tab on each tick.

const state = {
  devices: new Map(),          // name -> {ts_ns, values}
  activeTab: null,
  tabs: new Set(),
  attrs: new Map(),            // name -> [{channel, attr, value, writable}]
  deviceLocation: new Map(),   // name -> "hub" | "pod"
  iioDevices: new Set(),       // kernel IIO names (per-tab sample_hz in Settings)
  podLink: null,               // latest /api/status pod object
  clock: null,                 // latest /api/status clock object
  serverConnected: false,      // kingfisher reachable (ws or /api/status)
  paused: false,
  config: null,                // last /api/config (compass settings)
};

const tabsEl = document.getElementById('tabs');
const panelEl = document.getElementById('panel');
const bufEl   = document.getElementById('bufStat');
const dbEl    = document.getElementById('dbSize');

const DERIVED_DEVICES = ['ahrs', 'press_alt', 'compass', 'airspeed'];
// Derived / model tabs show (calc), not (hub).
const CALC_DEVICES = new Set([...DERIVED_DEVICES, 'geo']);
// Wing-pod sensor tabs (matches pod.DefaultPodDeviceNames).
const POD_TELEMETRY_DEVICES = new Set(['bmp581', 'mmc5983', 'ms4525', 'bq27441']);
const HUB_VIRTUAL_DEVICES = new Set(['gps', ...CALC_DEVICES]);
// Derived tabs use custom settings UI; skip /api/devices/.../attrs polling.
const SKIP_ATTRS_FETCH = new Set(['compass', 'ahrs', 'geo', 'press_alt', 'gps', 'airspeed']);

function isPodTelemetry(name) {
  return state.deviceLocation.get(name) === 'pod';
}

function inferDeviceLocation(name) {
  if (CALC_DEVICES.has(name)) return 'calc';
  if (POD_TELEMETRY_DEVICES.has(name)) return 'pod';
  if (state.iioDevices.has(name)) return 'hub';
  return null;
}

function applyDeviceLocation(name, loc) {
  if (!loc) return;
  if (CALC_DEVICES.has(name)) loc = 'calc';
  const prev = state.deviceLocation.get(name);
  if (prev === loc) return;
  state.deviceLocation.set(name, loc);
  const btn = tabsEl.querySelector(`button[data-tab="${name}"]`);
  if (btn) btn.textContent = tabLabel(name);
}

function tabLabel(name) {
  const loc = state.deviceLocation.get(name);
  return loc ? `${name} (${loc})` : name;
}

function tabRank(name) {
  if (state.iioDevices.has(name)) return 0;
  if (isPodTelemetry(name)) return 1;
  if (DERIVED_DEVICES.includes(name)) return 2;
  return 3; // gps, geo, other virtual
}

function compareTabNames(a, b) {
  const ra = tabRank(a);
  const rb = tabRank(b);
  if (ra !== rb) return ra - rb;
  return a.localeCompare(b);
}

function sortedTabNames() {
  return [...state.tabs].sort(compareTabNames);
}

function insertTabButton(btn, name) {
  const order = sortedTabNames();
  const idx = order.indexOf(name);
  for (const b of tabsEl.querySelectorAll('button')) {
    if (order.indexOf(b.dataset.tab) > idx) {
      tabsEl.insertBefore(btn, b);
      return;
    }
  }
  tabsEl.appendChild(btn);
}

function ensureTab(name) {
  if (name === 'pod') return; // legacy aggregate sticky cache
  if (state.tabs.has(name)) return;
  state.tabs.add(name);
  const loc = inferDeviceLocation(name);
  if (loc) applyDeviceLocation(name, loc);
  const btn = document.createElement('button');
  btn.textContent = tabLabel(name);
  btn.dataset.tab = name;
  btn.addEventListener('click', () => selectTab(name));
  insertTabButton(btn, name);
  if (!state.activeTab) selectTab(sortedTabNames()[0]);
}

function selectTab(name) {
  state.activeTab = name;
  for (const b of tabsEl.querySelectorAll('button')) {
    b.classList.toggle('active', b.dataset.tab === name);
  }
  // Reload pod/IIO attrs so rate fields stay current after edits or SetRate/ack.
  if (name === 'compass') {
    if (!state.attrs.has('compass')) state.attrs.set('compass', []);
  } else if (name === 'airspeed') {
    if (!state.attrs.has('airspeed')) state.attrs.set('airspeed', []);
  } else if (!SKIP_ATTRS_FETCH.has(name) &&
    (isPodTelemetry(name) || state.iioDevices.has(name) || !state.attrs.has(name))) {
    loadAttrs(name);
  }
  renderActiveTab();
  renderAttrs();
}

// Two-region panel: a live-values div (re-rendered on every WS tick) and
// an attrs div (re-rendered only on tab switch or attr fetch). Keeping
// attrs untouched on snapshot updates preserves focus while the user is
// typing into an editable attribute.
function rebuildPanel() {
  panelEl.innerHTML =
    `<div id="liveKV"></div><div id="attrsBox"></div>`;
  const regions = {
    kv:    document.getElementById('liveKV'),
    attrs: document.getElementById('attrsBox'),
  };
  regions.attrs.removeAttribute('data-compass-wired');
  regions.attrs.removeAttribute('data-airspeed-wired');
  return regions;
}
let panelRegions = rebuildPanel();

function renderLiveValues() {
  const name = state.activeTab;
  if (!name) { panelRegions.kv.innerHTML = ''; return; }
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
  const name = state.activeTab;
  if (!name) {
    panelRegions.attrs.innerHTML = '';
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
  const names = [...state.tabs].filter((n) => {
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

// nearlyEqual compares attribute strings tolerantly: IIO sometimes formats
// the current value with more trailing zeroes than the _available list
// (e.g. value="0.000598550" vs option="0.000598550000"), or vice versa.
function nearlyEqual(opt, val) {
  if (opt === val) return true;
  const a = parseFloat(opt);
  const b = parseFloat(val);
  if (!isFinite(a) || !isFinite(b)) return false;
  return Math.abs(a - b) < 1e-12 * Math.max(1, Math.abs(a));
}

function podLinkLabel(pod) {
  if (!pod || !pod.enabled) return 'Pod ingest off';
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

function formatBatteryHeadline(p) {
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

function renderPodStatus() {
  const el = document.getElementById('podStatus');
  if (!el) return;
  const p = state.podLink;
  if (!p || !p.enabled) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  el.hidden = false;
  const linkCls = !p.connected
    ? 'off'
    : ((p.rx_dropped || 0) > 0 ? 'warn' : 'ok');
  let rssiText = '—';
  let rssiCls = '';
  if (p.has_rssi) {
    rssiText = formatRssi(p.rssi_dbm);
    rssiCls = rssiClass(p.rssi_dbm);
  }
  let battText = formatBatteryHeadline(p);
  const battCls = p.has_battery_telemetry && p.battery_gauge_learned
    ? battSocClass(p.battery_soc_pct)
    : '';
  el.className = `podStatus podStatus-${linkCls}`;
  el.innerHTML =
    `<span class="podStatusItem"><span class="lbl">Pod</span> ${escapeHtml(podLinkLabel(p))}</span>` +
    `<span class="podStatusItem ${rssiCls}"><span class="lbl">RSSI</span> ${escapeHtml(rssiText)}</span>` +
    `<span class="podStatusItem ${battCls}"><span class="lbl">Batt</span> ${escapeHtml(battText)}</span>`;
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

function renderClockStatus() {
  const el = document.getElementById('clockStatus');
  if (!el) return;
  const c = state.clock;
  if (!c) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  el.hidden = false;
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
    if (d.pps_present && !d.pps_steering) {
      parts.push('<span class="clockStatusItem clockHint"><span class="lbl">PPS</span> wired, idle</span>');
    }
  } else if (d.available) {
    parts.push('<span class="clockStatusItem"><span class="lbl">Pi time</span> not synced</span>');
    if (d.pps_present) {
      parts.push('<span class="clockStatusItem clockHint"><span class="lbl">PPS</span> present</span>');
    }
  } else if (g.has_fix) {
    parts.push('<span class="clockStatusItem"><span class="lbl">Pi time</span> GPS fix only</span>');
    if (g.baseline_ready && Number.isFinite(g.clock_error_ms)) {
      parts.push(`<span class="clockStatusItem"><span class="lbl">Est. error</span> ${escapeHtml(formatClockOffsetMs(g.clock_error_ms))}</span>`);
    } else if (Number.isFinite(g.pipeline_lag_ms)) {
      parts.push('<span class="clockStatusItem"><span class="lbl">Est. error</span> calibrating…</span>');
    }
  } else {
    parts.push('<span class="clockStatusItem"><span class="lbl">Pi time</span> no GPS fix</span>');
  }

  if (g.has_fix && Number.isFinite(g.fix_age_s)) {
    const stale = g.fresh === false;
    parts.push(`<span class="clockStatusItem${stale ? ' clockWarn' : ''}"><span class="lbl">GPS data</span> ${escapeHtml(formatAgeSeconds(g.fix_age_s))}</span>`);
  }

  if (c.startup_fallback) {
    parts.push('<span class="clockStatusItem clockHint"><span class="lbl">Boot</span> unsynced start</span>');
  }

  el.title = c.detail || c.startup_reason || '';
  el.innerHTML = parts.join('');
}

function renderActiveTab() {
  if (state.activeTab === 'compass') {
    renderCompassPanel();
  } else if (state.activeTab === 'airspeed') {
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

function escapeAttr(s) { return String(s ?? '').replace(/"/g, '&quot;'); }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
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
    if (state.activeTab === name) renderAttrs();
  } catch {}
}

function fmt(v) {
  return KFDisplay.fmtDefault(v);
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
      ensureTab(name);
      if (name === 'bq27441') battUpdated = true;
      if (state.activeTab === name && !SKIP_ATTRS_FETCH.has(name) && !state.attrs.has(name)) {
        loadAttrs(name);
      }
    }
    if (battUpdated) renderPodStatus();
    renderActiveTab();
  };
  ws.onerror = () => setServerConnected(false);
  ws.onclose = () => {
    setServerConnected(false);
    setTimeout(connect, 1000);
  };
}

const tailEl     = document.querySelector('#hdr .tail');
const recDotEl   = document.getElementById('recDot');
const recLabelEl = document.getElementById('recLabel');
const recBlockEl = document.querySelector('#hdr .rec');
const pauseBtn   = document.getElementById('pauseBtn');

function setServerConnected(connected) {
  if (state.serverConnected === connected) return;
  state.serverConnected = connected;
  updateRecordingUI();
}

function updateRecordingUI() {
  if (!state.serverConnected) {
    if (recBlockEl) recBlockEl.classList.add('rec-offline');
    if (recDotEl) {
      recDotEl.classList.remove('live', 'paused');
      recDotEl.classList.add('offline');
    }
    if (recLabelEl) recLabelEl.textContent = 'OFFLINE';
    if (pauseBtn) {
      pauseBtn.disabled = true;
      pauseBtn.title = 'Server unavailable';
    }
    return;
  }
  if (recBlockEl) recBlockEl.classList.remove('rec-offline');
  if (recDotEl) recDotEl.classList.remove('offline');
  if (pauseBtn) {
    pauseBtn.disabled = false;
    pauseBtn.textContent = state.paused ? '▶' : '⏸';
    pauseBtn.title = state.paused ? 'Resume recording' : 'Pause recording';
  }
  if (recDotEl) {
    recDotEl.classList.toggle('paused', state.paused);
    recDotEl.classList.toggle('live', !state.paused);
  }
  if (recLabelEl) recLabelEl.textContent = state.paused ? 'PAUSED' : 'REC';
}

function setPausedUI(paused) {
  state.paused = paused;
  updateRecordingUI();
}

pauseBtn.addEventListener('click', async () => {
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
  const sent = rx + dropped; // pod SampleBatch seq span (received + gaps)
  return ` · Pod: ${dropped} dropped / ${sent} sent`;
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) {
      setServerConnected(false);
      return;
    }
    setServerConnected(true);
    const s = await r.json();
    if (s.db_size_bytes != null && dbEl) {
      let dbText = formatBytes(s.db_size_bytes);
      if (s.db_volume_free_bytes != null) {
        dbText += ` · ${formatBytes(s.db_volume_free_bytes)} free`;
      }
      dbEl.textContent = dbText;
      dbEl.title = s.db_path ? `Flight DB: ${s.db_path}` : '';
    }
    let bufText = 'Buffered: — rows';
    if (s.buffered_rows) {
      const total = Object.values(s.buffered_rows).reduce((a, b) => a + b, 0);
      bufText = `Buffered: ${total} rows`;
    }
    bufText += formatPodFooter(s.pod);
    if (bufEl) bufEl.textContent = bufText;
    if (s.aircraft && tailEl) tailEl.textContent = s.aircraft;
    if (typeof s.recording_paused === 'boolean') setPausedUI(s.recording_paused);
    state.podLink = s.pod || null;
    state.clock = s.clock || null;
    renderClockStatus();
    renderPodStatus();
  } catch {
    setServerConnected(false);
  }
}

let locationsPreloaded = false;

async function preloadDeviceLocations() {
  if (locationsPreloaded) return;
  const names = [...state.tabs];
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
    const iio = (devices || []).map(d => d.name).sort();
    state.iioDevices = new Set(iio);
    for (const name of iio) {
      applyDeviceLocation(name, 'hub');
      ensureTab(name);
    }
    if (state.activeTab && !state.attrs.has(state.activeTab)) {
      loadAttrs(state.activeTab);
    }
  } catch {}
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024*1024) return `${(n/1024).toFixed(1)} KB`;
  return `${(n/1024/1024).toFixed(1)} MB`;
}

// Settings dialog (aircraft / notes / flush only — IIO rates live on each sensor tab)
const dlg = document.getElementById('settingsDlg');

document.getElementById('settingsBtn').addEventListener('click', async () => {
  const cfgR = await fetch('/api/config');
  const cfg = await cfgR.json();
  document.getElementById('cfgAircraft').value = cfg.aircraft || '';
  document.getElementById('cfgNotes').value    = cfg.notes || '';
  document.getElementById('cfgFlush').value    = cfg.flush_seconds || 5;
  dlg._cfg = cfg;
  dlg.showModal();
});

document.getElementById('cfgSave').addEventListener('click', async (e) => {
  e.preventDefault();
  const cfg = dlg._cfg || {};
  cfg.aircraft      = document.getElementById('cfgAircraft').value;
  cfg.notes         = document.getElementById('cfgNotes').value;
  cfg.flush_seconds = parseInt(document.getElementById('cfgFlush').value, 10) || 5;
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  if (tailEl) tailEl.textContent = cfg.aircraft || '';
  dlg.close();
});

async function loadConfig() {
  try {
    const r = await fetch('/api/config');
    if (r.ok) state.config = await r.json();
  } catch {}
}

(async function init() {
  updateRecordingUI();
  await loadConfig();
  if (window.KF_INITIAL_DEVICES) {
    for (const d of window.KF_INITIAL_DEVICES) ensureTab(d);
  }
  await loadIIODevices();
  for (const d of POD_TELEMETRY_DEVICES) ensureTab(d);
  await preloadDeviceLocations();
  connect();
  refreshStatus();
})();
setInterval(refreshStatus, 2000);
