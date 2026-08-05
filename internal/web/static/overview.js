// Overview grid: multi-row device blocks, hub → pod → calc ordering.
const KFOverview = (function () {
  const GROUP_ORDER = { hub: 0, pod: 1, system: 2, calc: 3 };
  const SECTION_LABELS = { hub: 'Hub', pod: 'Pod', system: 'System', calc: 'Calc' };

  function compareOverviewDevices(a, b) {
    const ga = GROUP_ORDER[deviceTabGroup(a)] ?? 9;
    const gb = GROUP_ORDER[deviceTabGroup(b)] ?? 9;
    if (ga !== gb) return ga - gb;
    if (ga === 0) {
      if (a === 'gps') return 1;
      if (b === 'gps') return -1;
    }
    return a.localeCompare(b);
  }

  function sortedOverviewDevices(deviceNames) {
    return [...deviceNames].filter((n) => n !== 'pod').sort(compareOverviewDevices);
  }

  function groupedDevices(deviceNames) {
    const sorted = sortedOverviewDevices(deviceNames);
    const groups = [];
    let lastGroup = null;
    for (const name of sorted) {
      const g = deviceTabGroup(name);
      if (g !== lastGroup) {
        groups.push({ section: g, label: SECTION_LABELS[g] || g, devices: [] });
        lastGroup = g;
      }
      groups[groups.length - 1].devices.push(name);
    }
    return groups;
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }

  function renderBlock(device, sample) {
    const raw = sample?.values || {};
    const smoothed = typeof KFSmooth !== 'undefined' ? KFSmooth.values(device, raw) : raw;
    const vals = KFDisplay.withDerived(device, smoothed);
    const calVals = KFDisplay.applyTumbleCal(device, vals);
    const layout = KFDisplay.overviewLayout(device, vals);
    const loc = state.deviceLocation.get(device) || inferDeviceLocation(device) || '';
    let subHtml = '';
    for (const sr of layout.subRows) {
      let cells = '';
      let anyCal = false;
      for (const c of sr.cells) {
        const rawText = KFDisplay.formatOverviewCell(device, c.key, vals[c.key], vals);
        const dual = KFDisplay.channelHasTumbleCal(device, c.key);
        if (dual) {
          anyCal = true;
          const calText = KFDisplay.formatOverviewCell(device, c.key, calVals[c.key], calVals);
          cells +=
            `<span class="ovCell ovCellDual">` +
            `<span class="ovHdr">${escapeHtml(c.header)}</span>` +
            `<span class="ovVal ovValRaw" title="raw">${escapeHtml(rawText)}</span>` +
            `<span class="ovVal ovValCal" title="calibrated">${escapeHtml(calText)}</span>` +
            `</span>`;
        } else {
          cells +=
            `<span class="ovCell">` +
            `<span class="ovHdr">${escapeHtml(c.header)}</span>` +
            `<span class="ovVal">${escapeHtml(rawText)}</span>` +
            `</span>`;
        }
      }
      const label = sr.rowLabel
        ? `<span class="ovRowLbl">${escapeHtml(sr.rowLabel)}</span>`
        : '<span class="ovRowLbl"></span>';
      subHtml += `<div class="ovSubRow${anyCal ? ' ovSubRowDual' : ''}">${label}<div class="ovCells">${cells}</div></div>`;
    }
    if (!subHtml) {
      subHtml =
        '<div class="ovSubRow ovEmpty">' +
        '<span class="ovRowLbl"></span>' +
        '<span class="dim">No data yet</span></div>';
    }
    const displayName = KFDisplay.overviewDeviceName(device);
    const locBadge = loc ? `<span class="ovLoc">${escapeHtml(loc)}</span>` : '';
    return (
      `<article class="ovBlock" data-device="${escapeHtml(device)}" tabindex="0" role="button">` +
      `<div class="ovBlockHead"><span class="ovName" title="${escapeHtml(device)}">${escapeHtml(displayName)}</span>${locBadge}</div>` +
      `<div class="ovBlockBody">${subHtml}</div></article>`
    );
  }

  function render(container, getSample) {
    if (!container) return;
    const names = [...state.deviceNames];
    if (names.length === 0) {
      container.innerHTML = '<p class="dim ovEmptyPage">Waiting for sensor data…</p>';
      return;
    }
    let html = '';
    for (const group of groupedDevices(names)) {
      html += `<h3 class="ovSection">${escapeHtml(group.label)}</h3>`;
      for (const device of group.devices) {
        html += renderBlock(device, getSample(device));
      }
    }
    container.innerHTML = html;
  }

  return { render, sortedOverviewDevices, groupedDevices };
})();
