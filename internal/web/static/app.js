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
  wsOpen: false,
  lastWsAt: 0,
  deviceSeenAt: new Map(), // name -> client ms when last WS snapshot included device
  paused: false,
  powerOffAvailable: false,
  config: null,
};

const viewOverviewEl = document.getElementById('viewOverview');
const viewDetailEl = document.getElementById('viewDetail');
const viewInstrumentsEl = document.getElementById('viewInstruments');
const viewHowgozitEl = document.getElementById('viewHowgozit');
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
const powerOffBtn = document.getElementById('powerOffBtn');
const powerOffDlg = document.getElementById('powerOffDlg');
const menuBtn = document.getElementById('menuBtn');
const moreSheet = document.getElementById('moreSheet');
const moreBufStatEl = document.getElementById('moreBufStat');
const statusDrawer = document.getElementById('statusDrawer');
const statusDrawerClock = document.getElementById('statusDrawerClock');
const statusDrawerPod = document.getElementById('statusDrawerPod');
const settingsDlg = document.getElementById('settingsDlg');
const accessDlg = document.getElementById('accessDlg');
const accessBodyEl = document.getElementById('accessBody');
const flightsDlg = document.getElementById('flightsDlg');
const flightsBodyEl = document.getElementById('flightsBody');
const flightsMetaEl = document.getElementById('flightsMeta');
const flightNotesDlg = document.getElementById('flightNotesDlg');

let powerOffPending = false;

function setPowerOffUI() {
  if (!powerOffBtn) return;
  const available = state.powerOffAvailable;
  powerOffBtn.disabled = !available || powerOffPending || !state.serverConnected;
  powerOffBtn.title = available
    ? 'Shut down Kingfisher and power off the Pi'
    : 'Power-off helper not installed (see deploy/kingfisher-poweroff.sh)';
}

const DERIVED_DEVICES = ['ahrs', 'press_alt', 'compass', 'airspeed'];
const CALC_DEVICES = new Set([...DERIVED_DEVICES, 'geo']);
const POD_TELEMETRY_DEVICES = new Set(['bmp581', 'mmc5983', 'ms4525', 'bq27441']);
const SKIP_ATTRS_FETCH = new Set(['compass', 'ahrs', 'geo', 'press_alt', 'gps', 'airspeed']);

function isPodTelemetry(name) {
  return deviceTabGroup(name) === 'pod';
}

function inferDeviceLocation(name) {
  if (CALC_DEVICES.has(name)) return 'calc';
  if (name === 'system') return 'system';
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
  if (path === '/howgozit') return { view: 'howgozit', sensor: null };
  const m = path.match(/^\/sensor\/(.+)$/);
  if (m) return { view: 'sensors', sensor: decodeURIComponent(m[1]) };
  return { view: 'sensors', sensor: null };
}

// Apply route immediately; do not rely on hashchange alone (iOS Safari can defer
// it while the main thread is busy re-rendering the overview on WS ticks).
function setRoute(hash) {
  const normalized = hash.startsWith('#') ? hash : '#' + hash;
  if (location.hash !== normalized) {
    location.hash = normalized;
  }
  applyRoute();
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
  if (viewHowgozitEl) {
    viewHowgozitEl.classList.toggle('view-active', r.view === 'howgozit');
  }

  for (const btn of document.querySelectorAll('#bottomNav .bottomNavBtn')) {
    const nav = btn.dataset.nav;
    let active = false;
    if (nav === 'sensors') active = onSensors;
    else if (nav === 'instruments') active = r.view === 'instruments';
    else if (nav === 'howgozit') active = r.view === 'howgozit';
    btn.classList.toggle('active', active);
  }

  if (onDetail) {
    openSensorDetail(r.sensor);
  } else if (onSensors) {
    renderOverview(true);
  } else if (r.view === 'instruments') {
    renderInstruments();
  } else if (r.view === 'howgozit') {
    renderHowgozit();
  }
}

function renderInstruments() {
  const mount = document.getElementById('pfdMount');
  if (!mount) return;
  KFPFD.renderPanel(mount, {
    ahrs: KFSmooth.sampleValues(state.devices.get('ahrs'), 'ahrs'),
    compass: KFSmooth.sampleValues(state.devices.get('compass'), 'compass'),
    airspeed: KFSmooth.sampleValues(state.devices.get('airspeed'), 'airspeed'),
    pressAlt: KFSmooth.sampleValues(state.devices.get('press_alt'), 'press_alt'),
    gps: KFSmooth.sampleValues(state.devices.get('gps'), 'gps'),
  });
}

function renderHowgozit() {
  const mount = document.getElementById('howgozitMount');
  if (!mount) return;
  const mod = window.KFHowgozit;
  if (!mod) {
    mount.innerHTML = '<p class="dim" style="padding:1rem">Howgozit failed to initialize.</p>';
    return;
  }
  void mod.show(mount);
}

function overviewDataSig() {
  const parts = [];
  for (const name of KFOverview.sortedOverviewDevices(state.deviceNames)) {
    const sm = state.devices.get(name);
    parts.push(name, String(sm?.ts_ns ?? ''));
    const vals = sm?.values;
    if (vals) {
      for (const k of Object.keys(vals).sort()) {
        parts.push(k, String(vals[k]));
      }
    }
  }
  return parts.join('\0');
}

let lastOverviewSig = '';
const OVERVIEW_RENDER_MIN_MS = 250;
let lastOverviewRenderMs = 0;
let overviewRenderTimer = null;

function renderOverview(force) {
  const now = performance.now();
  if (!force && now - lastOverviewRenderMs < OVERVIEW_RENDER_MIN_MS) {
    if (!overviewRenderTimer) {
      overviewRenderTimer = setTimeout(() => {
        overviewRenderTimer = null;
        if (state.routeView === 'sensors' && !state.activeSensor) renderOverview(true);
      }, OVERVIEW_RENDER_MIN_MS - (now - lastOverviewRenderMs));
    }
    return;
  }
  if (overviewRenderTimer) {
    clearTimeout(overviewRenderTimer);
    overviewRenderTimer = null;
  }
  lastOverviewRenderMs = now;
  const sig = overviewDataSig();
  if (!force && sig === lastOverviewSig) return;
  lastOverviewSig = sig;
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
    setRoute('#/');
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
  const vals = KFSmooth.values(name, sm.values || {});
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
    html += renderSmoothSettings(name);
    panelRegions.attrs.removeAttribute('data-compass-wired');
    panelRegions.attrs.removeAttribute('data-smooth-wired');
    panelRegions.attrs.innerHTML = html + `</section>`;
    wireCompassSettings();
    wireSmoothSettings(name);
    return;
  }
  if (name === 'airspeed') {
    html += renderAirspeedSettings();
    html += renderSmoothSettings(name);
    panelRegions.attrs.removeAttribute('data-airspeed-wired');
    panelRegions.attrs.removeAttribute('data-smooth-wired');
    panelRegions.attrs.innerHTML = html + `</section>`;
    wireAirspeedSettings();
    wireSmoothSettings(name);
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
  html += renderSmoothSettings(name);
  html += `</section>`;
  panelRegions.attrs.removeAttribute('data-smooth-wired');
  panelRegions.attrs.innerHTML = html;
  wireAttrEdits(name);
  wireSmoothSettings(name);
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
    KFTap.bindTap(gpsBtn, () => doAlign({ manual_heading_deg: null }));
  }
  if (manBtn) {
    KFTap.bindTap(manBtn, () => {
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
    KFTap.bindTap(zeroBtn, async () => {
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

function renderSmoothSettings(device) {
  if (typeof KFSmooth === 'undefined') {
    return (
      '<section class="smoothSettings"><h4>Display smoothing</h4>' +
      '<p class="dim smoothHint">Smoothing UI unavailable — rebuild and restart kingfisher ' +
      '(this binary is missing <code>smooth.js</code>).</p></section>'
    );
  }
  const vals = state.devices.get(device)?.values ?? {};
  const groups = KFSmooth.listGroups(device, vals);
  if (groups.length === 0) {
    return (
      '<section class="smoothSettings"><h4>Display smoothing</h4>' +
      '<p class="dim smoothHint">No smoothable channel groups for this device yet.</p></section>'
    );
  }
  let rows = '';
  for (const g of groups) {
    const ui = KFSmooth.uiGroup(device, g.id);
    const rawOn = ui.mode !== 'smoothed';
    const tauDisabled = rawOn ? ' disabled' : '';
    const nameBase = `smooth-${device}-${g.id}`;
    rows +=
      `<div class="smoothRow attrRow" data-smooth-group="${escapeAttr(g.id)}">` +
      `<div class="k">${escapeHtml(KFSmooth.groupLabel(device, g.id, g.channels))}</div>` +
      `<div class="v">` +
      `<div class="smoothMode">` +
      `<label><input type="radio" name="${escapeAttr(nameBase)}" value="raw"${rawOn ? ' checked' : ''}/> Raw</label>` +
      `<label><input type="radio" name="${escapeAttr(nameBase)}" value="smoothed"${!rawOn ? ' checked' : ''}/> Smoothed</label>` +
      `</div>` +
      `<label class="smoothTauLbl">τ <input type="number" class="smoothTau" step="0.05" min="0.05" value="${escapeAttr(ui.tau_s)}"${tauDisabled}/> s</label>` +
      `</div></div>`;
  }
  let hint = '<p class="dim smoothHint">Affects cockpit display only; flight recording is unchanged.</p>';
  if (device === 'airspeed') {
    hint += '<p class="dim smoothHint">Pitot ΔP EMA (server) is configured above; this smooths published IAS/TAS on screen.</p>';
  }
  return `<section class="smoothSettings"><h4>Display smoothing</h4>${hint}${rows}</section>`;
}

function syncSmoothTauDisabled(row) {
  const raw = row.querySelector('input[value="raw"]')?.checked;
  const tauInp = row.querySelector('.smoothTau');
  if (tauInp) tauInp.disabled = !!raw;
}

async function saveSmoothSettings(device) {
  if (!state.config) await loadConfig();
  const cfg = state.config ?? {};
  cfg.display = cfg.display ?? {};
  cfg.display.smooth = cfg.display.smooth ?? {};
  cfg.display.smooth[device] = cfg.display.smooth[device] ?? {};
  const prev = { ...cfg.display.smooth[device] };
  const rows = panelRegions?.attrs?.querySelectorAll('.smoothRow') ?? [];
  for (const row of rows) {
    const groupId = row.dataset.smoothGroup;
    if (!groupId) continue;
    const mode = row.querySelector('input[value="smoothed"]')?.checked ? 'smoothed' : 'raw';
    const tauInp = row.querySelector('.smoothTau');
    const tau = tauInp ? Number(tauInp.value) : KFSmooth.uiGroup(device, groupId).tau_s;
    const prevG = prev[groupId];
    if (!prevG || prevG.mode !== mode || prevG.tau_s !== tau) {
      const g = KFSmooth.listGroups(device, state.devices.get(device)?.values ?? {})
        .find((x) => x.id === groupId);
      if (g) KFSmooth.clearGroup(device, g.channels);
    }
    cfg.display.smooth[device][groupId] = { mode, tau_s: tau };
  }
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    const t = await r.text();
    if (!r.ok) throw new Error(t || r.statusText);
    state.config = cfg;
    KFSmooth.setConfig(cfg.display);
  } catch (e) {
    console.error('smooth settings save:', e);
  }
}

function wireSmoothSettings(device) {
  if (!panelRegions?.attrs) return;
  const key = `smooth-${device}`;
  if (panelRegions.attrs.dataset.smoothWired === key) return;
  panelRegions.attrs.dataset.smoothWired = key;
  const saveDebounced = debounce(() => saveSmoothSettings(device), 400);
  for (const row of panelRegions.attrs.querySelectorAll('.smoothRow')) {
    syncSmoothTauDisabled(row);
    for (const inp of row.querySelectorAll('input')) {
      inp.addEventListener('change', () => {
        syncSmoothTauDisabled(row);
        saveDebounced();
      });
    }
  }
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
  if (pod.protect_sleep) return `Battery protect — pod sleeping (${pod.sleep_reason || 'low battery'})`;
  if (pod.power_mode === 'burst') {
    const age = Number.isFinite(pod.status_age_s) && pod.status_age_s >= 0 ? ` ${formatAgeShort(pod.status_age_s)}` : '';
    if (pod.connected) return 'Burst mode — uplinking';
    if (pod.burst_quiet) return `Burst mode — collecting, radio off (last sync${age})`;
    if (pod.burst_lost) return `Silent since burst mode (last sync${age}) — protect sleep, dead battery, or pod fault`;
    return `Burst mode — sync overdue (last sync${age})`;
  }
  if (pod.power_mode === 'sleeping') return `Sleeping (${pod.sleep_reason || 'battery'})`;
  if (pod.power_mode === 'sleep_pending') return `Sleep pending (${pod.sleep_reason || 'battery'})`;
  if (!pod.connected) return 'No recent pod traffic';
  if (pod.recent_drops) return 'Link up (recent drops)';
  return 'Link OK';
}

function formatAgeShort(s) {
  if (!Number.isFinite(s) || s < 0) return '';
  if (s < 90) return `${Math.round(s)}s ago`;
  return `${Math.round(s / 60)}m ago`;
}

// Chip/drawer severity for the pod link. Burst-quiet silence is healthy;
// protect is dispatch-relevant; an overdue burst sync is a real problem.
function podLinkClass(p) {
  if (p.protect_sleep) return 'warn';
  if (!p.connected && p.power_mode === 'burst') return p.burst_quiet ? 'ok' : 'warn';
  if (!p.connected) return 'off';
  return p.recent_drops ? 'warn' : 'ok';
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

function formatAgeSeconds(sec) {
  if (!Number.isFinite(sec)) return '—';
  if (sec >= 10) return `${sec.toFixed(0)} s ago`;
  if (sec >= 1) return `${sec.toFixed(1)} s ago`;
  return `${sec.toFixed(2)} s ago`;
}

function disciplineSourceLabel(d) {
  if (!d) return '—';
  // NTP source_label is the server hostname/IP — too long for the header chip;
  // the tooltip still carries it via the detail string.
  if (d.source === 'ntp') return 'NTP';
  if (d.source_label) return d.source_label;
  switch (d.source) {
    case 'pps': return 'PPS';
    case 'gps': return 'GPS';
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
  if (retryBtn && retryBtn.dataset.tapWired !== '1') {
    retryBtn.dataset.tapWired = '1';
    KFTap.bindTap(retryBtn, async () => {
      retryBtn.disabled = true;
      try {
        await clockResync('light');
      } catch (e) {
        alert(String(e.message || e));
      }
    });
  }
  if (fullBtn && fullBtn.dataset.tapWired !== '1') {
    fullBtn.dataset.tapWired = '1';
    KFTap.bindTap(fullBtn, async () => {
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
  const linkCls = podLinkClass(p);
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
  const droppedReadCls = p.recent_drops && (p.dropped_readings || 0) > 0 ? 'warn' : '';
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
  const linkCls = podLinkClass(p);
  let parts = ['Pod'];
  if (p.protect_sleep) {
    parts.push('protect');
  } else if (p.power_mode === 'burst' && !p.connected) {
    if (p.burst_lost) {
      const age = Number.isFinite(p.status_age_s) && p.status_age_s >= 0 ? ` ${Math.round(p.status_age_s / 60)}m` : '';
      parts.push(`silent${age}`);
    } else {
      parts.push(p.burst_quiet ? 'burst' : 'burst overdue');
    }
  } else if (p.has_rssi && Number.isFinite(p.rssi_dbm)) {
    parts.push(`${p.rssi_dbm} dBm`);
  } else if (!p.connected) {
    parts.push('off');
  }
  if (p.has_battery_telemetry || p.has_battery) {
    parts.push(formatBatteryHeadline(p));
  }
  return `<button type="button" class="statusChip statusChip-${linkCls}" data-open-status="pod">${escapeHtml(parts.join(' '))}</button>`;
}

// compactSystemChip surfaces Pi host health — chiefly undervoltage, the
// root cause of the 2026-07-11 in-flight AP loss. Live throttle/undervolt
// flags show as a red warning; sticky "since boot" flags show as a yellow
// warning even after recovery; otherwise a compact supply/temp headline.
// Clicking opens the `system` device tab with the full value list.
function compactSystemChip() {
  if (!state.serverConnected) return '';
  const sm = state.devices.get('system');
  const v = sm && sm.values;
  if (!v) return '';

  const nowFlags = [
    ['undervolt_now', 'Undervoltage'],
    ['throttled_now', 'Throttled'],
    ['soft_temp_now', 'Overtemp'],
  ];
  const active = nowFlags.filter(([k]) => v[k] >= 1).map(([, l]) => l);
  if (active.length) {
    return `<button type="button" class="statusChip statusChip-off" data-goto-sensor="system">${escapeHtml('⚠ ' + active.join(' + '))}</button>`;
  }

  const parts = [];
  if (Number.isFinite(v.supply_v)) parts.push(v.supply_v.toFixed(1) + 'V');
  if (Number.isFinite(v.cpu_temp_c)) parts.push(Math.round(v.cpu_temp_c) + '°C');
  let text = 'Sys ' + (parts.join(' ') || '?');

  const sinceFlags = [
    ['undervolt_since_boot', 'UV'],
    ['throttled_since_boot', 'throttle'],
    ['soft_temp_since_boot', 'overtemp'],
  ];
  const since = sinceFlags.filter(([k]) => v[k] >= 1).map(([, l]) => l);
  const cls = since.length ? 'warn' : 'ok';
  if (since.length) text += ' · ⚠ ' + since.join(',') + ' since boot';

  return `<button type="button" class="statusChip statusChip-${cls}" data-goto-sensor="system">${escapeHtml(text)}</button>`;
}

function renderStatusChips() {
  if (!statusChipsEl) return;
  const chips = [compactSystemChip(), compactClockChip(), compactPodChip()].filter(Boolean);
  statusChipsEl.innerHTML = chips.join('') || '<span class="dim">Status loading…</span>';
  updateClockBanner();
}

// clockBannerText returns the prominent-warning text for an unsynced clock, or
// null when the banner should be hidden. Kingfisher now starts (UI + recording)
// before chrony syncs; this banner is the pilot's cue that timestamps are
// provisional until the clock locks (corrections land in clock_offsets).
function clockBannerText(clock) {
  if (!clock) return null; // no clock state yet — stale banner covers outages
  const d = clock.discipline;
  if (d?.available && d.synced) return null;
  return '⚠ CLOCK NOT SYNCED — timestamps provisional';
}

function updateClockBanner() {
  const el = document.getElementById('clockBanner');
  if (!el) return;
  const text = state.serverConnected ? clockBannerText(state.clock) : null;
  if (!text) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.textContent = text;
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
  scheduleUiRefresh();
}

let uiRefreshPending = false;

function modalOpen() {
  return !!document.querySelector('dialog[open]');
}

function scheduleUiRefresh() {
  if (uiRefreshPending) return;
  uiRefreshPending = true;
  requestAnimationFrame(() => {
    uiRefreshPending = false;
    if (modalOpen()) return;
    if (state.routeView === 'sensors' && !state.activeSensor) {
      renderOverview(false);
    } else if (state.activeSensor) {
      renderActiveSensor();
    } else if (state.routeView === 'instruments') {
      renderInstruments();
    }
    markStaleness();
  });
}

function escapeAttr(s) { return String(s ?? '').replace(/"/g, '&quot;'); }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

function wireAttrEdits(name) {
  for (const inp of panelRegions.attrs.querySelectorAll('.attrRow:not(.smoothRow) input, .attrRow:not(.smoothRow) select')) {
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

function noteWsSnapshot(snap) {
  const now = Date.now();
  state.lastWsAt = now;
  if (!snap || !snap.devices) return;
  for (const name of Object.keys(snap.devices)) {
    state.deviceSeenAt.set(name, now);
  }
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    state.wsOpen = true;
    state.lastWsAt = Date.now();
    setServerConnected(true);
    refreshStatus();
  };
  ws.onmessage = (ev) => {
    state.wsOpen = true;
    setServerConnected(true);
    let snap;
    try { snap = JSON.parse(ev.data); } catch { return; }
    if (!snap || !snap.devices) return;
    noteWsSnapshot(snap);
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
  ws.onerror = () => {
    state.wsOpen = false;
    setServerConnected(false);
  };
  ws.onclose = () => {
    state.wsOpen = false;
    setServerConnected(false);
    setTimeout(connect, 1000);
  };
}

function setServerConnected(connected) {
  if (state.serverConnected === connected) return;
  state.serverConnected = connected;
  updateRecordingUI();
  setPowerOffUI();
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
      if (!state.wsOpen) setServerConnected(false);
      renderStatusChips();
      return;
    }
    if (state.wsOpen) setServerConnected(true);
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
    if (typeof s.poweroff_available === 'boolean') state.powerOffAvailable = s.poweroff_available;
    setPowerOffUI();
    state.recording = s.recording || null;
    updateRecordingUI();
    state.podLink = s.pod || null;
    state.clock = s.clock || null;
    renderStatusChips();
  } catch {
    if (!state.wsOpen) setServerConnected(false);
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
    if (r.ok) {
      state.config = await r.json();
      KFSmooth.setConfig(state.config?.display);
    }
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

// --- Trusted devices (AP allowlist) ---
// ---- Flights summary dialog ----

let flightsPollTimer = null;

function fmtFlightDuration(sec) {
  if (!Number.isFinite(sec) || sec <= 0) return '—';
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h > 0 ? `${h} h ${String(m).padStart(2, '0')} m` : `${m} m`;
}

function fmtFlightWhen(startUtc) {
  if (!startUtc) return '—';
  const d = new Date(startUtc);
  if (Number.isNaN(d.getTime())) return startUtc;
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}Z`;
}

function flightRouteHTML(f) {
  if (f.ground) return '<span class="ground">XXXX — ground session</span>';
  // "···" = data boundary (fix acquired after takeoff / recording stopped
  // before landing); "????" = genuinely no airport within range.
  const t = f.takeoff?.ident
    ? escapeHtml(f.takeoff.ident)
    : (f.starts_airborne ? '<span class="dim" title="data begins airborne">···</span>' : '????');
  const l = f.landing?.ident
    ? escapeHtml(f.landing.ident)
    : (f.ends_airborne ? '<span class="dim" title="data ends airborne">···</span>' : '????');
  const legs = f.legs > 1 ? ` <span class="dim">(${f.legs} legs)</span>` : '';
  return `${t} → ${l}${legs}`;
}

function flightBadgeHTML(f) {
  const map = {
    recording: ['rec', '● REC'],
    yes: ['yes', '✓ NAS'],
    stale: ['stale', '⟳ stale'],
    no: ['no', '✗ not backed up'],
  };
  const [cls, label] = map[f.backup] || ['no', f.backup || '—'];
  return `<span class="flightBadge flightBadge-${cls}">${label}</span>`;
}

function renderFlights(data) {
  const rows = [];
  for (const f of data.flights || []) {
    const alt = f.max_alt_msl_m > 0
      ? `${Math.round(f.max_alt_msl_m * 3.28084).toLocaleString()} ft`
      : '—';
    const stats = f.scan_error === 'pending'
      ? '<span class="dim">scanning…</span>'
      : f.scan_error
        ? `<span class="dim">scan failed: ${escapeHtml(f.scan_error)}</span>`
        : `${fmtFlightDuration(f.duration_s)} block` +
          (f.ground ? '' : ` · ${fmtFlightDuration(f.airborne_s)} air · max ${alt}`);
    const warn = f.unsynced ? ' <span title="clock was unsynced at start">⚠</span>' : '';
    const notes = f.notes
      ? escapeHtml(f.notes)
      : '<span class="placeholder">add notes…</span>';
    rows.push(
      `<div class="flightRow" data-file="${escapeAttr(f.file)}">` +
        `<span class="flightRoute">${flightRouteHTML(f)}${warn}</span>` +
        flightBadgeHTML(f) +
        `<span class="flightWhen dim">${escapeHtml(fmtFlightWhen(f.start_utc))}` +
          ` · ${(f.size_bytes / 1048576).toFixed(0)} MB</span>` +
        `<span class="flightStats">${stats}</span>` +
        `<span class="flightNotes" data-notes-file="${escapeAttr(f.file)}"` +
          ` data-notes="${escapeAttr(f.notes || '')}">${notes}</span>` +
      `</div>`);
  }
  flightsBodyEl.innerHTML = rows.join('') || '<p class="dim">No flight databases found.</p>';
  const meta = [];
  if (data.scanning) meta.push('scanning flight databases…');
  if (Number.isFinite(data.manifest_age_s) && data.manifest_age_s >= 0) {
    const h = data.manifest_age_s / 3600;
    meta.push(`backup state as of ${h < 1 ? Math.round(data.manifest_age_s / 60) + ' min' : h.toFixed(1) + ' h'} ago`);
  } else {
    meta.push('no backup manifest yet (runs after next NAS sync)');
  }
  flightsMetaEl.textContent = meta.join(' · ');
}

async function refreshFlights() {
  try {
    const r = await fetch('/api/flights');
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    renderFlights(data);
    if (data.scanning && flightsDlg.open) {
      clearTimeout(flightsPollTimer);
      flightsPollTimer = setTimeout(refreshFlights, 2000);
    }
  } catch (e) {
    flightsBodyEl.innerHTML = `<p class="dim">Failed to load flights: ${escapeHtml(e.message)}</p>`;
  }
}

function openFlightsDialog() {
  if (!flightsDlg) return;
  flightsBodyEl.innerHTML = '<p class="dim">Loading…</p>';
  flightsMetaEl.textContent = '';
  flightsDlg.showModal();
  refreshFlights();
}

function openFlightNotes(file, current) {
  const title = document.getElementById('flightNotesTitle');
  const text = document.getElementById('flightNotesText');
  title.textContent = `Notes — ${file.replace(/\.db$/, '')}`;
  text.value = current;
  flightNotesDlg._file = file;
  flightNotesDlg.showModal();
  text.focus();
}

function openAccessDialog() {
  if (!accessDlg) return;
  accessBodyEl.innerHTML = '<p class="dim">Loading…</p>';
  accessDlg.showModal();
  refreshAccess();
}

async function refreshAccess() {
  try {
    const r = await fetch('/api/access');
    if (r.status === 403) {
      accessBodyEl.innerHTML =
        '<p class="dim">This device isn’t trusted to manage the allowlist. ' +
        'Use a device on your home network, this Pi, or an already-trusted EFB.</p>';
      return;
    }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    renderAccessBody(await r.json());
  } catch (e) {
    accessBodyEl.innerHTML = `<p class="dim">Couldn’t load: ${escapeHtml(String(e.message || e))}</p>`;
  }
}

function accessRow(ip, mac, name, selfHtml, trusted) {
  const badge = trusted
    ? '<span class="accessBadge accessBadge-ok">trusted</span>'
    : '<span class="accessBadge accessBadge-block">blocked</span>';
  const btn = trusted
    ? `<button type="button" class="accessBtn" data-access-remove="${escapeHtml(ip)}">Untrust</button>`
    : `<button type="button" class="accessBtn accessBtn-add" data-access-add="${escapeHtml(ip)}">Trust</button>`;
  const macHtml = mac ? `<span class="dim accessMac">${escapeHtml(mac)}</span>` : '';
  const nameInput =
    `<input class="accessName" type="text" maxlength="64" placeholder="name…" ` +
    `data-access-name="${escapeHtml(ip)}" value="${escapeHtml(name || '')}" />`;
  return (
    `<div class="accessRow"><div class="accessId">` +
    nameInput +
    `<span class="accessSub"><span class="accessIp">${escapeHtml(ip)}</span>${macHtml}${selfHtml || ''}</span>` +
    `</div><div class="accessAct">${badge}${btn}</div></div>`
  );
}

function renderAccessBody(data) {
  const clients = data.clients || [];
  const trusted = new Set(data.trusted_ips || []);
  const names = data.names || {};
  const seen = new Set();
  let html = '<h3 class="accessH">On the access point</h3>';
  if (clients.length === 0) {
    html += '<p class="dim">No devices currently on the access point.</p>';
  } else {
    for (const c of clients) {
      seen.add(c.ip);
      const self = c.self ? ' <span class="dim">· this device</span>' : '';
      html += accessRow(c.ip, c.mac, c.name || '', self, !!c.trusted);
    }
  }
  const offline = [...trusted].filter((ip) => !seen.has(ip));
  if (offline.length) {
    html += '<h3 class="accessH">Trusted but not connected</h3>';
    for (const ip of offline) html += accessRow(ip, '', names[ip] || '', '', true);
  }
  accessBodyEl.innerHTML = html;
}

async function accessMutate(path, ip) {
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip }),
    });
    if (r.ok) {
      renderAccessBody(await r.json());
      return;
    }
  } catch {}
  refreshAccess();
}

// accessSetName saves a label without re-rendering (the input already shows the
// typed value); it only reloads on failure to restore the truth.
async function accessSetName(ip, name) {
  try {
    const r = await fetch('/api/access/name', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip, name }),
    });
    if (r.ok) return;
  } catch {}
  refreshAccess();
}

if (accessBodyEl) {
  accessBodyEl.addEventListener('click', (ev) => {
    const add = ev.target.closest('[data-access-add]');
    if (add) return accessMutate('/api/access/add', add.dataset.accessAdd);
    const rm = ev.target.closest('[data-access-remove]');
    if (rm) return accessMutate('/api/access/remove', rm.dataset.accessRemove);
  });
  accessBodyEl.addEventListener('change', (ev) => {
    const inp = ev.target.closest('input[data-access-name]');
    if (inp) accessSetName(inp.dataset.accessName, inp.value);
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

// Stale when the WebSocket stops delivering a device (not when sample.ts_ns
// ages on the Pi — phone/Pi clock skew and 1 Hz GPS should not grey tiles).
const STALENESS_MS = 4000;

function markStaleness() {
  const now = Date.now();
  const linkAgeMs = state.lastWsAt ? now - state.lastWsAt : Infinity;
  for (const name of state.deviceNames) {
    const seen = state.deviceSeenAt.get(name) ?? 0;
    const ageMs = seen ? now - seen : Infinity;
    const stale = !state.wsOpen || ageMs > STALENESS_MS;
    for (const el of document.querySelectorAll(`[data-device="${CSS.escape(name)}"]`)) {
      el.classList.toggle('kf-stale', stale);
      if (stale && Number.isFinite(ageMs)) {
        el.dataset.staleSec = String(Math.round(ageMs / 1000));
      } else {
        delete el.dataset.staleSec;
      }
    }
  }
  const banner = document.getElementById('staleBanner');
  if (!banner) return;
  if (state.wsOpen && linkAgeMs < STALENESS_MS) {
    banner.hidden = true;
    return;
  }
  if (linkAgeMs < STALENESS_MS) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  banner.textContent = `Link interrupted — last update ${Math.round(linkAgeMs / 1000)} s ago`;
}
setInterval(markStaleness, 1000);

function wireUiTaps() {
  KFTap.wireDialogCloses();
  KFTap.wireCheckboxLabels();

  document.addEventListener(
    'close',
    (ev) => {
      if (ev.target instanceof HTMLDialogElement) scheduleUiRefresh();
    },
    true
  );

  KFTap.bindPress(statusChipsEl, '[data-open-status]', () => openStatusDrawer());
  KFTap.bindPress(statusChipsEl, '[data-goto-sensor]', (ev, el) => {
    const dev = el.dataset.gotoSensor;
    if (dev) setRoute(`#/sensor/${encodeURIComponent(dev)}`);
  });

  KFTap.bindTap(detailBackEl, () => setRoute('#/'));

  KFTap.bindTap(pauseBtn, async () => {
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

  KFTap.bindTap(powerOffBtn, () => {
    if (!state.powerOffAvailable || powerOffPending || !state.serverConnected) return;
    powerOffDlg.showModal();
  });

  KFTap.bindTap(document.getElementById('powerOffConfirm'), async () => {
    powerOffDlg.close();
    powerOffPending = true;
    setPowerOffUI();
    document.body.classList.add('shutting-down');
    try {
      const r = await fetch('/api/power/off', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      });
      if (!r.ok) {
        powerOffPending = false;
        document.body.classList.remove('shutting-down');
        setPowerOffUI();
        alert('Power off failed');
      }
    } catch {
      // Connection loss is expected while the Pi shuts down.
    }
  });

  KFTap.bindTap(menuBtn, () => {
    refreshStatus();
    moreSheet.showModal();
  });

  KFTap.bindTap(document.getElementById('moreTerminalBtn'), () => {
    moreSheet.close();
    location.href = '/terminal';
  });

  KFTap.bindTap(document.getElementById('moreAccessBtn'), () => {
    moreSheet.close();
    openAccessDialog();
  });

  KFTap.bindTap(document.getElementById('moreFlightsBtn'), () => {
    moreSheet.close();
    openFlightsDialog();
  });

  if (flightsBodyEl) {
    flightsBodyEl.addEventListener('click', (e) => {
      const el = e.target.closest('[data-notes-file]');
      if (!el) return;
      openFlightNotes(el.dataset.notesFile, el.dataset.notes || '');
    });
  }

  KFTap.bindTap(document.getElementById('flightNotesSave'), async () => {
    const file = flightNotesDlg._file;
    const notes = document.getElementById('flightNotesText').value;
    try {
      const r = await fetch('/api/flights/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file, notes }),
      });
      if (!r.ok) throw new Error(await r.text());
      flightNotesDlg.close();
      refreshFlights();
    } catch (err) {
      alert(`Saving notes failed: ${err.message}`);
    }
  });

  KFTap.bindTap(document.getElementById('moreSettingsBtn'), () => {
    moreSheet.close();
    openSettingsDialog();
  });

  KFTap.bindTap(document.getElementById('moreStatusBtn'), () => {
    moreSheet.close();
    openStatusDrawer();
  });

  KFTap.bindTap(document.getElementById('cfgSave'), async (e) => {
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

  for (const btn of document.querySelectorAll('#bottomNav .bottomNavBtn')) {
    KFTap.bindTap(btn, () => {
      const nav = btn.dataset.nav;
      if (nav === 'sensors') {
        setRoute('#/');
      } else if (nav === 'instruments') {
        setRoute('#/instruments');
      } else if (nav === 'howgozit') {
        setRoute('#/howgozit');
      } else if (nav === 'more') {
        refreshStatus();
        moreSheet.showModal();
      }
    });
  }

  wireOverviewNav();
}

function wireOverviewNav() {
  if (!viewOverviewEl || viewOverviewEl.dataset.navWired === '1') return;
  viewOverviewEl.dataset.navWired = '1';
  KFTap.bindPress(viewOverviewEl, '.ovBlock', (ev, block) => {
    const device = block.dataset.device;
    if (device) setRoute(`#/sensor/${encodeURIComponent(device)}`);
  }, { stableKey: 'device', slop: 12 });
  viewOverviewEl.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const block = ev.target.closest('.ovBlock');
    if (!block) return;
    ev.preventDefault();
    const device = block.dataset.device;
    if (device) setRoute(`#/sensor/${encodeURIComponent(device)}`);
  });
}

window.addEventListener('hashchange', applyRoute);
window.addEventListener('pageshow', (ev) => {
  if (ev.persisted && state.routeView === 'howgozit') renderHowgozit();
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
  wireUiTaps();
  if (!location.hash || location.hash === '#') {
    location.hash = '#/';
  }
  applyRoute();
  connect();
  refreshStatus();
})();

setInterval(refreshStatus, 2000);
