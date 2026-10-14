/* Kingfisher flight-analysis browser */
(() => {
  const COLORS = ["#4cb4ff", "#5cffa3", "#e6c04a", "#ff7ce0", "#fb923c", "#a78bfa", "#f87171"];
  const M_TO_FT = 3.28084;
  const MPS_TO_KT = 1.94384;
  const MPS_TO_FPM = 196.8503937;
  const DEFAULT_SERIES = [
    ["gps", "gs"],
    ["gps", "alt_msl"],
    ["ahrs", "roll"],
  ];

  const sessListEl = document.getElementById("sessList");
  const searchEl = document.getElementById("sessSearch");
  const detailEl = document.getElementById("detail");
  const emptyEl = document.getElementById("emptyHint");
  const issuesEl = document.getElementById("issues");
  const plotWrap = document.getElementById("plotWrap");
  const chipsEl = document.getElementById("seriesChips");
  const cursorEl = document.getElementById("cursorVals");
  const tablesEl = document.getElementById("tables");
  const healthEl = document.getElementById("healthBox");
  const seriesDlg = document.getElementById("seriesDlg");
  const seriesPicker = document.getElementById("seriesPicker");
  const seriesSearch = document.getElementById("seriesSearch");
  const alignEl = document.getElementById("alignClock");
  const hdrStatus = document.getElementById("hdrStatus");

  let sessions = [];
  let filter = "flights";
  let current = null; // detail payload
  let selected = []; // {table, column, color}
  let plot = null;
  let fetched = [];
  let t0ns = 0;
  let t1ns = 0;
  let fullT0 = 0;
  let fullT1 = 0;
  let fetchTimer = null;
  let suppressScale = false;
  let activeFile = "";

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // DB columns are SI (m, m/s). Plot and labels use aviation units.
  function aviSpec(column) {
    const c = column;
    if (c === "ias_kt" || c === "tas_kt" || c.endsWith("_kt")) return { mul: 1, unit: "kt" };
    if (c === "gs" || c === "gs_acc" || c === "speed" || c === "speed_mps" || c.endsWith("_mps")) {
      return { mul: MPS_TO_KT, unit: "kt" };
    }
    if (c.endsWith("_ft")) return { mul: 1, unit: "ft" };
    if (c === "alt_msl" || c === "h_acc" || c === "v_acc") return { mul: M_TO_FT, unit: "ft" };
    if (c.endsWith("_m") && /alt/.test(c)) return { mul: M_TO_FT, unit: "ft" };
    if (c === "vs" || c === "vs_ms" || c === "vs_acc") return { mul: MPS_TO_FPM, unit: "fpm" };
    if (c.endsWith("_deg_s") || c === "turn_rate_deg_s") return { mul: 1, unit: "°/s" };
    if (c === "roll" || c === "pitch" || c === "yaw" || c === "track" || c.endsWith("_deg")) {
      return { mul: 1, unit: "°" };
    }
    return { mul: 1, unit: "" };
  }

  function seriesName(table, column) {
    const { unit } = aviSpec(column);
    const id = `${table}.${column}`;
    return unit ? `${id} (${unit})` : id;
  }

  function selectedIndex(table, column) {
    return selected.findIndex((s) => s.table === table && s.column === column);
  }

  function nextColor() {
    const used = new Set(selected.map((s) => s.color));
    return COLORS.find((c) => !used.has(c)) || COLORS[selected.length % COLORS.length];
  }

  function fmtWhen(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 19);
    return d.toISOString().replace("T", " ").slice(0, 16) + "Z";
  }

  function recentDays(s, days) {
    const raw = s.true_start_utc || s.start_utc || s.file;
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) {
      const m = /^(\d{4})(\d{2})(\d{2})/.exec(s.file);
      if (!m) return false;
      return (Date.now() - Date.UTC(+m[1], +m[2] - 1, +m[3])) / 86400000 <= days;
    }
    return (Date.now() - d.getTime()) / 86400000 <= days;
  }

  function shown(s) {
    const q = searchEl.value.trim().toLowerCase();
    if (q) {
      const blob = `${s.file} ${s.notes_preview || ""} ${s.notes || ""} ${s.class}`.toLowerCase();
      if (!blob.includes(q)) return false;
    }
    if (filter === "all") return true;
    if (s.class === "flight") return true;
    if (!s.from_catalog && recentDays(s, 3) && s.size_mb >= 5) return true;
    return false;
  }

  function renderList() {
    const rows = sessions.filter(shown);
    sessListEl.innerHTML = rows.map((s) => {
      const when = fmtWhen(s.true_start_utc || s.start_utc);
      const gs = s.max_gs != null ? `${Math.round(s.max_gs * MPS_TO_KT)} kt` : "";
      const dur = s.duration_h != null && s.duration_h < 48 ? `${s.duration_h.toFixed(1)} h` : "";
      const note = esc((s.notes_preview || s.notes || "").slice(0, 80));
      return `<button type="button" class="sess${s.file === activeFile ? " active" : ""}" data-file="${esc(s.file)}">
        <div class="when">${esc(when)} <span class="badge ${s.class === "flight" ? "flight" : ""}">${esc(s.class)}</span></div>
        <div class="sub">${esc(s.file.replace(/_n456t\.db$/, ""))} ${gs} ${dur}</div>
        ${note ? `<div class="sub">${note}</div>` : ""}
      </button>`;
    }).join("") || `<p class="dim">No sessions match.</p>`;
  }

  async function loadSessions() {
    const r = await fetch("/api/sessions");
    const data = await r.json();
    sessions = data.sessions || [];
    hdrStatus.textContent = `${sessions.length} sessions`;
    renderList();
  }

  sessListEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".sess");
    if (btn) openSession(btn.dataset.file);
  });
  searchEl.addEventListener("input", renderList);
  document.querySelectorAll(".filters .chip").forEach((c) => {
    c.addEventListener("click", () => {
      filter = c.dataset.filter;
      document.querySelectorAll(".filters .chip").forEach((x) => x.classList.toggle("on", x === c));
      renderList();
    });
  });

  function pickDefaults(detail) {
    const have = new Set();
    for (const t of detail.tables || []) {
      for (const c of t.plot || []) have.add(`${t.name}.${c}`);
    }
    const out = [];
    for (const [table, column] of DEFAULT_SERIES) {
      if (have.has(`${table}.${column}`)) {
        out.push({ table, column, color: COLORS[out.length % COLORS.length] });
      }
    }
    if (!out.length) {
      const gps = (detail.tables || []).find((t) => t.name === "gps");
      if (gps && gps.plot.length) {
        out.push({ table: "gps", column: gps.plot.find((c) => c !== "ts_ns") || gps.plot[0], color: COLORS[0] });
      }
    }
    return out;
  }

  async function openSession(file) {
    activeFile = file;
    renderList();
    emptyEl.hidden = true;
    detailEl.hidden = false;
    document.getElementById("detailTitle").textContent = file;
    document.getElementById("detailMeta").textContent = "Loading…";
    issuesEl.hidden = true;
    healthEl.textContent = "Sampling health…";
    tablesEl.innerHTML = "";
    destroyPlot();
    const r = await fetch(`/api/sessions/${encodeURIComponent(file)}`);
    if (!r.ok) {
      document.getElementById("detailMeta").textContent = "Failed to load session";
      return;
    }
    current = await r.json();
    const clock = current.clock || {};
    const bits = [
      current.class,
      current.true_start_utc || current.start_utc,
      current.plot_t1_utc ? `→ ${current.plot_t1_utc}` : "",
      current.max_gs != null ? `max gs ${Math.round(current.max_gs * MPS_TO_KT)} kt` : "",
      current.has_pitot ? "pitot" : "no pitot",
      `${(current.size_mb || 0).toFixed(0)} MB`,
      (current.notes || current.notes_preview || "").split("\n")[0].slice(0, 100),
    ].filter(Boolean);
    document.getElementById("detailMeta").textContent = bits.join(" · ");
    document.getElementById("detailTitle").textContent = file.replace(/\.db$/, "");

    const issues = [...(clock.issues || [])];
    if (current.missing_core_cabin?.length) issues.push("missing cabin: " + current.missing_core_cabin.join(", "));
    if (current.missing_core_pod?.length) issues.push("missing pod: " + current.missing_core_pod.join(", "));
    if (issues.length) {
      issuesEl.hidden = false;
      issuesEl.innerHTML = "<ul>" + issues.map((i) => `<li>${esc(i)}</li>`).join("") + "</ul>";
    } else {
      issuesEl.hidden = true;
    }
    const clockBox = document.getElementById("clockBox");
    const offs = clock.clock_offsets || [];
    if (offs.length) {
      clockBox.hidden = false;
      clockBox.innerHTML = `<div>clock_offsets (o = realtime − monotonic)</div>
        <table><thead><tr><th>note</th><th>UTC</th><th>Δo</th><th>monotonic</th></tr></thead><tbody>` +
        offs.map((m) => `<tr>
          <td>${esc(m.note)}</td>
          <td>${esc(m.utc || "")}</td>
          <td>${m.delta_s >= 1 ? (m.delta_s / 3600).toFixed(2) + " h" : (m.delta_s * 1000).toFixed(1) + " ms"}</td>
          <td>${(m.monotonic_ns / 1e9).toFixed(3)} s</td>
        </tr>`).join("") + "</tbody></table>";
    } else {
      clockBox.hidden = true;
    }
    alignEl.checked = true;
    alignEl.disabled = !clock.needs_align;

    fullT0 = current.plot_t0_ns;
    fullT1 = current.plot_t1_ns;
    t0ns = fullT0;
    t1ns = fullT1;
    selected = pickDefaults(current);
    renderTables();
    renderChips();
    await reloadSeries();
    loadHealth(file);
  }

  function renderTables() {
    const first = !tablesEl.querySelector("details.tbl");
    const open = new Set(
      [...tablesEl.querySelectorAll("details.tbl[open]")].map((d) => d.dataset.table)
    );
    tablesEl.innerHTML = (current.tables || []).map((t) => {
      const mark = t.core_cabin ? "cabin" : t.core_pod ? "pod" : t.optional_pod ? "pitot" : "";
      const cols = (t.plot || []).map((c) => {
        const i = selectedIndex(t.name, c);
        const on = i >= 0;
        const { unit } = aviSpec(c);
        const label = unit ? `${c} (${unit})` : c;
        const style = on ? ` style="color:${selected[i].color};border-color:currentColor"` : "";
        return `<button type="button" class="colBtn${on ? " on" : ""}"${style} data-table="${esc(t.name)}" data-col="${esc(c)}">${esc(label)}</button>`;
      }).join("");
      const isOpen = open.has(t.name) || (first && selected.some((s) => s.table === t.name));
      return `<details class="tbl"${isOpen ? " open" : ""} data-table="${esc(t.name)}">
        <summary>${esc(t.name)} <span class="dim">${t.rows || 0} rows${mark ? " · " + mark : ""}</span></summary>
        <div class="dim">${esc(t.t0_utc || "")} → ${esc(t.t1_utc || "")}</div>
        <div class="cols">${cols}</div>
      </details>`;
    }).join("");
  }

  tablesEl.addEventListener("click", (e) => {
    const b = e.target.closest(".colBtn");
    if (!b) return;
    toggleSeries(b.dataset.table, b.dataset.col);
  });

  function toggleSeries(table, column) {
    const i = selectedIndex(table, column);
    if (i >= 0) removeSeries(i);
    else addSeries(table, column);
  }

  function addSeries(table, column) {
    if (selectedIndex(table, column) >= 0) return;
    selected.push({ table, column, color: nextColor() });
    renderChips();
    reloadSeries();
  }

  function removeSeries(i) {
    selected.splice(i, 1);
    renderChips();
    reloadSeries();
  }

  function renderChips() {
    chipsEl.innerHTML = selected.map((s, i) =>
      `<span class="chip schip" style="color:${s.color}">${esc(seriesName(s.table, s.column))}
        <button type="button" class="x" data-i="${i}" aria-label="remove">×</button></span>`
    ).join("");
    if (current) renderTables();
  }

  chipsEl.addEventListener("click", (e) => {
    const x = e.target.closest(".x");
    if (x) removeSeries(+x.dataset.i);
  });

  document.getElementById("addSeriesBtn").addEventListener("click", () => {
    fillPicker();
    seriesDlg.showModal();
    seriesSearch.value = "";
    seriesSearch.focus();
  });

  function fillPicker() {
    const q = seriesSearch.value.trim().toLowerCase();
    const items = [];
    for (const t of current?.tables || []) {
      for (const c of t.plot || []) {
        const id = `${t.name}.${c}`;
        if (q && !id.toLowerCase().includes(q)) continue;
        items.push(`<button type="button" data-table="${esc(t.name)}" data-col="${esc(c)}">${esc(seriesName(t.name, c))}</button>`);
      }
    }
    seriesPicker.innerHTML = items.join("") || `<p class="dim">No columns.</p>`;
  }

  seriesSearch.addEventListener("input", fillPicker);
  seriesPicker.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    toggleSeries(b.dataset.table, b.dataset.col);
  });

  alignEl.addEventListener("change", () => {
    t0ns = fullT0;
    t1ns = fullT1;
    reloadSeries();
  });

  document.getElementById("resetZoomBtn").addEventListener("click", () => {
    t0ns = fullT0;
    t1ns = fullT1;
    reloadSeries();
  });

  async function loadHealth(file) {
    try {
      const r = await fetch(`/api/sessions/${encodeURIComponent(file)}/health`);
      const h = await r.json();
      const grade = h.grade || "n/a";
      const rows = (h.tables || []).map((t) =>
        `<tr>
          <td>${esc(t.table)}</td>
          <td class="${esc(t.grade)}">${esc(t.grade)}</td>
          <td>${t.rows}</td>
          <td>${t.mean_hz != null ? t.mean_hz.toFixed(2) : "—"}</td>
          <td>${t.max_gap_s != null ? t.max_gap_s.toFixed(2) : "—"}</td>
          <td>${t.gaps_gt_1s}</td>
        </tr>`
      ).join("");
      healthEl.innerHTML = `<div>Health <span class="${esc(grade)}">${esc(grade)}</span>
        · coverage ${esc(h.coverage)} · airborne ${h.airborne_min} min
        ${h.notes?.length ? `<div class="warn">${h.notes.map(esc).join(" · ")}</div>` : ""}
        <table>
          <thead><tr><th>Table</th><th>Grade</th><th>Rows</th><th>Hz</th><th>Max gap s</th><th>Gaps &gt;1s</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>`;
    } catch (err) {
      healthEl.textContent = "Health failed: " + err.message;
    }
  }

  function destroyPlot() {
    if (plot) {
      plot.destroy();
      plot = null;
    }
    plotWrap.innerHTML = "";
  }

  async function reloadSeries() {
    if (!current || !selected.length || !t0ns || !t1ns) {
      fetched = [];
      destroyPlot();
      return;
    }
    const align = alignEl.checked ? "1" : "0";
    const n = Math.round(plotWrap.clientWidth * 1.2) || 2000;
    const results = await Promise.all(selected.map(async (s) => {
      const u = new URL(`/api/sessions/${encodeURIComponent(current.file)}/series`, location.origin);
      u.searchParams.set("table", s.table);
      u.searchParams.set("col", s.column);
      u.searchParams.set("t0", String(t0ns));
      u.searchParams.set("t1", String(t1ns));
      u.searchParams.set("n", String(n));
      u.searchParams.set("align", align);
      const r = await fetch(u);
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      const spec = aviSpec(s.column);
      data.color = s.color;
      data.unit = spec.unit;
      data.label = seriesName(s.table, s.column);
      data.y = data.y.map((v) => (v == null ? null : v * spec.mul));
      return data;
    }));
    fetched = results;
    drawPlot();
  }

  function merge(seriesList) {
    const xs = [...new Set(seriesList.flatMap((s) => s.t.filter((v) => v != null)))].sort((a, b) => a - b);
    const ys = seriesList.map((s) => {
      const m = new Map();
      for (let i = 0; i < s.t.length; i++) {
        if (s.t[i] == null) continue;
        m.set(s.t[i], s.y[i] == null ? null : s.y[i]);
      }
      return xs.map((t) => (m.has(t) ? m.get(t) : null));
    });
    return [xs, ...ys];
  }

  function fmtTick(ts) {
    const d = new Date(ts * 1000);
    const spanH = (t1ns - t0ns) / 1e9 / 3600;
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mm = String(d.getUTCMinutes()).padStart(2, "0");
    const ss = String(d.getUTCSeconds()).padStart(2, "0");
    if (spanH > 36) return d.toISOString().slice(0, 10);
    if (spanH > 3) return `${hh}:${mm}Z`;
    return `${hh}:${mm}:${ss}Z`;
  }

  function fmtAvi(v, unit) {
    const abs = Math.abs(v);
    let n;
    if (unit === "kt" || unit === "ft" || unit === "fpm") n = abs >= 100 ? v.toFixed(0) : v.toFixed(1);
    else if (unit === "°" || unit === "°/s") n = v.toFixed(1);
    else n = Number(v).toPrecision(5);
    return unit ? `${n} ${unit}` : n;
  }

  function drawPlot() {
    destroyPlot();
    if (!fetched.length) return;
    const data = merge(fetched);
    const w = plotWrap.clientWidth || 800;
    const h = plotWrap.clientHeight || 340;
    const unitKeys = [];
    for (const s of fetched) {
      const u = s.unit || "y";
      if (!unitKeys.includes(u)) unitKeys.push(u);
    }
    const scales = {
      x: { time: true, min: t0ns / 1e9, max: t1ns / 1e9 },
    };
    for (const u of unitKeys) scales[u] = {};
    const series = [{ label: "UTC" }].concat(fetched.map((s) => ({
      label: s.label,
      stroke: s.color,
      scale: s.unit || "y",
      width: 1.4,
      spanGaps: true,
      points: { show: false },
    })));
    const yAxes = unitKeys.map((u, i) => ({
      scale: u,
      side: i % 2 === 1 ? 1 : 3,
      stroke: "#7d8aa5",
      grid: { stroke: i === 0 ? "#1f2a44" : "transparent" },
      ticks: { stroke: "#1f2a44" },
      label: u === "y" ? "" : u,
      labelFont: "12px ui-sans-serif, system-ui, sans-serif",
    }));
    suppressScale = true;
    plot = new uPlot({
      width: w,
      height: h,
      padding: [8, 12, 8, 8],
      series,
      scales,
      axes: [
        {
          stroke: "#7d8aa5",
          grid: { stroke: "#1f2a44" },
          ticks: { stroke: "#1f2a44" },
          values: (_u, splits) => splits.map(fmtTick),
        },
        ...yAxes,
      ],
      legend: { show: false },
      cursor: {
        drag: { x: true, y: false },
        points: { size: 6 },
      },
      hooks: {
        setCursor: [
          (u) => {
            const i = u.cursor.idx;
            if (i == null || i < 0) {
              cursorEl.textContent = "";
              return;
            }
            const t = u.data[0][i];
            const parts = [`${new Date(t * 1000).toISOString().slice(11, 23)}Z`];
            fetched.forEach((s, k) => {
              const v = u.data[k + 1][i];
              if (v != null) parts.push(`${s.label}=${fmtAvi(v, s.unit)}`);
            });
            cursorEl.textContent = parts.join("  ");
          },
        ],
        setScale: [
          (u, key) => {
            if (key !== "x" || suppressScale) return;
            const min = u.scales.x.min;
            const max = u.scales.x.max;
            if (min == null || max == null) return;
            const nt0 = Math.round(min * 1e9);
            const nt1 = Math.round(max * 1e9);
            if (Math.abs(nt0 - t0ns) < 1e6 && Math.abs(nt1 - t1ns) < 1e6) return;
            t0ns = nt0;
            t1ns = nt1;
            clearTimeout(fetchTimer);
            fetchTimer = setTimeout(() => reloadSeries(), 180);
          },
        ],
      },
    }, data, plotWrap);
    requestAnimationFrame(() => { suppressScale = false; });
    bindWheel(plot);
  }

  function bindWheel(u) {
    const over = u.over;
    over.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = over.getBoundingClientRect();
      const frac = (e.clientX - rect.left) / rect.width;
      const min = u.scales.x.min;
      const max = u.scales.x.max;
      const span = max - min;
      if (e.shiftKey) {
        const pan = span * 0.08 * (e.deltaY > 0 ? 1 : -1);
        u.setScale("x", { min: min + pan, max: max + pan });
        return;
      }
      const zoom = e.deltaY > 0 ? 1.25 : 0.8;
      const nspan = span * zoom;
      u.setScale("x", { min: min + (span - nspan) * frac, max: max - (span - nspan) * (1 - frac) });
    }, { passive: false });
  }

  window.addEventListener("resize", () => {
    if (plot && current) {
      plot.setSize({ width: plotWrap.clientWidth, height: plotWrap.clientHeight });
    }
  });

  loadSessions().catch((e) => {
    sessListEl.innerHTML = `<p class="bad">${esc(e.message)}</p>`;
  });
})();
