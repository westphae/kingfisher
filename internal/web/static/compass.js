// Compass tab: SVG rose + model vs measured field table.
// Static DOM is built once; WebSocket ticks update values only.
const KFCompass = (function () {
  const COMPARE_ROWS = [
    { key: 'field_x_nt', label: 'Field X (north)' },
    { key: 'field_y_nt', label: 'Field Y (east)' },
    { key: 'field_z_nt', label: 'Field Z (down)' },
    { key: 'field_h_nt', label: 'Horizontal H' },
    { key: 'field_f_nt', label: 'Total F' },
    { key: 'inclination', label: 'Inclination' },
    { key: 'declination', label: 'Declination', modelOnly: true },
  ];

  const DIAG_ROWS = [
    { key: 'filter_mode', label: 'Filter mode' },
    { key: 'nis', label: 'NIS' },
    { key: 'converged', label: 'Converged' },
    { key: 'align_active', label: 'Aligned' },
    { key: 'heading_mag_deg', label: 'Heading (magnetic)' },
    { key: 'heading_true_deg', label: 'Heading (true)' },
    { key: 'heading_sensor_deg', label: 'Heading (sensor)' },
  ];

  function smoothedVals(device, sample) {
    return KFSmooth.values(device, sample?.values ?? {});
  }

  function heading360(deg) {
    const n = Number(deg);
    if (!Number.isFinite(n)) return null;
    return ((n % 360) + 360) % 360;
  }

  function effectiveHMeasNt(hMeasNt, hModelNt) {
    const h = Number(hMeasNt);
    if (Number.isFinite(h) && h > 0) return h;
    const m = Number(hModelNt);
    return Number.isFinite(m) && m > 0 ? m : 50000;
  }

  function needleLength(hMeasNt, hModelNt) {
    const hRef = effectiveHMeasNt(hMeasNt, hModelNt);
    const base = 72;
    const minLen = 28;
    const maxLen = 88;
    const ref = Number.isFinite(Number(hModelNt)) && Number(hModelNt) > 0 ? Number(hModelNt) : 50000;
    const ratio = hRef / ref;
    return Math.max(minLen, Math.min(maxLen, base * ratio));
  }

  function mount(kvEl) {
    const existing = kvEl.querySelector('[data-compass-root]');
    if (existing && existing.querySelector('[data-cmp-footnote]')) return;
    if (existing) existing.remove();

    let compareBody = '';
    for (const row of COMPARE_ROWS) {
      compareBody +=
        `<tr data-cmp="${row.key}">` +
        `<th scope="row">${row.label}</th>` +
        `<td class="col-model" data-cmp-model>—</td>` +
        `<td class="col-meas" data-cmp-meas>—</td></tr>`;
    }

    let diagHtml = '';
    for (const row of DIAG_ROWS) {
      diagHtml +=
        `<div class="kv" data-diag="${row.key}">` +
        `<div class="k">${row.label}</div>` +
        `<div class="v" data-diag-val>—</div></div>`;
    }

    kvEl.innerHTML =
      `<div data-compass-root>` +
      `<section class="compass-instrument">` +
      `<svg class="compass-svg" viewBox="0 0 200 200" role="img" aria-label="Compass">` +
      `<circle class="compass-dial" cx="100" cy="100" r="92"/>` +
      tickMarksStatic() +
      `<text class="compass-cardinal" x="100" y="18" text-anchor="middle">N</text>` +
      `<text class="compass-cardinal" x="182" y="105" text-anchor="middle">E</text>` +
      `<text class="compass-cardinal" x="100" y="194" text-anchor="middle">S</text>` +
      `<text class="compass-cardinal" x="18" y="105" text-anchor="middle">W</text>` +
      `<g data-compass-needle>` +
      `<line class="compass-needle-tail" x1="100" y1="100" x2="100" y2="128"/>` +
      `<line class="compass-needle" x1="100" y1="100" x2="100" y2="28"/>` +
      `</g>` +
      `<circle class="compass-pivot" cx="100" cy="100" r="4"/>` +
      `</svg>` +
      `<div class="compass-digital">` +
      `<div class="compass-heading-primary" data-hdg-primary>—</div>` +
      `<div class="compass-heading-sub" data-hdg-sub></div>` +
      `</div></section>` +
      `<table class="compass-compare">` +
      `<thead><tr><th></th><th>Model (geo)</th><th>Measured</th></tr></thead>` +
      `<tbody>${compareBody}</tbody></table>` +
      `<p class="compass-footnote dim" data-cmp-footnote></p>` +
      `<div class="compass-diag">${diagHtml}</div>` +
      `</div>`;
  }

  function updateFootnote(root, alignMethod) {
    const el = root.querySelector('[data-cmp-footnote]');
    if (!el) return;
    if (alignMethod === 'accel') {
      el.textContent = 'Measured: sensor frame + gravity (accel align).';
    } else {
      el.textContent = 'Measured: true rotated field (sensor -> aircraft FRD -> earth NED).';
    }
  }

  function tickMarksStatic() {
    let s = '';
    const cx = 100;
    const cy = 100;
    const r = 92;
    for (let d = 0; d < 360; d += 30) {
      const a = (d * Math.PI) / 180;
      const x1 = cx + (r - 6) * Math.sin(a);
      const y1 = cy - (r - 6) * Math.cos(a);
      const x2 = cx + r * Math.sin(a);
      const y2 = cy - r * Math.cos(a);
      s += `<line class="compass-tick" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
    }
    return s;
  }

  // Presentation-only dead-band: only redraw the needle when the displayed
  // heading would shift by more than NEEDLE_DEADBAND_DEG. The DB still
  // gets every unfiltered sample; this just calms the needle in turbulence
  // and saves a per-WS-tick SVG attribute write.
  const NEEDLE_DEADBAND_DEG = 0.5;

  function updateNeedle(root, headingDeg, hMeasNt, hModelNt, vehicleOK) {
    const g = root.querySelector('[data-compass-needle]');
    if (!g) return;
    const hdg = Number(headingDeg);
    const rot = Number.isFinite(hdg) ? hdg : 0;
    const prev = Number(g.dataset.rot);
    if (Number.isFinite(prev)) {
      let delta = Math.abs(rot - prev);
      if (delta > 180) delta = 360 - delta;
      if (delta <= NEEDLE_DEADBAND_DEG) {
        // Skip the SVG write but still update length/dim below.
      } else {
        g.setAttribute('transform', `rotate(${rot} 100 100)`);
        g.dataset.rot = String(rot);
      }
    } else {
      g.setAttribute('transform', `rotate(${rot} 100 100)`);
      g.dataset.rot = String(rot);
    }
    const len = needleLength(hMeasNt, hModelNt);
    const tailLen = len * 0.35;
    const needle = g.querySelector('.compass-needle');
    const tail = g.querySelector('.compass-needle-tail');
    if (needle) {
      needle.setAttribute('x2', String(100));
      needle.setAttribute('y2', String(100 - len));
    }
    if (tail) {
      tail.setAttribute('x2', String(100));
      tail.setAttribute('y2', String(100 + tailLen));
    }
    g.classList.toggle('compass-dim', !vehicleOK);
  }

  function updateDigital(root, compassVals) {
    const mag = heading360(compassVals.heading_mag_deg);
    const tru = heading360(compassVals.heading_true_deg);
    const sensor = heading360(compassVals.heading_sensor_deg);
    const align = compassVals.align_active === 1;
    const primary = root.querySelector('[data-hdg-primary]');
    const sub = root.querySelector('[data-hdg-sub]');
    if (!primary) return;
    if (align && mag != null) {
      primary.textContent = `${mag.toFixed(1)}°M`;
      if (sub) sub.textContent = tru != null ? `${tru.toFixed(1)}°T` : '';
    } else if (sensor != null) {
      primary.textContent = `${sensor.toFixed(1)}°`;
      if (sub) sub.textContent = 'sensor (not aligned)';
    } else {
      primary.textContent = '—';
      if (sub) sub.textContent = '';
    }
  }

  function updateCompare(root, geoVals, compassVals) {
    for (const row of COMPARE_ROWS) {
      const tr = root.querySelector(`tr[data-cmp="${row.key}"]`);
      if (!tr) continue;
      const modelCell = tr.querySelector('[data-cmp-model]');
      const measCell = tr.querySelector('[data-cmp-meas]');
      if (modelCell) modelCell.innerHTML = KFDisplay.fmtCell('geo', row.key, geoVals[row.key]);
      if (measCell) {
        measCell.innerHTML = row.modelOnly
          ? '—'
          : KFDisplay.fmtCell('compass', row.key, compassVals[row.key]);
      }
    }
  }

  function updateDiag(root, compassVals) {
    for (const row of DIAG_ROWS) {
      const el = root.querySelector(`[data-diag="${row.key}"] [data-diag-val]`);
      if (!el) continue;
      const v = compassVals[row.key];
      if (v == null || !Number.isFinite(Number(v))) {
        el.textContent = '—';
      } else {
        const out = KFDisplay.formatValue('compass', row.key, Number(v));
        el.innerHTML = out.html ?? String(out.text ?? '');
      }
    }
  }

  function renderPanel(kvEl, geoSample, compassSample, alignMethod) {
    mount(kvEl);
    const root = kvEl.querySelector('[data-compass-root]');
    if (!root) return;
    updateFootnote(root, alignMethod || 'wmm');
    const geoVals = smoothedVals('geo', geoSample);
    const compassVals = smoothedVals('compass', compassSample);
    const hdgRaw = compassVals.align_active === 1
      ? compassVals.heading_mag_deg
      : compassVals.heading_sensor_deg;
    const hdg = heading360(hdgRaw) ?? 0;
    updateNeedle(root, hdg, compassVals.field_h_nt, geoVals.field_h_nt, compassVals.align_active === 1);
    updateDigital(root, compassVals);
    updateCompare(root, geoVals, compassVals);
    updateDiag(root, compassVals);
  }

  return { renderPanel };
})();
