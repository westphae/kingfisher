// Presentation-only EWMA per sensor group. Raw hub samples stay in state.devices;
// consumers call KFSmooth.values(device, rawValues) before formatting.
const KFSmooth = (function () {
  const GROUP_DEFAULTS = {
    accel: { mode: 'smoothed', tau_s: 0.25 },
    gyro: { mode: 'smoothed', tau_s: 0.25 },
    mag: { mode: 'smoothed', tau_s: 0.5 },
    default: { mode: 'smoothed', tau_s: 0.35 },
    heading: { mode: 'smoothed', tau_s: 0.5 },
    field: { mode: 'smoothed', tau_s: 1.0 },
    pos: { mode: 'smoothed', tau_s: 2.0 },
    vel: { mode: 'smoothed', tau_s: 2.0 },
    acc: { mode: 'smoothed', tau_s: 2.0 },
    fix: { mode: 'raw', tau_s: 0.5 },
  };

  const DEVICE_GROUP_DEFAULTS = {
    airspeed: { default: { mode: 'raw', tau_s: 0.5 } },
    geo: { default: { mode: 'smoothed', tau_s: 5.0 } },
    press_alt: { default: { mode: 'smoothed', tau_s: 1.5 } },
    bmp581: { default: { mode: 'smoothed', tau_s: 0.5 } },
    ms4525: { default: { mode: 'smoothed', tau_s: 0.5 } },
    bq27441: { default: { mode: 'smoothed', tau_s: 2.0 } },
    compass: { default: { mode: 'smoothed', tau_s: 0.5 } },
  };

  const SINGLETON_DEFAULT = { mode: 'raw', tau_s: 0.5 };

  let smoothConfig = {};
  const filterState = new Map();

  function setConfig(display) {
    smoothConfig = display?.smooth ?? {};
  }

  function defaultGroup(device, groupId) {
    const dev = DEVICE_GROUP_DEFAULTS[device];
    if (dev && dev[groupId]) return { ...dev[groupId] };
    if (GROUP_DEFAULTS[groupId]) return { ...GROUP_DEFAULTS[groupId] };
    return { ...SINGLETON_DEFAULT };
  }

  function savedGroup(device, groupId) {
    return smoothConfig[device]?.[groupId] ?? null;
  }

  function effectiveGroup(device, groupId) {
    const saved = savedGroup(device, groupId);
    const def = defaultGroup(device, groupId);
    if (!saved || (saved.mode !== 'raw' && saved.mode !== 'smoothed')) {
      return def;
    }
    let tau = saved.tau_s;
    if (saved.mode === 'smoothed') {
      if (!Number.isFinite(tau) || tau <= 0) {
        tau = def.tau_s > 0 ? def.tau_s : 0.5;
      }
    } else if (!Number.isFinite(tau)) {
      tau = def.tau_s > 0 ? def.tau_s : 0.5;
    }
    return { mode: saved.mode, tau_s: tau };
  }

  function uiGroup(device, groupId) {
    const saved = savedGroup(device, groupId);
    const def = defaultGroup(device, groupId);
    const mode = saved?.mode === 'raw' || saved?.mode === 'smoothed' ? saved.mode : def.mode;
    let tau = saved?.tau_s;
    if (!Number.isFinite(tau) || tau <= 0) {
      tau = def.tau_s > 0 ? def.tau_s : 0.5;
    }
    return { mode, tau_s: tau };
  }

  function isAngleChannel(ch) {
    return ch === 'roll' || ch === 'pitch' || ch === 'yaw' || ch === 'track'
      || ch === 'declination' || ch === 'inclination'
      || /^heading_/.test(ch);
  }

  function emaAlpha(dtSec, tauSec) {
    if (tauSec <= 0) return 1;
    return 1 - Math.exp(-dtSec / tauSec);
  }

  function clearGroup(device, channels) {
    for (const ch of channels) {
      filterState.delete(`${device}:${ch}`);
    }
  }

  function updateChannel(device, channel, raw, tauSec, nowMs) {
    const key = `${device}:${channel}`;
    let st = filterState.get(key);
    if (st == null) {
      if (isAngleChannel(channel)) {
        const rad = (raw * Math.PI) / 180;
        st = { sinY: Math.sin(rad), cosY: Math.cos(rad), lastMs: nowMs, angle: true };
      } else {
        st = { y: raw, lastMs: nowMs, angle: false };
      }
      filterState.set(key, st);
      return raw;
    }
    const dtSec = Math.max(0.001, (nowMs - st.lastMs) / 1000);
    st.lastMs = nowMs;
    const alpha = emaAlpha(dtSec, tauSec);
    if (st.angle) {
      const rad = (raw * Math.PI) / 180;
      const sinR = Math.sin(rad);
      const cosR = Math.cos(rad);
      st.sinY += alpha * (sinR - st.sinY);
      st.cosY += alpha * (cosR - st.cosY);
      return (Math.atan2(st.sinY, st.cosY) * 180) / Math.PI;
    }
    st.y += alpha * (raw - st.y);
    return st.y;
  }

  function values(device, rawValues, nowMs) {
    rawValues = rawValues || {};
    if (nowMs == null) nowMs = Date.now();
    const groups = KFDisplay.listSmoothGroups(device, rawValues);
    const out = { ...rawValues };
    const touched = new Set();

    for (const g of groups) {
      const { mode, tau_s: tau } = effectiveGroup(device, g.id);
      for (const ch of g.channels) {
        touched.add(ch);
        const raw = rawValues[ch];
        if (KFDisplay.noSmoothChannel(ch)) {
          out[ch] = raw;
          continue;
        }
        if (raw == null || typeof raw !== 'number' || !Number.isFinite(raw)) {
          out[ch] = raw;
          continue;
        }
        if (mode === 'raw') {
          out[ch] = raw;
          continue;
        }
        out[ch] = updateChannel(device, ch, raw, tau, nowMs);
      }
    }
    return out;
  }

  function sampleValues(sample, device, nowMs) {
    if (!sample?.values) return sample;
    return { ...sample, values: values(device, sample.values, nowMs) };
  }

  return {
    setConfig,
    values,
    sampleValues,
    effectiveGroup,
    uiGroup,
    clearGroup,
    listGroups(device, rawValues) {
      return KFDisplay.listSmoothGroups(device, rawValues);
    },
    groupLabel(device, groupId, channels) {
      return KFDisplay.smoothGroupLabel(device, groupId, channels);
    },
  };
})();
