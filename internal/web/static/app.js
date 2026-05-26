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
};

const tabsEl = document.getElementById('tabs');
const panelEl = document.getElementById('panel');
const bufEl   = document.getElementById('bufStat');
const dbEl    = document.getElementById('dbSize');

const DERIVED_DEVICES = ['ahrs', 'press_alt'];
// Wing-pod sensor tabs (matches pod.DefaultPodDeviceNames).
const POD_TELEMETRY_DEVICES = new Set(['bmp581', 'mmc5983', 'ms4525']);
const HUB_VIRTUAL_DEVICES = new Set(['gps', 'geo', ...DERIVED_DEVICES]);

function isPodTelemetry(name) {
  return state.deviceLocation.get(name) === 'pod';
}

function inferDeviceLocation(name) {
  if (HUB_VIRTUAL_DEVICES.has(name)) return 'hub';
  if (POD_TELEMETRY_DEVICES.has(name)) return 'pod';
  if (state.iioDevices.has(name)) return 'hub';
  return null;
}

function applyDeviceLocation(name, loc) {
  if (!loc) return;
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
  if (isPodTelemetry(name) || state.iioDevices.has(name) || !state.attrs.has(name)) {
    loadAttrs(name);
  }
  renderActiveTab();
}

// Two-region panel: a live-values div (re-rendered on every WS tick) and
// an attrs div (re-rendered only on tab switch or attr fetch). Keeping
// attrs untouched on snapshot updates preserves focus while the user is
// typing into an editable attribute.
function rebuildPanel() {
  panelEl.innerHTML =
    `<div id="liveKV"></div><div id="attrsBox"></div>`;
  return {
    kv:    document.getElementById('liveKV'),
    attrs: document.getElementById('attrsBox'),
  };
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
    const out = KFDisplay.formatValue(name, k, vals[k]);
    const vCell = out.html ?? escapeHtml(String(out.text ?? ''));
    const label = escapeHtml(KFDisplay.channelLabel(name, k));
    const rowCls = KFDisplay.rowClass(name, k);
    html += `<div class="kv${rowCls}"><div class="k">${label}</div><div class="v">${vCell}</div></div>`;
  }
  html += KFDisplay.gpsFootnote(name);
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

function attrLabel(device, a) {
  if (device === 'press_alt' && !a.channel && a.attr === 'kollsman_inhg') {
    return 'Kollsman (inHg)';
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
  let battText = '—';
  if (p.has_battery) {
    battText = `${Number(p.battery_v).toFixed(2)} V`;
  }
  el.className = `podStatus podStatus-${linkCls}`;
  el.innerHTML =
    `<span class="podStatusItem"><span class="lbl">Pod</span> ${escapeHtml(podLinkLabel(p))}</span>` +
    `<span class="podStatusItem ${rssiCls}"><span class="lbl">RSSI</span> ${escapeHtml(rssiText)}</span>` +
    `<span class="podStatusItem"><span class="lbl">Batt</span> ${escapeHtml(battText)}</span>`;
}

function renderActiveTab() {
  renderLiveValues();
  renderAttrs();
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
  try {
    const r = await fetch(`/api/devices/${encodeURIComponent(name)}/attrs`);
    if (!r.ok) return;
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
  ws.onmessage = (ev) => {
    let snap;
    try { snap = JSON.parse(ev.data); } catch { return; }
    if (!snap || !snap.devices) return;
    for (const [name, sample] of Object.entries(snap.devices)) {
      state.devices.set(name, sample);
      ensureTab(name);
      if (state.activeTab === name && !state.attrs.has(name)) {
        loadAttrs(name);
      }
    }
    renderLiveValues();
  };
  ws.onclose = () => setTimeout(connect, 1000);
}

const tailEl     = document.querySelector('#hdr .tail');
const recDotEl   = document.getElementById('recDot');
const recLabelEl = document.getElementById('recLabel');
const pauseBtn   = document.getElementById('pauseBtn');

function setPausedUI(paused) {
  state.paused = paused;
  if (recDotEl)   recDotEl.classList.toggle('paused', paused);
  if (recLabelEl) recLabelEl.textContent = paused ? 'PAUSED' : 'REC';
  if (pauseBtn) {
    pauseBtn.textContent = paused ? '▶' : '⏸';
    pauseBtn.title = paused ? 'Resume recording' : 'Pause recording';
  }
}

pauseBtn.addEventListener('click', async () => {
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
    if (!r.ok) return;
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
    renderPodStatus();
  } catch {}
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

(async function init() {
  if (window.KF_INITIAL_DEVICES) {
    for (const d of window.KF_INITIAL_DEVICES) ensureTab(d);
  }
  await loadIIODevices();
  await preloadDeviceLocations();
  connect();
  refreshStatus();
})();
setInterval(refreshStatus, 2000);
