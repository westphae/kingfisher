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
    'fix_time_unix_s',
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

  const COMPASS_SORT = [
    'heading_mag_deg', 'heading_true_deg', 'heading_sensor_deg',
    'field_x_nt', 'field_y_nt', 'field_z_nt', 'field_h_nt', 'field_f_nt',
    'declination', 'inclination',
    'filter_mode', 'nis', 'converged', 'align_active',
  ];

  const PRESS_ALT_SORT = [
    'pressure_alt_m', 'pressure_alt_ft',
    'indicated_alt_m', 'indicated_alt_ft',
    'density_alt_ft', 'vs_ms',
    'pressure_pa', 'pressure_source',
  ];

  const POD_DEVICE_SORT = {
    bmp581: ['static_pressure_pa', 'static_temp_c'],
    bmp280: ['pressure_pa', 'temp_c'],
    mmc5983: ['mag_x_ut', 'mag_y_ut', 'mag_z_ut'],
    ms4525: ['airspeed_dp_pa', 'airspeed_temp_c'],
    bq27441: [
      'battery_voltage_v',
      'battery_soc_pct',
      'battery_current_a',
      'battery_power_w',
      'battery_capacity_remain_mah',
      'battery_capacity_full_mah',
      'battery_time_remain_s',
    ],
  };

  const BQ27441_HIDDEN = new Set(['battery_gauge_learned']);

  const NO_SMOOTH_CHANNELS = new Set([
    'fix_time_unix_s',
    'fix',
    'sats',
    'align_active',
    'converged',
    'filter_mode',
    'battery_gauge_learned',
    'pressure_source',
  ]);

  const SMOOTH_GROUP_LABELS = {
    default: 'All channels',
    accel: 'Accel',
    gyro: 'Gyro',
    mag: 'Mag',
    pos: 'Position',
    acc: 'Accuracy',
    fix: 'Fix',
    vel: 'Velocity',
    field: 'Field',
    heading: 'Heading',
  };

  /** Smoothing group templates for devices that have not published values
   *  yet (so listSmoothGroups can find no channels). Keyed by device id. */
  const STATIC_SMOOTH_GROUPS = {
    gps: [
      { id: 'pos', channels: ['lat', 'lon', 'alt_msl'] },
      { id: 'acc', channels: ['h_acc', 'v_acc', 'gs_acc'] },
      { id: 'fix', channels: ['fix_time_unix_s', 'fix', 'sats'] },
      { id: 'vel', channels: ['gs', 'track', 'vs'] },
    ],
    ahrs: [{ id: 'default', channels: ['roll', 'pitch', 'yaw'] }],
    compass: [{ id: 'default', channels: ['heading_mag_deg', 'align_active'] }],
    airspeed: [{ id: 'default', channels: ['ias_kt', 'tas_kt'] }],
    press_alt: [{ id: 'default', channels: ['indicated_alt_ft', 'pressure_alt_ft', 'density_alt_ft'] }],
    geo: [{ id: 'default', channels: ['field_f_nt', 'declination', 'inclination'] }],
    bmp581: [{ id: 'default', channels: ['static_pressure_pa', 'static_temp_c'] }],
    ms4525: [{ id: 'default', channels: ['airspeed_dp_pa', 'airspeed_temp_c'] }],
    bq27441: [{ id: 'default', channels: ['battery_voltage_v', 'battery_soc_pct', 'battery_time_remain_s'] }],
    mmc5983: [{ id: 'default', channels: ['mag_x_ut', 'mag_y_ut', 'mag_z_ut'] }],
  };

  /** Overview block title overrides (device id unchanged in routes/WS). */
  const OVERVIEW_DEVICE_NAMES = {
    press_alt: 'altitude',
    geo: 'geomagnetic',
  };

  function overviewDeviceName(device) {
    return OVERVIEW_DEVICE_NAMES[device] || device;
  }

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

  /** Overview cells: plain fixed decimal, no thousands separators. */
  function fmtOverviewNum(v, decimals) {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    return n.toFixed(decimals);
  }

  function fmtOverviewTimeRemain(sec) {
    if (!Number.isFinite(sec) || sec < 0) return '—';
    if (sec >= 3600) return fmtOverviewNum(sec / 3600, 1) + ' h';
    if (sec >= 60) return Math.round(sec / 60) + ' m';
    return Math.round(sec) + ' s';
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

  function fmtGpsTimeUTC(unixS) {
    const d = new Date(unixS * 1000);
    if (!Number.isFinite(unixS) || Number.isNaN(d.getTime())) return '—';
    const y = d.getUTCFullYear();
    const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
    const day = String(d.getUTCDate()).padStart(2, '0');
    const h = String(d.getUTCHours()).padStart(2, '0');
    const m = String(d.getUTCMinutes()).padStart(2, '0');
    const s = String(d.getUTCSeconds()).padStart(2, '0');
    return `${y}-${mo}-${day} ${h}:${m}:${s} UTC`;
  }

  const GPS_SPECS = {
    fix_time_unix_s: {
      label: 'GPS time',
      fmt(v) { return fmtGpsTimeUTC(v); },
    },
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
    vs_ms: {
      label: 'Vertical speed',
      // Stored in m/s; displayed in ft/min (aviation VSI convention).
      fmt(v) { return `${fmtNum(v * MPS_TO_FPM, 0)} ft/min`; },
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
    ias_kt: {
      label: 'Indicated airspeed',
      fmt(v) { return `${fmtNum(v, 1)} kt`; },
    },
    tas_kt: {
      label: 'True airspeed',
      fmt(v) { return `${fmtNum(v, 1)} kt`; },
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
    compass: {
      heading_mag_deg: {
        label: 'Heading (magnetic)',
        fmt(v) { return `${fmtNum(v, 1)}°`; },
      },
      heading_true_deg: {
        label: 'Heading (true)',
        fmt(v) { return `${fmtNum(v, 1)}°`; },
      },
      heading_sensor_deg: {
        label: 'Heading (sensor)',
        fmt(v) { return `${fmtNum(v, 1)}°`; },
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
      inclination: {
        label: 'Inclination (measured)',
        fmt(v) { return `${fmtNum(v, 2)}°`; },
      },
      filter_mode: {
        label: 'Filter mode',
        fmt(v) { return Number(v) === 1 ? 'Locked' : 'Calibrating'; },
      },
      nis: {
        label: 'NIS',
        fmt(v) { return fmtNum(v, 3); },
      },
      converged: {
        label: 'Converged',
        fmt(v) { return Number(v) >= 1 ? 'yes' : 'no'; },
      },
      align_active: {
        label: 'Aligned',
        fmt(v) { return Number(v) >= 1 ? 'yes' : 'no'; },
      },
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
    bq27441: {
      battery_voltage_v: {
        label: 'Voltage',
        fmt(v) { return `${fmtNum(v, 2)} V`; },
      },
      battery_soc_pct: {
        label: 'State of charge',
        fmt(v) { return `${fmtNum(v, 0)}%`; },
      },
      battery_current_a: {
        label: 'Current',
        fmt(v) {
          const ma = Number(v) * 1000;
          if (!Number.isFinite(ma)) return String(v);
          const sign = ma >= 0 ? '+' : '−';
          return `${sign}${fmtNum(Math.abs(ma), 0)} mA`;
        },
      },
      battery_power_w: {
        label: 'Power',
        fmt(v) { return `${fmtNum(v, 2)} W`; },
      },
      battery_capacity_remain_mah: {
        label: 'Remaining capacity',
        fmt(v) { return `${fmtNum(v, 0)} mAh`; },
      },
      battery_capacity_full_mah: {
        label: 'Full capacity',
        fmt(v) { return `${fmtNum(v, 0)} mAh`; },
      },
      battery_time_remain_s: {
        label: 'Time remaining',
        fmt(v) {
          const sec = Number(v);
          if (!Number.isFinite(sec) || sec < 0) return '—';
          if (sec >= 3600) return `${fmtNum(sec / 3600, 1)} h`;
          if (sec >= 60) return `${fmtNum(sec / 60, 0)} min`;
          return `${fmtNum(sec, 0)} s`;
        },
      },
    },
    airspeed: {
      ias_kt: CHANNEL_SPECS.ias_kt,
      tas_kt: CHANNEL_SPECS.tas_kt,
      airspeed_dp_pa: CHANNEL_SPECS.airspeed_dp_pa,
      airspeed_dp_cal_pa: CHANNEL_SPECS.airspeed_dp_pa,
      airspeed_temp_c: CHANNEL_SPECS.airspeed_temp_c,
      static_pressure_pa: CHANNEL_SPECS.static_pressure_pa,
      static_temp_c: CHANNEL_SPECS.static_temp_c,
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
    if (device === 'bq27441') {
      keys = keys.filter((k) => !BQ27441_HIDDEN.has(k));
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

  function bq27441UnlearnedField(channel, values) {
    if (!values || values.battery_gauge_learned !== 0) return false;
    return channel === 'battery_soc_pct'
      || channel === 'battery_capacity_remain_mah'
      || channel === 'battery_capacity_full_mah'
      || channel === 'battery_time_remain_s';
  }

  function formatValue(device, channel, value, allValues) {
    if (device === 'bq27441' && bq27441UnlearnedField(channel, allValues)) {
      return { text: '—' };
    }
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
    if (device === 'compass') return sortWithOrder(keys, COMPASS_SORT);
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

  function bq27441Footnote(values) {
    if (!values || values.battery_gauge_learned !== 0) return '';
    return '<div class="gaugeFootnote dim">Fuel gauge not learned — state of charge and capacity are unavailable until the BQ27441 completes a charge/discharge learning cycle. Design capacity is set under Settings.</div>';
  }

  function cell(_keys, _values, key, header) {
    return { key, header };
  }

  function row(rowLabel, cells) {
    const c = cells.filter(Boolean);
    if (c.length === 0) return null;
    return { rowLabel, cells: c };
  }

  function overviewLayout(device, values) {
    values = values || {};
    const keys = Object.keys(values);
    const subRows = [];

    if (device === 'gps') {
      subRows.push(
        row('pos', [
          cell(keys, values, 'lat', 'Lat'),
          cell(keys, values, 'lon', 'Lon'),
          cell(keys, values, 'alt_msl', 'Alt'),
        ]),
        row('acc', [
          cell(keys, values, 'h_acc', 'H'),
          cell(keys, values, 'v_acc', 'V'),
          cell(keys, values, 'gs_acc', 'GS'),
        ]),
        row('fix', [
          cell(keys, values, 'fix_time_unix_s', 'Time'),
          cell(keys, values, 'fix', 'Fix'),
          cell(keys, values, 'sats', 'Sats'),
        ]),
        row('vel', [
          cell(keys, values, 'gs', 'GS'),
          cell(keys, values, 'track', 'Trk'),
          cell(keys, values, 'vs', 'VS'),
        ]),
      );
    } else if (device === 'ahrs') {
      subRows.push(row('', [
        cell(keys, values, 'roll', 'Roll'),
        cell(keys, values, 'pitch', 'Pitch'),
        cell(keys, values, 'yaw', 'Yaw'),
      ]));
    } else if (device === 'compass') {
      subRows.push(row('', [
        cell(keys, values, 'heading_mag_deg', 'Hdg'),
        cell(keys, values, 'align_active', 'Align'),
      ]));
    } else if (device === 'airspeed') {
      subRows.push(row('', [
        cell(keys, values, 'ias_kt', 'IAS'),
        cell(keys, values, 'tas_kt', 'TAS'),
      ]));
    } else if (device === 'press_alt') {
      subRows.push(row('', [
        cell(keys, values, 'indicated_alt_ft', 'IALT'),
        cell(keys, values, 'pressure_alt_ft', 'PALT'),
        cell(keys, values, 'density_alt_ft', 'DALT'),
      ]));
    } else if (device === 'geo') {
      subRows.push(row('', [
        cell(keys, values, 'field_f_nt', 'F'),
        cell(keys, values, 'declination', 'Dec'),
        cell(keys, values, 'inclination', 'Incl'),
      ]));
    } else if (device === 'mmc5983') {
      subRows.push(row('', [
        cell(keys, values, 'mag_x_ut', 'X'),
        cell(keys, values, 'mag_y_ut', 'Y'),
        cell(keys, values, 'mag_z_ut', 'Z'),
      ]));
    } else if (device === 'bmp581') {
      subRows.push(row('', [
        cell(keys, values, 'static_pressure_pa', 'P'),
        cell(keys, values, 'static_temp_c', 'T'),
      ]));
    } else if (device === 'ms4525') {
      subRows.push(row('', [
        cell(keys, values, 'airspeed_dp_pa', 'ΔP'),
        cell(keys, values, 'airspeed_temp_c', 'T'),
      ]));
    } else if (device === 'bq27441') {
      subRows.push(row('', [
        cell(keys, values, 'battery_voltage_v', 'V'),
        cell(keys, values, 'battery_soc_pct', 'SOC'),
        cell(keys, values, 'battery_time_remain_s', 'Rem'),
      ]));
    } else if (device.endsWith('-accel')) {
      subRows.push(row('accel', [
        cell(keys, values, 'accel_x', 'Ax'),
        cell(keys, values, 'accel_y', 'Ay'),
        cell(keys, values, 'accel_z', 'Az'),
      ]));
    } else if (device.endsWith('-gyro')) {
      subRows.push(row('gyro', [
        cell(keys, values, 'anglvel_x', 'Gx'),
        cell(keys, values, 'anglvel_y', 'Gy'),
        cell(keys, values, 'anglvel_z', 'Gz'),
      ]));
    } else if (isIMUDevice(device) || hasIMUChannels(keys)) {
      subRows.push(row('accel', [
        cell(keys, values, 'accel_x', 'Ax'),
        cell(keys, values, 'accel_y', 'Ay'),
        cell(keys, values, 'accel_z', 'Az'),
      ]));
      subRows.push(row('gyro', [
        cell(keys, values, 'anglvel_x', 'Gx'),
        cell(keys, values, 'anglvel_y', 'Gy'),
        cell(keys, values, 'anglvel_z', 'Gz'),
      ]));
      if (keys.some((k) => /^magn_/.test(k))) {
        subRows.push(row('mag', [
          cell(keys, values, 'magn_x', 'Mx'),
          cell(keys, values, 'magn_y', 'My'),
          cell(keys, values, 'magn_z', 'Mz'),
        ]));
      }
    } else {
      const ordered = sortKeys(device, keys).slice(0, 3);
      if (ordered.length) {
        subRows.push(row('', ordered.map((k) => ({ key: k, header: k.replace(/_/g, ' ') }))));
      }
    }

    return { subRows: subRows.filter(Boolean) };
  }

  function noSmoothChannel(channel) {
    return NO_SMOOTH_CHANNELS.has(channel);
  }

  function smoothGroupLabel(device, groupId, channels) {
    if (SMOOTH_GROUP_LABELS[groupId]) {
      const base = SMOOTH_GROUP_LABELS[groupId];
      if (groupId === 'accel' || groupId === 'gyro' || groupId === 'mag') {
        const axes = (channels || []).map((ch) => {
          const m = ch.match(/(?:accel_|anglvel_|magn_|mag_)(.)/);
          if (m) return m[1].toUpperCase();
          const parts = ch.split('_');
          return parts[parts.length - 1].slice(0, 1).toUpperCase();
        });
        if (axes.length) return `${base} (${axes.join(' ')})`;
      }
      return base;
    }
    if (groupId === 'default' && device === 'ahrs') return 'Attitude (roll pitch yaw)';
    if (groupId === 'default' && device === 'compass') return 'Heading';
    if (groupId === 'default' && device === 'airspeed') return 'IAS / TAS';
    if (groupId === 'default' && device === 'press_alt') return 'Altitude';
    if (groupId === 'default' && device === 'geo') return 'Geomagnetic';
    if (channels && channels.length === 1) {
      return channelLabel(device, channels[0]);
    }
    return groupId.replace(/_/g, ' ');
  }

  function staticSmoothGroups(device) {
    if (device.endsWith('-accel')) {
      return [{ id: 'accel', label: smoothGroupLabel(device, 'accel', IMU_ACCEL), channels: [...IMU_ACCEL] }];
    }
    if (device.endsWith('-gyro')) {
      return [{ id: 'gyro', label: smoothGroupLabel(device, 'gyro', IMU_GYRO), channels: [...IMU_GYRO] }];
    }
    const tpl = STATIC_SMOOTH_GROUPS[device];
    if (!tpl) return [];
    return tpl.map((g) => ({
      id: g.id,
      label: smoothGroupLabel(device, g.id, g.channels),
      channels: g.channels.filter((ch) => !noSmoothChannel(ch)),
    })).filter((g) => g.channels.length > 0);
  }

  function listSmoothGroups(device, values) {
    values = values || {};
    const keys = Object.keys(values);
    const layout = overviewLayout(device, values);
    const groups = [];
    const assigned = new Set();

    for (const sr of layout.subRows) {
      const groupId = sr.rowLabel || 'default';
      const channels = sr.cells
        .map((c) => c.key)
        .filter((ch) => !noSmoothChannel(ch));
      if (channels.length === 0) continue;
      groups.push({
        id: groupId,
        label: smoothGroupLabel(device, groupId, channels),
        channels,
      });
      for (const ch of channels) assigned.add(ch);
    }

    const prefixGroups = [
      ['field_', 'field'],
      ['heading_', 'heading'],
    ];
    const extra = {};
    for (const k of keys) {
      if (assigned.has(k) || noSmoothChannel(k)) continue;
      if (typeof values[k] !== 'number' || !Number.isFinite(values[k])) continue;
      let gid = k;
      for (const [prefix, g] of prefixGroups) {
        if (k.startsWith(prefix)) {
          gid = g;
          break;
        }
      }
      if (device === 'press_alt' && k === 'vs_ms') gid = 'default';
      const existing = groups.find((g) => g.id === gid);
      if (existing) {
        if (!existing.channels.includes(k)) existing.channels.push(k);
      } else {
        if (!extra[gid]) extra[gid] = [];
        extra[gid].push(k);
      }
      assigned.add(k);
    }
    for (const [gid, chs] of Object.entries(extra)) {
      const sorted = sortKeys(device, chs);
      groups.push({
        id: gid,
        label: smoothGroupLabel(device, gid, sorted),
        channels: sorted,
      });
    }
    if (groups.length === 0) {
      return staticSmoothGroups(device);
    }
    return groups;
  }

  function formatOverviewCell(device, channel, value, allValues) {
    if (value == null || (typeof value === 'number' && !Number.isFinite(value))) {
      if (device === 'bq27441' && bq27441UnlearnedField(channel, allValues)) return '—';
      return '—';
    }
    if (device === 'compass' && channel === 'align_active') {
      return Number(value) ? '✓' : '—';
    }
    if (channel === 'fix_time_unix_s') {
      const d = new Date(value * 1000);
      if (!Number.isFinite(value) || Number.isNaN(d.getTime())) return '—';
      const h = String(d.getUTCHours()).padStart(2, '0');
      const m = String(d.getUTCMinutes()).padStart(2, '0');
      const s = String(d.getUTCSeconds()).padStart(2, '0');
      return `${h}:${m}:${s}`;
    }
    if (channel === 'lat' || channel === 'lon') return fmtOverviewNum(value, 2) + '°';
    if (channel === 'alt_msl') return fmtOverviewNum(value * M_TO_FT, 0) + ' ft';
    if (channel === 'h_acc' || channel === 'v_acc') {
      const ft = value * M_TO_FT;
      return '±' + fmtOverviewNum(ft, ft < 10 ? 1 : 0) + ' ft';
    }
    if (channel === 'gs_acc') {
      const kt = value * MPS_TO_KT;
      return '±' + fmtOverviewNum(kt, 1) + ' kt';
    }
    if (channel === 'gs') return fmtOverviewNum(value * MPS_TO_KT, 0) + ' kt';
    if (channel === 'track') return fmtOverviewNum(value, 0) + '°';
    if (channel === 'vs') {
      const fpm = value * MPS_TO_FPM;
      const sign = fpm >= 0 ? '+' : '';
      return sign + fmtOverviewNum(fpm, 0) + ' fpm';
    }
    if (channel === 'fix') return String(Math.round(value));
    if (channel === 'sats') return String(Math.round(value));
    if (channel === 'roll' || channel === 'pitch' || channel === 'yaw') {
      return fmtOverviewNum(value, 0) + '°';
    }
    if (channel === 'heading_mag_deg') return fmtOverviewNum(value, 0) + '°';
    if (channel === 'ias_kt' || channel === 'tas_kt') return fmtOverviewNum(value, 0) + ' kt';
    if (channel.endsWith('_ft')) return fmtOverviewNum(value, 0) + ' ft';
    if (channel === 'field_f_nt') {
      const n = Number(value);
      if (n >= 1000) return fmtOverviewNum(n / 1000, 1) + 'k nT';
      return fmtOverviewNum(n, 0) + ' nT';
    }
    if (channel === 'declination' || channel === 'inclination') {
      return fmtOverviewNum(value, 1) + '°';
    }
    if (channel.endsWith('_ut') || channel.startsWith('magn_')) {
      return fmtOverviewNum(value, 2);
    }
    if (channel.match(/^anglvel_[xyz]$/)) {
      return fmtOverviewNum(value * RAD_TO_DEG, 2) + '°/s';
    }
    if (channel.match(/^accel_[xyz]$/)) {
      return fmtOverviewNum(value, Math.abs(value) >= 10 ? 2 : Math.abs(value) >= 1 ? 3 : 4);
    }
    if (channel === 'static_pressure_pa') {
      return fmtOverviewNum(value / 100, 3) + ' hPa';
    }
    if (channel.endsWith('_pa')) {
      return fmtOverviewNum(value, Math.abs(value) >= 1000 ? 0 : 1) + ' Pa';
    }
    if (channel.endsWith('_c') || channel === 'temp') {
      return fmtOverviewNum(value, 2) + '°';
    }
    if (channel === 'battery_voltage_v') return fmtOverviewNum(value, 2) + ' V';
    if (channel === 'battery_soc_pct') return fmtOverviewNum(value, 0) + '%';
    if (channel === 'battery_time_remain_s') return fmtOverviewTimeRemain(value);
    if (channel.endsWith('_dp_pa')) return fmtOverviewNum(value, 0) + ' Pa';
    if (typeof value === 'number' && Number.isFinite(value)) {
      const abs = Math.abs(value);
      if (abs >= 1000) return fmtOverviewNum(value, 1);
      if (abs >= 1) return fmtOverviewNum(value, 3);
      if (abs >= 0.01) return fmtOverviewNum(value, 4);
      return value.toExponential(2);
    }
    return String(value);
  }

  // fmtCell renders a single value to display HTML, or an em dash when absent.
  function fmtCell(device, channel, value) {
    if (value == null || !Number.isFinite(Number(value))) {
      return '—';
    }
    const out = formatValue(device, channel, Number(value));
    return out.html ?? String(out.text ?? '');
  }

  return {
    channelLabel,
    formatValue,
    fmtCell,
    sortKeys,
    rowClass,
    gpsFootnote,
    bq27441Footnote,
    fmtDefault,
    overviewLayout,
    formatOverviewCell,
    overviewDeviceName,
    noSmoothChannel,
    listSmoothGroups,
    smoothGroupLabel,
  };
})();
