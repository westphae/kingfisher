// Howgozit — in-flight manual log tab.
const KFHowgozit = (function () {
  const PATCH_DEBOUNCE_MS = 400;

  const DEFAULT_NUMBER_STEP = {
    baro_inhg: '0.01',
    mp_inhg: '0.01',
  };

  const ui = {
    root: null,
    tabsEl: null,
    tableWrap: null,
    addLogDlg: null,
    editDlg: null,
    toTmplDlg: null,
  };

  const data = {
    templates: [],
    logs: [],
    activeLogId: null,
    rows: [],
    prefill: new Map(),
  };

  let editDraft = null;
  let editOriginalKeys = new Set();

  let patchTimers = new Map();
  let mounted = false;
  let resizeWired = false;

  async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body != null) opts.body = JSON.stringify(body);
    const res = await fetch('/api/howgozit' + path, opts);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  function activeLog() {
    return data.logs.find((l) => l.log_id === data.activeLogId) || null;
  }

  function fieldsForLog(log) {
    return log?.fields || [];
  }

  function formatTime(tsNs) {
    if (!tsNs) return '';
    const d = new Date(tsNs / 1e6);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  }

  function parseTimeToTsNs(hhmm, baseTsNs) {
    const parts = String(hhmm).trim().split(':');
    if (parts.length < 2) return null;
    const h = Number(parts[0]);
    const m = Number(parts[1]);
    if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
    const base = new Date((baseTsNs || Date.now() * 1e6) / 1e6);
    base.setHours(h, m, 0, 0);
    return Math.round(base.getTime() * 1e6);
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function mount(rootEl) {
    ui.root = rootEl;
    if (rootEl.querySelector('[data-hgz-root]')) {
      bindShellRefs(rootEl);
      wireEditForm();
      wireResize();
      mounted = true;
      return;
    }
    rootEl.innerHTML =
      '<div class="hgz-root" data-hgz-root>' +
      '<div class="hgz-toolbar">' +
      '<div class="hgz-tabs" data-hgz-tabs role="tablist"></div>' +
      '<button type="button" class="hgz-iconBtn" data-hgz-add-log title="Add log">+ Log</button>' +
      '<button type="button" class="hgz-iconBtn" data-hgz-edit title="Edit log">Edit</button>' +
      '<button type="button" class="hgz-iconBtn" data-hgz-to-tmpl title="Save log as template">→ Template</button>' +
      '</div>' +
      '<div class="hgz-scroll" data-hgz-scroll>' +
      '<table class="hgz-table" data-hgz-table><thead data-hgz-head></thead><tbody data-hgz-body></tbody></table>' +
      '</div>' +
      '<dialog class="sheet" data-hgz-add-log-dlg><form method="dialog">' +
      '<h2>Add log</h2>' +
      '<p class="dim hgz-dlgHint">Pick a template to seed columns, or start blank.</p>' +
      '<div class="hgz-pick-list" data-hgz-add-log-list></div>' +
      '<div class="dlgRow"><button value="cancel">Cancel</button></div></form></dialog>' +
      '<dialog class="sheet sheet-wide" data-hgz-edit-dlg><form method="dialog" id="hgzEditForm">' +
      '<h2>Edit log</h2>' +
      '<label>Log name <input name="display_name" autocomplete="off" required /></label>' +
      '<p class="dim hgz-dlgHint">Column keys are fixed after creation; edit labels and parameters below.</p>' +
      '<div class="hgz-edit-fields" data-hgz-edit-fields></div>' +
      '<div class="hgz-edit-add" data-hgz-edit-add>' +
      '<h3>Add column</h3>' +
      '<div class="hgz-edit-add-grid">' +
      '<label>Key <input name="new_key" pattern="[A-Za-z0-9_]+" autocomplete="off" /></label>' +
      '<label>Label <input name="new_label" autocomplete="off" /></label>' +
      '<label>Type <select name="new_type"><option value="text">text</option><option value="number" selected>number</option><option value="select">select</option></select></label>' +
      '<label>Unit <input name="new_unit" autocomplete="off" placeholder="optional" /></label>' +
      '<label>Step <input name="new_step" autocomplete="off" placeholder="e.g. 0.01" /></label>' +
      '<label>Input mode <select name="new_input_mode"><option value="">default</option><option value="decimal">decimal</option><option value="numeric">numeric</option><option value="text">text</option></select></label>' +
      '<label data-hgz-edit-new-options hidden>Options (comma-separated) <input name="new_options" autocomplete="off" /></label>' +
      '</div>' +
      '<button type="button" class="hgz-edit-addBtn" data-hgz-edit-add-col>+ Add column</button>' +
      '</div>' +
      '<div class="hgz-edit-danger"><button type="button" class="hgz-delLogBtn" data-hgz-delete-log>Delete log</button></div>' +
      '<div class="dlgRow"><button type="submit" value="save">Save</button><button value="cancel">Cancel</button></div>' +
      '</form></dialog>' +
      '<dialog class="sheet" data-hgz-to-tmpl-dlg><form method="dialog" id="hgzToTmplForm">' +
      '<h2>Save as template</h2>' +
      '<label>Template id <input name="id" required pattern="[A-Za-z0-9_]+" autocomplete="off" /></label>' +
      '<label>Name <input name="name" autocomplete="off" /></label>' +
      '<label class="cfgCheckbox"><input name="replace" type="checkbox" /> Replace existing template with same id</label>' +
      '<div class="dlgRow"><button type="submit" value="save">Save</button><button value="cancel">Cancel</button></div></form></dialog>';

    bindShellRefs(rootEl);
    rootEl.querySelector('[data-hgz-add-log]')?.addEventListener('click', () => openAddLogDialog());
    rootEl.querySelector('[data-hgz-edit]')?.addEventListener('click', () => openEditDialog());
    rootEl.querySelector('[data-hgz-to-tmpl]')?.addEventListener('click', () => openToTemplateDialog());
    wireEditForm();
    wireToTemplateForm();
    wireResize();
    mounted = true;
  }

  function wireResize() {
    if (resizeWired) return;
    resizeWired = true;
    let timer;
    window.addEventListener('resize', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const headEl = ui.root?.querySelector('[data-hgz-head]');
        const bodyEl = ui.root?.querySelector('[data-hgz-body]');
        if (headEl && bodyEl) fitColumnWidths(headEl, bodyEl);
      }, 150);
    });
  }

  function bindShellRefs(rootEl) {
    ui.tabsEl = rootEl.querySelector('[data-hgz-tabs]');
    ui.tableWrap = rootEl.querySelector('[data-hgz-scroll]');
    ui.addLogDlg = rootEl.querySelector('[data-hgz-add-log-dlg]');
    ui.editDlg = rootEl.querySelector('[data-hgz-edit-dlg]');
    ui.toTmplDlg = rootEl.querySelector('[data-hgz-to-tmpl-dlg]');
  }

  async function loadAll() {
    const [tmplRes, logRes] = await Promise.all([
      api('GET', '/templates'),
      api('GET', '/logs'),
    ]);
    data.templates = tmplRes.templates || [];
    data.logs = logRes.logs || [];
    if (data.logs.length === 0) {
      data.activeLogId = null;
      data.rows = [];
      data.prefill.clear();
      return;
    }
    if (!data.activeLogId) {
      data.activeLogId = data.logs[0].log_id;
    }
    if (data.activeLogId && !data.logs.some((l) => l.log_id === data.activeLogId)) {
      data.activeLogId = data.logs[0]?.log_id || null;
    }
  }

  async function loadRows() {
    const log = activeLog();
    if (!log) {
      data.rows = [];
      return;
    }
    const res = await api('GET', `/logs/${encodeURIComponent(log.log_id)}/rows`);
    data.rows = res.rows || [];
  }

  function syncLogMeta(meta) {
    const idx = data.logs.findIndex((l) => l.log_id === meta.log_id);
    if (idx >= 0) data.logs[idx] = meta;
    else data.logs.push(meta);
  }

  function renderTabs() {
    if (!ui.tabsEl) return;
    let html = '';
    for (const log of data.logs) {
      const active = log.log_id === data.activeLogId ? ' hgz-tab-active' : '';
      html += `<button type="button" class="hgz-tab${active}" role="tab" data-log-id="${escapeHtml(log.log_id)}">${escapeHtml(log.display_name)}</button>`;
    }
    ui.tabsEl.innerHTML = html;
    for (const btn of ui.tabsEl.querySelectorAll('.hgz-tab')) {
      btn.addEventListener('click', async () => {
        data.activeLogId = btn.dataset.logId;
        data.prefill.clear();
        renderTabs();
        await loadRows();
        renderTable();
      });
    }
  }

  function cellInputType(field) {
    if (field.type === 'select') return 'select';
    if (field.type === 'text') return 'text';
    return 'number';
  }

  function numberFieldAttrs(field) {
    const step = field.step || DEFAULT_NUMBER_STEP[field.key];
    const im = field.input_mode || 'decimal';
    if (step) {
      return ` type="number" step="${escapeHtml(step)}" inputmode="${escapeHtml(im)}"`;
    }
    const mode = field.input_mode || 'decimal';
    return ` type="text" inputmode="${escapeHtml(mode)}"`;
  }

  function renderTable() {
    const log = activeLog();
    const headEl = ui.root?.querySelector('[data-hgz-head]');
    const bodyEl = ui.root?.querySelector('[data-hgz-body]');
    if (!headEl || !bodyEl) return;

    if (!log) {
      headEl.innerHTML = '';
      bodyEl.innerHTML = '<tr><td class="hgz-empty">No logs — tap + Log to start.</td></tr>';
      return;
    }

    const fields = fieldsForLog(log);
    let head = '<tr><th class="hgz-col-time">Time</th>';
    for (const f of fields) {
      const label = f.unit ? `${f.label} (${f.unit})` : f.label;
      head += `<th>${escapeHtml(label)}</th>`;
    }
    head += '<th class="hgz-col-del"></th></tr>';
    headEl.innerHTML = head;
    headEl.querySelector('tr')?.classList.add('hgz-head-row');

    let body = '';
    for (const row of data.rows) {
      const prefilled = data.prefill.get(row.rowid) || new Set();
      body += `<tr class="hgz-data-row" data-rowid="${row.rowid}">`;
      const timePrefill = prefilled.has('__time__');
      body += `<td class="hgz-col-time"><input type="text" class="hgz-cell${timePrefill ? ' hgz-prefill' : ''}" data-field="__time__" value="${escapeHtml(formatTime(row.ts_ns))}" inputmode="numeric" /></td>`;
      for (const f of fields) {
        const val = row.values?.[f.key] ?? '';
        const isPrefill = prefilled.has(f.key);
        const kind = cellInputType(f);
        if (kind === 'select') {
          let opts = '<option value="">—</option>';
          for (const o of f.options || []) {
            const sel = String(val) === String(o) ? ' selected' : '';
            opts += `<option value="${escapeHtml(o)}"${sel}>${escapeHtml(o)}</option>`;
          }
          body += `<td><select class="hgz-cell${isPrefill ? ' hgz-prefill' : ''}" data-field="${escapeHtml(f.key)}">${opts}</select></td>`;
        } else if (kind === 'number') {
          body += `<td><input class="hgz-cell${isPrefill ? ' hgz-prefill' : ''}" data-field="${escapeHtml(f.key)}" value="${escapeHtml(val)}"${numberFieldAttrs(f)} /></td>`;
        } else {
          body += `<td><input type="text" class="hgz-cell${isPrefill ? ' hgz-prefill' : ''}" data-field="${escapeHtml(f.key)}" value="${escapeHtml(val)}" inputmode="text" /></td>`;
        }
      }
      body += `<td class="hgz-col-del"><button type="button" class="hgz-delBtn" data-del-row="${row.rowid}" title="Delete row">×</button></td></tr>`;
    }
    const colSpan = fields.length + 2;
    body += `<tr class="hgz-add-row" data-hgz-add-row role="button" tabindex="0"><td colspan="${colSpan}">Add new row</td></tr>`;
    bodyEl.innerHTML = body;

    for (const inp of bodyEl.querySelectorAll('.hgz-cell')) {
      const handler = () => onCellEdit(inp);
      inp.addEventListener('input', handler);
      inp.addEventListener('change', handler);
      inp.addEventListener('blur', () => {
        flushPatch(inp, true);
        fitColumnWidths(headEl, bodyEl);
      });
    }
    for (const btn of bodyEl.querySelectorAll('[data-del-row]')) {
      btn.addEventListener('click', () => deleteRow(Number(btn.dataset.delRow)));
    }
    const addRowEl = bodyEl.querySelector('[data-hgz-add-row]');
    addRowEl?.addEventListener('click', () => addRow());
    addRowEl?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        addRow();
      }
    });
    fitColumnWidths(headEl, bodyEl);
  }

  function fitColumnWidths(headEl, bodyEl) {
    const heads = [...headEl.querySelectorAll('th')];
    if (!heads.length) return;

    const clear = (cells) => {
      for (const c of cells) {
        c.style.width = '';
        c.style.minWidth = '';
        c.style.maxWidth = '';
        c.style.flexBasis = '';
      }
    };
    clear(heads);
    for (const tr of bodyEl.querySelectorAll('tr.hgz-data-row')) {
      clear([...tr.querySelectorAll('td')]);
    }

    const measureCell = (cell) => {
      const control = cell.querySelector('.hgz-cell, .hgz-delBtn');
      if (control) {
        return Math.ceil(Math.max(cell.offsetWidth, control.scrollWidth));
      }
      return cell.offsetWidth;
    };

    const widths = heads.map((th) => measureCell(th));
    for (const tr of bodyEl.querySelectorAll('tr.hgz-data-row')) {
      tr.querySelectorAll('td').forEach((td, i) => {
        widths[i] = Math.max(widths[i] || 0, measureCell(td));
      });
    }

    const apply = (cells) => {
      cells.forEach((cell, i) => {
        const w = Math.ceil(widths[i] || 0);
        if (w <= 0) return;
        cell.style.flexBasis = `${w}px`;
        cell.style.width = `${w}px`;
        cell.style.maxWidth = `${w}px`;
      });
    };
    apply(heads);
    for (const tr of bodyEl.querySelectorAll('tr.hgz-data-row')) {
      apply([...tr.querySelectorAll('td')]);
    }
  }

  function clearPrefill(rowid, field) {
    const set = data.prefill.get(rowid);
    if (!set) return;
    set.delete(field);
    if (set.size === 0) data.prefill.delete(rowid);
  }

  function onCellEdit(inp) {
    const tr = inp.closest('tr');
    if (!tr) return;
    const rowid = Number(tr.dataset.rowid);
    const field = inp.dataset.field;
    inp.classList.remove('hgz-prefill');
    clearPrefill(rowid, field);
    flushPatch(inp, false);
  }

  function flushPatch(inp, immediate) {
    const tr = inp.closest('tr');
    if (!tr) return;
    const rowid = Number(tr.dataset.rowid);
    const field = inp.dataset.field;
    const key = `${rowid}:${field}`;
    if (patchTimers.has(key)) {
      clearTimeout(patchTimers.get(key));
      patchTimers.delete(key);
    }
    const run = () => patchCell(rowid, field, inp);
    if (immediate) run();
    else patchTimers.set(key, setTimeout(run, PATCH_DEBOUNCE_MS));
  }

  async function patchCell(rowid, field, inp) {
    const log = activeLog();
    if (!log) return;
    const row = data.rows.find((r) => r.rowid === rowid);
    if (!row) return;

    if (field === '__time__') {
      const ts = parseTimeToTsNs(inp.value, row.ts_ns);
      if (ts == null) return;
      try {
        await api('PATCH', `/logs/${encodeURIComponent(log.log_id)}/rows/${rowid}`, { ts_ns: ts });
        row.ts_ns = ts;
        inp.value = formatTime(ts);
      } catch (e) {
        console.error('howgozit time patch', e);
      }
      return;
    }

    const val = inp.value;
    if (row.values == null) row.values = {};
    row.values[field] = val;
    try {
      await api('PATCH', `/logs/${encodeURIComponent(log.log_id)}/rows/${rowid}`, {
        values: { [field]: val },
      });
    } catch (e) {
      console.error('howgozit patch', e);
    }
  }

  async function addRow() {
    const log = activeLog();
    if (!log) {
      openAddLogDialog();
      return;
    }
    const fields = fieldsForLog(log);
    const last = data.rows.length ? data.rows[data.rows.length - 1] : null;
    const values = {};
    const prefillSet = new Set();
    if (last?.values) {
      for (const f of fields) {
        if (last.values[f.key] != null && last.values[f.key] !== '') {
          values[f.key] = last.values[f.key];
          prefillSet.add(f.key);
        }
      }
    }
    const tsNs = Date.now() * 1e6;
    try {
      const row = await api('POST', `/logs/${encodeURIComponent(log.log_id)}/rows`, {
        ts_ns: tsNs,
        values,
      });
      if (prefillSet.size) {
        prefillSet.add('__time__');
        data.prefill.set(row.rowid, prefillSet);
      }
      data.rows.push(row);
      renderTable();
      ui.tableWrap?.scrollTo({ top: ui.tableWrap.scrollHeight, behavior: 'smooth' });
    } catch (e) {
      console.error('howgozit add row', e);
    }
  }

  async function deleteRow(rowid) {
    const log = activeLog();
    if (!log || !confirm('Delete this row?')) return;
    try {
      await api('DELETE', `/logs/${encodeURIComponent(log.log_id)}/rows/${rowid}`);
      data.rows = data.rows.filter((r) => r.rowid !== rowid);
      data.prefill.delete(rowid);
      renderTable();
    } catch (e) {
      console.error('howgozit delete row', e);
    }
  }

  function openAddLogDialog() {
    const list = ui.addLogDlg?.querySelector('[data-hgz-add-log-list]');
    if (!list) return;
    let html =
      '<button type="button" class="hgz-pick-item hgz-pick-new" data-pick-new>New blank log</button>';
    for (const tmpl of data.templates) {
      html +=
        `<button type="button" class="hgz-pick-item" data-pick-tmpl="${escapeHtml(tmpl.id)}">` +
        `<span class="hgz-pick-name">${escapeHtml(tmpl.name)}</span>` +
        `<span class="dim hgz-pick-meta">${tmpl.fields.length} col(s)</span></button>`;
    }
    list.innerHTML = html;
    list.querySelector('[data-pick-new]')?.addEventListener('click', () => {
      ui.addLogDlg.close();
      createLog({ new: true, name: 'New log' });
    });
    for (const btn of list.querySelectorAll('[data-pick-tmpl]')) {
      btn.addEventListener('click', () => {
        ui.addLogDlg.close();
        createLog({ template_id: btn.dataset.pickTmpl });
      });
    }
    ui.addLogDlg.showModal();
  }

  async function createLog(body) {
    try {
      const meta = await api('POST', '/logs', body);
      syncLogMeta(meta);
      data.activeLogId = meta.log_id;
      data.prefill.clear();
      await loadRows();
      renderTabs();
      renderTable();
    } catch (e) {
      console.error('howgozit create log', e);
      alert('Could not create log: ' + e.message);
    }
  }

  function cloneField(f) {
    const out = {
      key: f.key,
      label: f.label,
      type: f.type || 'number',
    };
    if (f.unit) out.unit = f.unit;
    if (f.step) out.step = f.step;
    if (f.input_mode) out.input_mode = f.input_mode;
    if (f.options?.length) out.options = [...f.options];
    return out;
  }

  function fieldFromDraftRow(rowEl) {
    const idx = Number(rowEl.dataset.fieldIdx);
    const field = { ...editDraft.fields[idx] };
    field.label = rowEl.querySelector('[data-f-label]')?.value?.trim() || field.key;
    field.type = rowEl.querySelector('[data-f-type]')?.value || 'number';
    field.unit = rowEl.querySelector('[data-f-unit]')?.value?.trim() || '';
    field.step = rowEl.querySelector('[data-f-step]')?.value?.trim() || '';
    field.input_mode = rowEl.querySelector('[data-f-input-mode]')?.value?.trim() || '';
    if (field.type === 'select') {
      const raw = rowEl.querySelector('[data-f-options]')?.value || '';
      field.options = raw.split(',').map((s) => s.trim()).filter(Boolean);
    } else {
      delete field.options;
    }
    if (!field.unit) delete field.unit;
    if (!field.step) delete field.step;
    if (!field.input_mode) delete field.input_mode;
    return field;
  }

  function syncDraftFromDom() {
    if (!editDraft) return;
    const form = document.getElementById('hgzEditForm');
    editDraft.display_name = form?.querySelector('[name="display_name"]')?.value?.trim() || editDraft.display_name;
    const rows = ui.editDlg?.querySelectorAll('[data-hgz-field-row]') || [];
    rows.forEach((row) => {
      const idx = Number(row.dataset.fieldIdx);
      editDraft.fields[idx] = fieldFromDraftRow(row);
    });
  }

  function columnHasRowData(key) {
    return data.rows.some((row) => {
      const v = row.values?.[key];
      return v != null && String(v).trim() !== '';
    });
  }

  function renderEditFields() {
    const list = ui.editDlg?.querySelector('[data-hgz-edit-fields]');
    if (!list || !editDraft) return;
    if (!editDraft.fields.length) {
      list.innerHTML = '<p class="dim">No columns yet — add one below.</p>';
      return;
    }
    let html = '';
    editDraft.fields.forEach((f, idx) => {
      const isExisting = editOriginalKeys.has(f.key);
      const typeOpts = ['number', 'text', 'select']
        .map((t) => `<option value="${t}"${f.type === t ? ' selected' : ''}>${t}</option>`)
        .join('');
      const imOpts = ['', 'decimal', 'numeric', 'text']
        .map((t) => {
          const label = t || 'default';
          const sel = (f.input_mode || '') === t ? ' selected' : '';
          return `<option value="${t}"${sel}>${label}</option>`;
        })
        .join('');
      const optionsVal = (f.options || []).join(', ');
      html +=
        `<div class="hgz-edit-field" data-hgz-field-row data-field-idx="${idx}">` +
        `<div class="hgz-edit-field-head">` +
        `<span class="dim hgz-edit-key">${escapeHtml(f.key)}</span>` +
        `<span class="hgz-edit-field-actions">` +
        `<button type="button" class="hgz-edit-move" data-move-up title="Move up">↑</button>` +
        `<button type="button" class="hgz-edit-move" data-move-down title="Move down">↓</button>` +
        `<button type="button" class="hgz-edit-del" data-del-field title="Delete column">×</button>` +
        `</span></div>` +
        `<div class="hgz-edit-field-grid">` +
        `<label>Label <input data-f-label value="${escapeHtml(f.label)}" autocomplete="off" /></label>` +
        `<label>Type <select data-f-type${isExisting ? ' disabled' : ''}>${typeOpts}</select></label>` +
        `<label>Unit <input data-f-unit value="${escapeHtml(f.unit || '')}" autocomplete="off" placeholder="optional" /></label>` +
        `<label>Step <input data-f-step value="${escapeHtml(f.step || '')}" autocomplete="off" placeholder="e.g. 0.01" /></label>` +
        `<label>Input mode <select data-f-input-mode>${imOpts}</select></label>` +
        `<label data-f-options-row${f.type === 'select' ? '' : ' hidden'}>Options <input data-f-options value="${escapeHtml(optionsVal)}" autocomplete="off" placeholder="comma-separated" /></label>` +
        `</div></div>`;
    });
    list.innerHTML = html;

    list.querySelectorAll('[data-f-type]').forEach((sel) => {
      sel.addEventListener('change', () => {
        const row = sel.closest('[data-hgz-field-row]');
        const optRow = row?.querySelector('[data-f-options-row]');
        if (optRow) optRow.hidden = sel.value !== 'select';
      });
    });
    list.querySelectorAll('[data-move-up]').forEach((btn) => {
      btn.addEventListener('click', () => {
        syncDraftFromDom();
        const idx = Number(btn.closest('[data-hgz-field-row]')?.dataset.fieldIdx);
        if (idx <= 0) return;
        const tmp = editDraft.fields[idx - 1];
        editDraft.fields[idx - 1] = editDraft.fields[idx];
        editDraft.fields[idx] = tmp;
        renderEditFields();
      });
    });
    list.querySelectorAll('[data-move-down]').forEach((btn) => {
      btn.addEventListener('click', () => {
        syncDraftFromDom();
        const idx = Number(btn.closest('[data-hgz-field-row]')?.dataset.fieldIdx);
        if (idx < 0 || idx >= editDraft.fields.length - 1) return;
        const tmp = editDraft.fields[idx + 1];
        editDraft.fields[idx + 1] = editDraft.fields[idx];
        editDraft.fields[idx] = tmp;
        renderEditFields();
      });
    });
    list.querySelectorAll('[data-del-field]').forEach((btn) => {
      btn.addEventListener('click', () => {
        syncDraftFromDom();
        const idx = Number(btn.closest('[data-hgz-field-row]')?.dataset.fieldIdx);
        const field = editDraft.fields[idx];
        if (!field) return;
        if (columnHasRowData(field.key)) {
          if (!confirm(`Delete column "${field.label}" and all its row values?`)) return;
        }
        editDraft.fields.splice(idx, 1);
        renderEditFields();
      });
    });
  }

  function openEditDialog() {
    const log = activeLog();
    if (!log) {
      alert('Select a log first.');
      return;
    }
    editOriginalKeys = new Set((log.fields || []).map((f) => f.key));
    editDraft = {
      log_id: log.log_id,
      display_name: log.display_name,
      fields: (log.fields || []).map(cloneField),
    };
    const form = document.getElementById('hgzEditForm');
    form?.reset();
    form?.querySelector('[name="display_name"]').value = editDraft.display_name;
    const typeSel = form?.querySelector('[name="new_type"]');
    const optRow = form?.querySelector('[data-hgz-edit-new-options]');
    if (typeSel && optRow) optRow.hidden = typeSel.value !== 'select';
    renderEditFields();
    ui.editDlg.showModal();
  }

  function parseNewColumnForm(form) {
    const key = String(form.querySelector('[name="new_key"]')?.value || '').trim();
    if (!key || !/^[A-Za-z0-9_]+$/.test(key)) {
      throw new Error('Column key required (letters, numbers, underscore).');
    }
    if (editDraft.fields.some((f) => f.key === key)) {
      throw new Error('Column key already exists.');
    }
    const field = {
      key,
      label: String(form.querySelector('[name="new_label"]')?.value || '').trim() || key,
      type: String(form.querySelector('[name="new_type"]')?.value || 'number'),
      unit: String(form.querySelector('[name="new_unit"]')?.value || '').trim(),
      step: String(form.querySelector('[name="new_step"]')?.value || '').trim(),
      input_mode: String(form.querySelector('[name="new_input_mode"]')?.value || '').trim(),
    };
    if (field.type === 'select') {
      const raw = String(form.querySelector('[name="new_options"]')?.value || '');
      field.options = raw.split(',').map((s) => s.trim()).filter(Boolean);
    }
    if (!field.unit) delete field.unit;
    if (!field.step) delete field.step;
    if (!field.input_mode) delete field.input_mode;
    return field;
  }

  function wireEditForm() {
    const form = document.getElementById('hgzEditForm');
    if (!form || form.dataset.wired) return;
    form.dataset.wired = '1';

    const typeSel = form.querySelector('[name="new_type"]');
    const optRow = form.querySelector('[data-hgz-edit-new-options]');
    typeSel?.addEventListener('change', () => {
      if (optRow) optRow.hidden = typeSel.value !== 'select';
    });

    form.querySelector('[data-hgz-edit-add-col]')?.addEventListener('click', () => {
      try {
        syncDraftFromDom();
        const field = parseNewColumnForm(form);
        editDraft.fields.push(field);
        form.querySelector('[name="new_key"]').value = '';
        form.querySelector('[name="new_label"]').value = '';
        form.querySelector('[name="new_unit"]').value = '';
        form.querySelector('[name="new_step"]').value = '';
        form.querySelector('[name="new_options"]').value = '';
        renderEditFields();
      } catch (e) {
        alert(e.message);
      }
    });

    form.querySelector('[data-hgz-delete-log]')?.addEventListener('click', async () => {
      const log = activeLog();
      if (!log || !editDraft) return;
      if (!confirm(`Delete log "${log.display_name}" and all its rows?`)) return;
      try {
        await api('DELETE', `/logs/${encodeURIComponent(log.log_id)}`);
        data.logs = data.logs.filter((l) => l.log_id !== log.log_id);
        data.activeLogId = data.logs[0]?.log_id || null;
        data.rows = [];
        data.prefill.clear();
        editDraft = null;
        ui.editDlg.close();
        renderTabs();
        renderTable();
      } catch (e) {
        alert('Delete log failed: ' + e.message);
      }
    });

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!editDraft) return;
      syncDraftFromDom();
      const body = {
        display_name: editDraft.display_name,
        fields: editDraft.fields.map(cloneField),
      };
      try {
        const meta = await api('PUT', `/logs/${encodeURIComponent(editDraft.log_id)}`, body);
        syncLogMeta(meta);
        editDraft = null;
        ui.editDlg.close();
        await loadRows();
        renderTabs();
        renderTable();
      } catch (e) {
        alert('Save failed: ' + e.message);
      }
    });
  }

  function openToTemplateDialog() {
    const log = activeLog();
    if (!log) {
      alert('Select a log first.');
      return;
    }
    const form = document.getElementById('hgzToTmplForm');
    if (!form) return;
    form.reset();
    form.querySelector('[name="id"]').value = log.template_id || log.log_id || '';
    form.querySelector('[name="name"]').value = log.display_name || '';
    ui.toTmplDlg.showModal();
  }

  function wireToTemplateForm() {
    const form = document.getElementById('hgzToTmplForm');
    if (!form || form.dataset.wired) return;
    form.dataset.wired = '1';
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const log = activeLog();
      if (!log) return;
      const fd = new FormData(form);
      const body = {
        id: String(fd.get('id') || '').trim(),
        name: String(fd.get('name') || '').trim(),
        replace: fd.get('replace') === 'on',
      };
      try {
        await api('POST', `/logs/${encodeURIComponent(log.log_id)}/to-template`, body);
        await loadAll();
        ui.toTmplDlg.close();
        alert('Template saved.');
      } catch (e) {
        alert('Save template failed: ' + e.message);
      }
    });
  }

  async function show(rootEl) {
    mount(rootEl);
    try {
      await loadAll();
      await loadRows();
      renderTabs();
      renderTable();
    } catch (e) {
      console.error('howgozit load', e);
      const body = ui.root && ui.root.querySelector('[data-hgz-body]');
      if (body) {
        body.innerHTML = `<tr><td class="hgz-empty" colspan="99">Load error: ${escapeHtml(e.message)}</td></tr>`;
      }
    }
  }

  return { show, mount };
})();
