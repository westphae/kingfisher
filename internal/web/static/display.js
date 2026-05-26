// Live-value display registry: labels, formatters, and sort order.
// Loaded before app.js; exposes KFDisplay.
// Hub/DB column names are unchanged — display layer only.

const KFDisplay = (function () {
  const M_TO_FT = 3.28084;
  const MPS_TO_KT = 1.94384;
  const MPS_TO_FPM = 196.8503937;
  const RAD_TO_DEG = 180 / Math.PI;

  const GPS_ACCURACY = new Set(['h_acc', 'v_acc', 'gs_acc', 'vs_acc', 'track_acc']);

  const GPS_SORT = [
    'lat', 'lon', 'h_acc',
    'alt_msl', 'v_acc',
    'gs', 'gs_acc',
    'track', 'track_acc',
    'vs', 'vs_acc',
    'fix', 'sats',
  ];

  const GEO_SORT = [
    'declination', 'inclination',
    'field_x_nt', 'field_y_nt', 'field_z_nt',
    'field_h_nt', 'field_f_nt',
  ];

  const IMU_ACCEL = ['accel_x', 'accel_y', 'accel_z'];
  const IMU_GYRO = ['anglvel_x', 'anglvel_y', 'anglvel_z'];
  const IMU_MAG = ['magn_x', 'magn_y', 'magn_z'];
  const IMU_SORT = [
    ...IMU_ACCEL,
    ...IMU_GYRO,
    ...IMU_MAG,
    'temp_c', 'temp',
  ];

  const AHRS_SORT = ['roll', 'pitch', 'yaw', 'g_load', 'turn_rate_deg_s', 'slip_skid'];

  const PRESS_ALT_SORT = [
    'pressure_alt_m', 'pressure_alt_ft',
    'indicated_alt_m', 'indicated_alt_ft',
    'density_alt_ft',
    'pressure_pa', 'pressure_source',
  ];

  const POD_DEVICE_SORT = {
    bmp581: ['static_pressure_pa', 'static_temp_c'],
    bmp280: ['pressure_pa', 'temp_c'],
    mmc5983: ['mag_x_ut', 'mag_y_ut', 'mag_z_ut'],
    ms4525: ['airspeed_dp_pa', 'airspeed_temp_c'],
  };

  const PRESSURE_SOURCE_LABEL = {
    1: 'Wing pod (BMP581)',
    2: 'Cabin baro (e.g. BMP280)',
  };

  function fmtNum(v, decimals) {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    const abs = Math.abs(n);
    const opts = { maximumFractionDigits: decimals, minimumFractionDigits: 0 };
    if (abs >= 1000) {
      return n.toLocaleString(undefined, { ...opts, minimumFractionDigits: decimals });
    }
    return n.toFixed(decimals);
  }

  /** Fixed-width numeric field (character count includes sign). */
  function fmtPadded(v, decimals, width, signed) {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    let s;
    if (signed) {
      const sign = n >= 0 ? '+' : '-';
      s = sign + Math.abs(n).toFixed(decimals);
    } else {
      s = n.toFixed(decimals);
    }
    if (s.length > width) return s;
    return s.padStart(width, '\u2007'); // figure space — same width as digits
  }

  /** Dual metric/imperial row with fixed ch-width number columns. */
  function fmtDualHTML(mVal, mDec, mW, mUnit, iVal, iDec, iW, iUnit, signed) {
    const m = fmtPadded(mVal, mDec, mW, signed);
    const i = fmtPadded(iVal, iDec, iW, signed);
    return `<span class="du" style="--du-a:${mW}ch;--du-b:${iW}ch">` +
      `<span class="du-n">${m}</span><span class="du-u">${mUnit}</span>` +
      `<span class="du-sep">·</span>` +
      `<span class="du-n">${i}</span><span class="du-u">${iUnit}</span></span>`;
  }

  function fmtDualAccHTML(mVal, mDec, mW, mUnit, iVal, iDec, iW, iUnit) {
    const m = fmtPadded(mVal, mDec, mW, false);
    const i = fmtPadded(iVal, iDec, iW, false);
    return `<span class="du du-acc" style="--du-a:${mW}ch;--du-b:${iW}ch">` +
      `<span class="du-n">±${m}</span><span class="du-u">${mUnit}</span>` +
      `<span class="du-sep">·</span>` +
      `<span class="du-n">±${i}</span><span class="du-u">${iUnit}</span></span>`;
  }

  function fmtLengthAcc(m) {
    const ft = m * M_TO_FT;
    return fmtDualAccHTML(m, 1, 6, 'm', ft, 0, 5, 'ft');
  }

  function fmtPressurePa(v) {
    const abs = Math.abs(v);
    if (abs >= 1000) return fmtNum(v, 0);
    if (abs >= 1) return fmtNum(v, 1);
    return fmtNum(v, 2);
  }

  function fmtTempC(v) {
    return fmtNum(v, 2);
  }

  function fmtMicroT(v) {
    return fmtNum(v, 2);
  }

  /** IIO anglvel_* is rad/s in the hub; show °/s in the UI. */
  function fmtAnglvelDegS(v) {
    return fmtNum(v * RAD_TO_DEG, 2);
  }

  function fmtAccelMS2(v) {
    const abs = Math.abs(v);
    if (abs >= 10) return fmtNum(v, 2);
    if (abs >= 1) return fmtNum(v, 3);
    return fmtNum(v, 4);
  }

  function axisName(a) {
    return { x: 'X', y: 'Y', z: 'Z' }[a] || a.toUpperCase();
  }

  const GPS_SPECS = {
    lat: {
      label: 'Latitude',
      fmt(v) { return `${v.toFixed(6)}°`; },
    },
    lon: {
      label: 'Longitude',
      fmt(v) { return `${v.toFixed(6)}°`; },
    },
    alt_msl: {
      label: 'Altitude MSL',
      fmt(v) {
        const ft = v * M_TO_FT;
        return { html: fmtDualHTML(v, 1, 7, 'm', ft, 0, 6, 'ft', false) };
      },
    },
    gs: {
      label: 'Ground speed',
      fmt(v) {
        const kt = v * MPS_TO_KT;
        return { html: fmtDualHTML(v, 2, 7, 'm/s', kt, 1, 6, 'kt', false) };
      },
    },
    track: {
      label: 'Track (true)',
      fmt(v) { return `${fmtNum(v, 1)}°`; },
    },
    vs: {
      label: 'Vertical speed',
      fmt(v) {
        const fpm = v * MPS_TO_FPM;
        return { html: fmtDualHTML(v, 2, 7, 'm/s', fpm, 0, 7, 'fpm', true) };
      },
    },
    fix: {
      label: 'Fix mode',
      fmt(v) {
        const m = Math.round(v);
        if (m >= 3) return '3D';
        if (m === 2) return '2D';
        return 'No fix';
      },
    },
    sats: {
      label: 'Satellites in use',
      fmt(v) { return String(Math.round(v)); },
    },
    h_acc: {
      label: 'Horizontal accuracy (est.)',
      accuracy: true,
      fmt(m) { return { html: fmtLengthAcc(m) }; },
    },
    v_acc: {
      label: 'Vertical accuracy (est.)',
      accuracy: true,
      fmt(m) { return { html: fmtLengthAcc(m) }; },
    },
    gs_acc: {
      label: 'Ground speed accuracy (est.)',
      accuracy: true,
      fmt(v) {
        const kt = v * MPS_TO_KT;
        return { html: fmtDualAccHTML(v, 2, 6, 'm/s', kt, 1, 5, 'kt') };
      },
    },
    vs_acc: {
      label: 'Vertical speed accuracy (est.)',
      accuracy: true,
      fmt(v) {
        const fpm = v * MPS_TO_FPM;
        return { html: fmtDualAccHTML(v, 2, 6, 'm/s', fpm, 0, 6, 'fpm') };
      },
    },
    track_acc: {
      label: 'Track accuracy (est.)',
      accuracy: true,
      fmt(v) { return `±${fmtNum(v, 1)}°`; },
    },
  };

  /** Shared channel specs (hub column name → label + formatter). */
  const CHANNEL_SPECS = {
    // Baro (cabin IIO / derived)
    pressure_pa: {
      label: 'Pressure',
      fmt(v) { return `${fmtPressurePa(v)} Pa`; },
    },
    temp_c: {
      label: 'Temperature',
      fmt(v) { return `${fmtTempC(v)} °C`; },
    },
    temp: {
      label: 'Temperature',
      fmt(v) { return `${fmtTempC(v)} °C`; },
    },
    // Wing pod static (BMP581)
    static_pressure_pa: {
      label: 'Static pressure',
      fmt(v) { return `${fmtPressurePa(v)} Pa`; },
    },
    static_temp_c: {
      label: 'Static temperature',
      fmt(v) { return `${fmtTempC(v)} °C`; },
    },
    // Wing pod airspeed (MS4525)
    airspeed_dp_pa: {
      label: 'Differential pressure',
      fmt(v) { return `${fmtPressurePa(v)} Pa`; },
    },
    airspeed_temp_c: {
      label: 'Airspeed sensor temperature',
      fmt(v) { return `${fmtTempC(v)} °C`; },
    },
    // Wing pod mag (MMC5983) — already µT in hub
    mag_x_ut: {
      label: 'Magnetic field X',
      fmt(v) { return `${fmtMicroT(v)} µT`; },
    },
    mag_y_ut: {
      label: 'Magnetic field Y',
      fmt(v) { return `${fmtMicroT(v)} µT`; },
    },
    mag_z_ut: {
      label: 'Magnetic field Z',
      fmt(v) { return `${fmtMicroT(v)} µT`; },
    },
    // Derived pressure altitude
    pressure_alt_m: {
      label: 'Pressure altitude',
      fmt(v) {
        const ft = v * M_TO_FT;
        return `${fmtNum(v, 0)} m · ${fmtNum(ft, 0)} ft`;
      },
    },
    pressure_alt_ft: {
      label: 'Pressure altitude',
      fmt(v) { return `${fmtNum(v, 0)} ft`; },
    },
    indicated_alt_m: {
      label: 'Indicated altitude',
      fmt(v) {
        const ft = v * M_TO_FT;
        return `${fmtNum(v, 0)} m · ${fmtNum(ft, 0)} ft`;
      },
    },
    indicated_alt_ft: {
      label: 'Indicated altitude',
      fmt(v) { return `${fmtNum(v, 0)} ft`; },
    },
    density_alt_ft: {
      label: 'Density altitude',
      fmt(v) { return `${fmtNum(v, 0)} ft`; },
    },
    pressure_source: {
      label: 'Pressure source',
      fmt(v) {
        const n = Math.round(v);
        return PRESSURE_SOURCE_LABEL[n] || `Unknown (${n})`;
      },
    },
    // AHRS extras (angles already in DEVICE_SPECS.ahrs)
    slip_skid: {
      label: 'Slip / skid',
      fmt(v) { return `${fmtNum(v, 1)}°`; },
    },
    turn_rate_deg_s: {
      label: 'Turn rate',
      fmt(v) { return `${fmtNum(v, 2)}°/s`; },
    },
    g_load: {
      label: 'G load',
      fmt(v) { return `${fmtNum(v, 2)} G`; },
    },
  };

  const DEVICE_SPECS = {
    press_alt: {
      pressure_alt_m: CHANNEL_SPECS.pressure_alt_m,
      pressure_alt_ft: CHANNEL_SPECS.pressure_alt_ft,
      indicated_alt_m: CHANNEL_SPECS.indicated_alt_m,
      indicated_alt_ft: CHANNEL_SPECS.indicated_alt_ft,
      density_alt_ft: CHANNEL_SPECS.density_alt_ft,
      pressure_pa: CHANNEL_SPECS.pressure_pa,
      pressure_source: CHANNEL_SPECS.pressure_source,
    },
    ahrs: {
      roll:  { label: 'Roll',  fmt(v) { return `${fmtNum(v, 1)}°`; }},
      pitch: { label: 'Pitch', fmt(v) { return `${fmtNum(v, 1)}°`; }},
      yaw:   { label: 'Yaw',   fmt(v) { return `${fmtNum(v, 1)}°`; }},
      slip_skid: CHANNEL_SPECS.slip_skid,
      turn_rate_deg_s: CHANNEL_SPECS.turn_rate_deg_s,
      g_load: CHANNEL_SPECS.g_load,
    },
    geo: {
      declination: {
        label: 'Magnetic declination',
        fmt(v) { return `${fmtNum(v, 2)}°`; },
      },
      inclination: {
        label: 'Magnetic inclination',
        fmt(v) { return `${fmtNum(v, 2)}°`; },
      },
      field_x_nt: {
        label: 'Field X (north)',
        fmt(v) { return `${fmtNum(v / 1000, 2)} µT`; },
      },
      field_y_nt: {
        label: 'Field Y (east)',
        fmt(v) { return `${fmtNum(v / 1000, 2)} µT`; },
      },
      field_z_nt: {
        label: 'Field Z (down)',
        fmt(v) { return `${fmtNum(v / 1000, 2)} µT`; },
      },
      field_h_nt: {
        label: 'Horizontal field',
        fmt(v) { return `${fmtNum(v / 1000, 2)} µT`; },
      },
      field_f_nt: {
        label: 'Total field',
        fmt(v) { return `${fmtNum(v / 1000, 2)} µT`; },
      },
    },
    // Cabin / pod baro tabs (channel names match CHANNEL_SPECS)
    bmp280: {
      pressure_pa: CHANNEL_SPECS.pressure_pa,
      temp_c: CHANNEL_SPECS.temp_c,
    },
    bmp581: {
      static_pressure_pa: CHANNEL_SPECS.static_pressure_pa,
      static_temp_c: CHANNEL_SPECS.static_temp_c,
    },
    mmc5983: {
      mag_x_ut: CHANNEL_SPECS.mag_x_ut,
      mag_y_ut: CHANNEL_SPECS.mag_y_ut,
      mag_z_ut: CHANNEL_SPECS.mag_z_ut,
    },
    ms4525: {
      airspeed_dp_pa: CHANNEL_SPECS.airspeed_dp_pa,
      airspeed_temp_c: CHANNEL_SPECS.airspeed_temp_c,
    },
    // IMU device tabs use pattern rules; explicit entries for temp only
    icm20948: { temp_c: CHANNEL_SPECS.temp_c, temp: CHANNEL_SPECS.temp },
    icm45686: { temp_c: CHANNEL_SPECS.temp_c, temp: CHANNEL_SPECS.temp },
  };

  function imuPatternSpec(channel) {
    let m = channel.match(/^accel_([xyz])$/);
    if (m) {
      return {
        label: `Acceleration ${axisName(m[1])}`,
        fmt(v) { return `${fmtAccelMS2(v)} m/s²`; },
      };
    }
    m = channel.match(/^anglvel_([xyz])$/);
    if (m) {
      return {
        label: `Angular rate ${axisName(m[1])}`,
        fmt(v) { return `${fmtAnglvelDegS(v)} °/s`; },
      };
    }
    m = channel.match(/^magn_([xyz])$/);
    if (m) {
      return {
        label: `Magnetic field ${axisName(m[1])}`,
        fmt(v) { return `${fmtMicroT(v)} µT`; },
      };
    }
    return null;
  }

  function legacySuffixSpec(channel) {
    if (channel.endsWith('_dp_pa')) {
      const base = channel.replace(/_dp_pa$/, '').replace(/_/g, ' ');
      return {
        label: `${base} ΔP`,
        fmt(v) { return `${fmtPressurePa(v)} Pa`; },
      };
    }
    if (channel.endsWith('_pa') && !channel.endsWith('_dp_pa')) {
      const base = channel.replace(/_pa$/, '').replace(/_/g, ' ');
      return {
        label: base,
        fmt(v) { return `${fmtPressurePa(v)} Pa`; },
      };
    }
    if (channel.endsWith('_c')) {
      const base = channel.replace(/_c$/, '').replace(/_/g, ' ');
      return {
        label: base,
        fmt(v) { return `${fmtTempC(v)} °C`; },
      };
    }
    if (channel.endsWith('_ut')) {
      const base = channel.replace(/_ut$/, '').replace(/_/g, ' ');
      return {
        label: base,
        fmt(v) { return `${fmtMicroT(v)} µT`; },
      };
    }
    if (channel.endsWith('_deg_s')) {
      const base = channel.replace(/_deg_s$/, '').replace(/_/g, ' ');
      return {
        label: base,
        fmt(v) { return `${fmtNum(v, 2)} °/s`; },
      };
    }
    return null;
  }

  function channelSpec(device, channel) {
    if (device === 'gps' && GPS_SPECS[channel]) return GPS_SPECS[channel];
    const dev = DEVICE_SPECS[device];
    if (dev && dev[channel]) return dev[channel];
    if (CHANNEL_SPECS[channel]) return CHANNEL_SPECS[channel];
    const imu = imuPatternSpec(channel);
    if (imu) return imu;
    const legacy = legacySuffixSpec(channel);
    if (legacy) return legacy;
    return null;
  }

  /** Omit redundant ft-only row when dual m·ft is present (display only). */
  function filterDisplayKeys(device, keys) {
    if (device === 'press_alt' && keys.includes('pressure_alt_m') && keys.includes('pressure_alt_ft')) {
      keys = keys.filter((k) => k !== 'pressure_alt_ft');
    }
    if (device === 'press_alt' && keys.includes('indicated_alt_m') && keys.includes('indicated_alt_ft')) {
      keys = keys.filter((k) => k !== 'indicated_alt_ft');
    }
    if (device === 'press_alt') {
      keys = keys.filter((k) => k !== 'kollsman_inhg');
    }
    return keys;
  }

  function fmtDefault(v) {
    if (typeof v !== 'number' || !Number.isFinite(v)) return v;
    if (Number.isInteger(v) || (Math.abs(v - Math.round(v)) < 1e-9 && Math.abs(v) < 1e7)) {
      return String(Math.round(v));
    }
    const abs = Math.abs(v);
    if (abs >= 1000) return v.toFixed(1);
    if (abs >= 1) return v.toFixed(3);
    if (abs >= 0.01) return v.toFixed(4);
    return v.toExponential(2);
  }

  function channelLabel(device, channel) {
    const spec = channelSpec(device, channel);
    if (spec && spec.label) return spec.label;
    return channel;
  }

  function formatValue(device, channel, value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return { text: value };
    const spec = channelSpec(device, channel);
    if (spec && spec.fmt) {
      const out = spec.fmt(value);
      if (out && typeof out === 'object' && (out.html || out.text)) return out;
      return { text: String(out) };
    }
    if (channel.match(/^anglvel_[xyz]$/)) return { text: `${fmtAnglvelDegS(value)} °/s` };
    if (channel.endsWith('_pa') || channel.endsWith('_dp_pa')) return { text: `${fmtPressurePa(value)} Pa` };
    if (channel.endsWith('_c') || channel === 'temp') return { text: `${fmtTempC(value)} °C` };
    if (channel.endsWith('_ut') || channel.startsWith('magn_')) return { text: `${fmtMicroT(value)} µT` };
    if (channel.endsWith('_deg_s')) return { text: `${fmtNum(value, 2)} °/s` };
    return { text: String(fmtDefault(value)) };
  }

  function sortWithOrder(keys, orderList) {
    const order = new Map(orderList.map((k, i) => [k, i]));
    return [...keys].sort((a, b) => {
      const ia = order.has(a) ? order.get(a) : 999;
      const ib = order.has(b) ? order.get(b) : 999;
      if (ia !== ib) return ia - ib;
      return a.localeCompare(b);
    });
  }

  function isIMUDevice(device) {
    const d = device.toLowerCase();
    return d.includes('icm') || d.includes('mpu') || d.includes('bmi');
  }

  function hasIMUChannels(keys) {
    return keys.some((k) => /^(accel_|anglvel_|magn_)/.test(k));
  }

  function sortKeys(device, keys) {
    keys = filterDisplayKeys(device, keys);
    if (device === 'gps') return sortWithOrder(keys, GPS_SORT);
    if (device === 'geo') return sortWithOrder(keys, GEO_SORT);
    if (device === 'ahrs') return sortWithOrder(keys, AHRS_SORT);
    if (device === 'press_alt') return sortWithOrder(keys, PRESS_ALT_SORT);
    const podOrder = POD_DEVICE_SORT[device];
    if (podOrder) return sortWithOrder(keys, podOrder);
    if (isIMUDevice(device) || hasIMUChannels(keys)) {
      return sortWithOrder(keys, IMU_SORT);
    }
    return [...keys].sort();
  }

  function rowClass(device, channel) {
    if (device === 'gps' && GPS_ACCURACY.has(channel)) return ' kv-accuracy';
    return '';
  }

  function gpsFootnote(device) {
    if (device !== 'gps') return '';
    return '<div class="gpsFootnote dim">Error estimates from gpsd; ~95% confidence, receiver-dependent.</div>';
  }

  return {
    channelLabel,
    formatValue,
    sortKeys,
    rowClass,
    gpsFootnote,
    fmtDefault,
  };
})();
