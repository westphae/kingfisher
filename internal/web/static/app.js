// Kingfisher UI — vanilla JS, no build step. Maintains a per-device latest-
// value map updated from the /ws snapshot stream and re-renders the active
// tab on each tick.

const state = {
  devices: new Map(),          // name -> {ts_ns, values}
  activeTab: null,
  tabs: new Set(),
  attrs: new Map(),            // name -> [{channel, attr, value, writable}]
  iioDevices: new Set(),       // names from /api/devices (config sample_hz UI)
  podLink: null,               // latest /api/status pod object
};

const tabsEl = document.getElementById('tabs');
const panelEl = document.getElementById('panel');
const bufEl   = document.getElementById('bufStat');
const dbEl    = document.getElementById('dbSize');

const POD_DEVICE = 'pod';
const DERIVED_DEVICES = ['ahrs', 'press_alt'];

function tabRank(name) {
  if (state.iioDevices.has(name)) return 0;
  if (name === POD_DEVICE) return 1;
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

function reorderTabs() {
  for (const name of sortedTabNames()) {
    const btn = tabsEl.querySelector(`button[data-tab="${name}"]`);
    if (btn) tabsEl.appendChild(btn);
  }
}

function ensureTab(name) {
  if (state.tabs.has(name)) return;
  state.tabs.add(name);
  const btn = document.createElement('button');
  btn.textContent = name;
  btn.dataset.tab = name;
  btn.addEventListener('click', () => selectTab(name));
  tabsEl.appendChild(btn);
  reorderTabs();
  if (!state.activeTab) selectTab(sortedTabNames()[0]);
}

function selectTab(name) {
  state.activeTab = name;
  for (const b of tabsEl.querySelectorAll('button')) {
    b.classList.toggle('active', b.dataset.tab === name);
  }
  if (!state.attrs.has(name)) {
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
    `<div id="podBanner" class="podBanner" hidden></div>` +
    `<div id="liveKV"></div><div id="attrsBox"></div>`;
  return {
    banner: document.getElementById('podBanner'),
    kv:     document.getElementById('liveKV'),
    attrs:  document.getElementById('attrsBox'),
  };
}
let panelRegions = rebuildPanel();

// Matches derive.PressureSource* on press_alt.pressure_source.
const PRESSURE_SOURCE_LABEL = {
  1: 'pod (wing static_pressure_pa, BMP581)',
  2: 'cabin IIO baro (pressure_pa, e.g. bmp280)',
};

function pressAltSourceLabel(code) {
  const n = Number(code);
  return PRESSURE_SOURCE_LABEL[n] || `unknown (${fmt(n)})`;
}

function renderLiveValues() {
  const name = state.activeTab;
  if (!name) { panelRegions.kv.innerHTML = ''; return; }
  const sm = state.devices.get(name);
  if (!sm) { panelRegions.kv.innerHTML = ''; return; }
  const vals = sm.values || {};
  let html = '';
  const keys = Object.keys(vals).sort();
  for (const k of keys) {
    const v = k === 'pressure_source'
      ? pressAltSourceLabel(vals[k])
      : fmt(vals[k]);
    html += `<div class="kv"><div class="k">${k}</div><div class="v">${escapeHtml(String(v))}</div></div>`;
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
  let html = `<section class="attrs"><h3>Settings</h3>`;
  if (!attrs) {
    html += `<div class="dim">(loading…)</div>`;
  } else if (attrs.length === 0) {
    html += `<div class="dim">(no editable attributes)</div>`;
  } else {
    for (const a of attrs) {
      const label = a.channel ? `${a.channel} ${a.attr}` : a.attr;
      html += `<div class="attrRow"><div class="k">${label}</div><div class="v">${renderAttrInput(a)}</div></div>`;
    }
  }
  html += `</section>`;
  panelRegions.attrs.innerHTML = html;
  wireAttrEdits(name);
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

function renderPodBanner() {
  const el = panelRegions.banner;
  if (!el) return;
  if (state.activeTab !== POD_DEVICE) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const p = state.podLink;
  el.hidden = false;
  const linkCls = !p || !p.enabled || !p.connected
    ? 'off'
    : ((p.rx_dropped || 0) > 0 ? 'warn' : 'ok');
  let rssiText = '—';
  let rssiCls = '';
  if (p && p.has_rssi) {
    rssiText = formatRssi(p.rssi_dbm);
    rssiCls = rssiClass(p.rssi_dbm);
  }
  let battText = '—';
  if (p && p.has_battery) {
    battText = `${Number(p.battery_v).toFixed(2)} V`;
  }
  el.innerHTML =
    `<div class="podBannerInner podBanner-${linkCls}">` +
    `<span class="podBannerItem"><span class="lbl">Link</span> ${escapeHtml(podLinkLabel(p))}</span>` +
    `<span class="podBannerItem ${rssiCls}"><span class="lbl">RSSI</span> ${escapeHtml(rssiText)}</span>` +
    `<span class="podBannerItem"><span class="lbl">Battery</span> ${escapeHtml(battText)}</span>` +
    `</div>`;
}

function renderActiveTab() {
  renderPodBanner();
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
          const updated = await r.json();
          state.attrs.set(name, updated);
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

async function loadAttrs(name) {
  try {
    const r = await fetch(`/api/devices/${encodeURIComponent(name)}/attrs`);
    if (!r.ok) return;
    const attrs = await r.json();
    state.attrs.set(name, attrs || []);
    if (state.activeTab === name) renderAttrs();
  } catch {}
}

function fmt(v) {
  if (typeof v !== 'number' || !isFinite(v)) return v;
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toFixed(1);
  if (abs >= 1)    return v.toFixed(3);
  if (abs >= 0.01) return v.toFixed(4);
  return v.toExponential(2);
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
    reorderTabs();
    renderLiveValues();
  };
  ws.onclose = () => setTimeout(connect, 1000);
}

const tailEl     = document.querySelector('#hdr .tail');
const recDotEl   = document.getElementById('recDot');
const recLabelEl = document.getElementById('recLabel');
const pauseBtn   = document.getElementById('pauseBtn');
const podTagEl   = document.getElementById('podTag');

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

function setPodUI(pod) {
  if (!podTagEl) return;
  podTagEl.textContent = 'POD';
  if (!pod || !pod.enabled) {
    podTagEl.className = 'podTag pod-off';
    podTagEl.title = 'Pod ingest not running';
    return;
  }
  if (!pod.connected) {
    podTagEl.className = 'podTag pod-off';
    podTagEl.title = 'No recent traffic from wing pod';
    return;
  }
  if ((pod.rx_dropped || 0) > 0) {
    podTagEl.className = 'podTag pod-warn';
    podTagEl.title = `Pod link up; ${pod.rx_dropped} batch gap(s) this session`;
    return;
  }
  podTagEl.className = 'podTag pod-ok';
  podTagEl.title = 'Pod link OK (no dropped batches)';
}

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
    setPodUI(s.pod);
    if (state.activeTab === POD_DEVICE) renderPodBanner();
  } catch {}
}

async function loadIIODevices() {
  try {
    const r = await fetch('/api/devices');
    if (!r.ok) return;
    const devices = await r.json();
    const iio = (devices || []).map(d => d.name).sort();
    state.iioDevices = new Set(iio);
    for (const name of iio) ensureTab(name);
    reorderTabs();
    // Eager-load attrs for the active tab (IIO + virtual devices like pod).
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

// Settings dialog
const dlg = document.getElementById('settingsDlg');
const devicesFs = document.getElementById('cfgDevices');

function renderDevicesUI(devices) {
  // Wipe existing rows but keep the <legend>.
  for (const node of [...devicesFs.children]) {
    if (node.tagName !== 'LEGEND') node.remove();
  }
  if (!devices || devices.length === 0) {
    const empty = document.createElement('div');
    empty.textContent = '(no IIO sensors discovered)';
    empty.style.color = 'var(--dim)';
    devicesFs.appendChild(empty);
    return;
  }
  for (const d of devices) {
    const row = document.createElement('div');
    row.className = 'devRow';
    row.dataset.name = d.name;
    row.innerHTML = `
      <span>${d.name}</span>
      <input type="number" min="1" max="1000" step="1" value="${d.sample_hz || 10}">
      <label class="en"><input type="checkbox" ${d.enabled ? 'checked' : ''}> on</label>`;
    devicesFs.appendChild(row);
  }
}

document.getElementById('settingsBtn').addEventListener('click', async () => {
  const [cfgR, devR] = await Promise.all([fetch('/api/config'), fetch('/api/devices')]);
  const cfg = await cfgR.json();
  const devices = devR.ok ? await devR.json() : [];
  document.getElementById('cfgAircraft').value = cfg.aircraft || '';
  document.getElementById('cfgNotes').value    = cfg.notes || '';
  document.getElementById('cfgFlush').value    = cfg.flush_seconds || 5;
  renderDevicesUI(devices);
  dlg._cfg = cfg;
  dlg.showModal();
});

document.getElementById('cfgSave').addEventListener('click', async (e) => {
  e.preventDefault();
  const cfg = dlg._cfg || {};
  cfg.aircraft      = document.getElementById('cfgAircraft').value;
  cfg.notes         = document.getElementById('cfgNotes').value;
  cfg.flush_seconds = parseInt(document.getElementById('cfgFlush').value, 10) || 5;
  cfg.devices       = cfg.devices || {};
  for (const row of devicesFs.querySelectorAll('.devRow')) {
    const name = row.dataset.name;
    const hz = parseFloat(row.querySelector('input[type="number"]').value) || 0;
    const enabled = row.querySelector('input[type="checkbox"]').checked;
    const existing = cfg.devices[name] || {};
    cfg.devices[name] = { ...existing, enabled, sample_hz: hz };
  }
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  if (tailEl) tailEl.textContent = cfg.aircraft || '';
  dlg.close();
});

if (window.KF_INITIAL_DEVICES) {
  for (const d of window.KF_INITIAL_DEVICES) ensureTab(d);
}
connect();
refreshStatus();
loadIIODevices();
setInterval(refreshStatus, 2000);
