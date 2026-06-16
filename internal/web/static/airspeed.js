// Airspeed (calc) tab: hero IAS/TAS + raw inputs table.
// Static DOM is built once; WebSocket ticks update values only.
const KFAirspeed = (function () {
  const INPUT_ROWS = [
    { key: 'airspeed_dp_pa', label: 'Differential pressure (raw)', device: 'ms4525' },
    { key: 'airspeed_dp_cal_pa', label: 'Differential pressure (cal)', device: 'airspeed' },
    { key: 'static_pressure_pa', label: 'Static pressure', device: 'bmp581' },
    { key: 'static_temp_c', label: 'Static temperature', device: 'bmp581' },
    { key: 'airspeed_temp_c', label: 'Pitot sensor temperature', device: 'ms4525' },
    { key: 'pressure_alt_m', label: 'Pressure altitude', device: 'press_alt' },
  ];

  function fmtCell(device, channel, value) {
    if (value == null || !Number.isFinite(Number(value))) {
      return '—';
    }
    const out = KFDisplay.formatValue(device, channel, Number(value));
    return out.html ?? String(out.text ?? '');
  }

  function smoothChannel(device, sample, channel) {
    const v = KFSmooth.values(device, sample?.values ?? {})[channel];
    return v != null && Number.isFinite(Number(v)) ? Number(v) : null;
  }

  function fmtSpeedKt(value) {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    return `${Number(value).toFixed(1)} kt`;
  }

  function mount(kvEl) {
    const existing = kvEl.querySelector('[data-airspeed-root]');
    if (existing) return;

    let inputBody = '';
    for (const row of INPUT_ROWS) {
      inputBody +=
        `<tr data-asp="${row.key}">` +
        `<th scope="row">${row.label}</th>` +
        `<td class="col-val" data-asp-val>—</td></tr>`;
    }

    kvEl.innerHTML =
      `<div data-airspeed-root>` +
      `<section class="airspeed-hero">` +
      `<div class="airspeed-speed-block">` +
      `<div class="airspeed-speed-label">IAS</div>` +
      `<div class="airspeed-speed-value" data-asp-ias>—</div>` +
      `</div>` +
      `<div class="airspeed-speed-block">` +
      `<div class="airspeed-speed-label">TAS</div>` +
      `<div class="airspeed-speed-value" data-asp-tas>—</div>` +
      `</div>` +
      `</section>` +
      `<table class="airspeed-inputs">` +
      `<thead><tr><th>Input</th><th>Value</th></tr></thead>` +
      `<tbody>${inputBody}</tbody></table>` +
      `</div>`;
  }

  function renderPanel(kvEl, ms4525Sample, bmp581Sample, airspeedSample, pressAltSample) {
    mount(kvEl);
    const root = kvEl.querySelector('[data-airspeed-root]');
    if (!root) return;

    const asp = KFSmooth.values('airspeed', airspeedSample?.values ?? {});
    const iasEl = root.querySelector('[data-asp-ias]');
    const tasEl = root.querySelector('[data-asp-tas]');
    if (iasEl) iasEl.textContent = fmtSpeedKt(asp.ias_kt);
    if (tasEl) tasEl.textContent = fmtSpeedKt(asp.tas_kt);

    const sources = {
      ms4525: ms4525Sample,
      bmp581: bmp581Sample,
      press_alt: pressAltSample,
    };

    for (const row of INPUT_ROWS) {
      const tr = root.querySelector(`tr[data-asp="${row.key}"]`);
      if (!tr) continue;
      const cell = tr.querySelector('[data-asp-val]');
      if (!cell) continue;
      let val = null;
      let fmtDevice = row.device;
      let fmtChannel = row.key;
      if (row.key === 'pressure_alt_m') {
        val = smoothChannel('press_alt', pressAltSample, row.key);
        fmtDevice = 'press_alt';
      } else if (row.key === 'airspeed_dp_cal_pa') {
        val = smoothChannel('airspeed', airspeedSample, row.key);
        fmtDevice = 'airspeed';
        fmtChannel = 'airspeed_dp_pa';
      } else {
        val = smoothChannel(row.device, sources[row.device], row.key);
      }
      cell.innerHTML = fmtCell(fmtDevice, fmtChannel, val);
    }
  }

  return { renderPanel, INPUT_ROWS };
})();
