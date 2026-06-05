// Howgozit — in-flight manual log tab.
var KFHowgozit = (function () {
  const PATCH_DEBOUNCE_MS = 400;

  const DEFAULT_NUMBER_STEP = {
    baro_inhg: '0.01',
    mp_inhg: '0.01',
  };

  const ui = {
    root: null,
    tabsEl: null,
    scrollEl: null,
    bodyEl: null,
    addLogDlg: null,
    editDlg: null,
    toTmplDlg: null,
    rowDlg: null,
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
  let rowEditId = null;

  let patchTimers = new Map();
  let mounted = false;

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
    for (const dlg of rootEl.querySelectorAll(
      'dialog[data-hgz-add-log-dlg], dialog[data-hgz-edit-dlg], dialog[data-hgz-to-tmpl-dlg], dialog[data-hgz-row-dlg]'
    )) {
      dlg.remove();
    }
    if (!rootEl.querySelector('[data-hgz-root]')) {
      rootEl.innerHTML =
        '<div class="hgz-root" data-hgz-root>' +
        '<div class="hgz-toolbar">' +
        '<div class="hgz-tabs" data-hgz-tabs role="tablist"></div>' +
        '<button type="button" class="hgz-iconBtn" data-hgz-add-log title="Add log">+ Log</button>' +
        '<button type="button" class="hgz-iconBtn" data-hgz-edit title="Edit log">Edit</button>' +
        '<button type="button" class="hgz-iconBtn" data-hgz-to-tmpl title="Save log as template">→ Template</button>' +
        '</div>' +
        '<div class="hgz-scroll" data-hgz-scroll>' +
        '<div class="hgz-row-list" data-hgz-body></div>' +
        '<button type="button" class="hgz-add-entry" data-hgz-add-row>+ Add entry</button>' +
        '</div></div>';
    }
    bindShellRefs(rootEl);
    wireHowgozitTaps();
    wireEditForm();
    wireToTemplateForm();
    wireRowDialog();
    wireHowgozitDialogFields();
    mounted = true;
  }

  function wireHowgozitDialogFields() {
    if (document.documentElement.dataset.hgzDlgFocusWired === '1') return;
    document.documentElement.dataset.hgzDlgFocusWired = '1';
    const editFields = document.querySelector('[data-hgz-edit-fields]');
    const editAdd = document.querySelector('[data-hgz-edit-add]');
    KFTap.bindFieldFocus(editFields, '[data-hgz-field-row]', 'input, select');
    KFTap.bindFieldFocus(editAdd, 'label', 'input, select');
    KFTap.bindFieldFocus(document.getElementById('hgzEditForm'), 'label', 'input, select, textarea');
    KFTap.bindFieldFocus(document.getElementById('hgzToTmplForm'), 'label', 'input');
  }

  function wireHowgozitTaps() {
    if (document.documentElement.dataset.hgzTapWired === '1') return;
    document.documentElement.dataset.hgzTapWired = '1';

    KFTap.bindPress(document, '[data-hgz-add-log]', () => openAddLogDialog());
    KFTap.bindPress(document, '[data-hgz-edit]', () => openEditDialog());
    KFTap.bindPress(document, '[data-hgz-to-tmpl]', () => openToTemplateDialog());

    KFTap.bindPress(document, '.hgz-tab', async (ev, btn) => {
      data.activeLogId = btn.dataset.logId;
      data.prefill.clear();
      renderTabs();
      await loadRows();
      renderRows();
    });

    KFTap.bindPress(document, '[data-hgz-add-row]', () => addRow());
    KFTap.bindPress(document, '.hgz-row-card', (ev, card) => {
      if (ev.target.closest('[data-del-row], .hgz-delBtn')) return;
      openRowDialog(Number(card.dataset.rowid));
    });
    KFTap.bindPress(document, '[data-del-row]', (ev, btn) => deleteRow(Number(btn.dataset.delRow)));

    KFTap.bindPress(document, '[data-pick-new]', () => {
      ui.addLogDlg?.close();
      createLog({ new: true, name: 'New log' });
    });
    KFTap.bindPress(document, '[data-pick-tmpl]', (ev, btn) => {
      ui.addLogDlg?.close();
      createLog({ template_id: btn.dataset.pickTmpl });
    });

    KFTap.bindPress(document, '[data-move-up]', (ev, btn) => {
      syncDraftFromDom();
      const idx = Number(btn.closest('[data-hgz-field-row]')?.dataset.fieldIdx);
      if (idx <= 0) return;
      const tmp = editDraft.fields[idx - 1];
      editDraft.fields[idx - 1] = editDraft.fields[idx];
      editDraft.fields[idx] = tmp;
      renderEditFields();
    });
    KFTap.bindPress(document, '[data-move-down]', (ev, btn) => {
      syncDraftFromDom();
      const idx = Number(btn.closest('[data-hgz-field-row]')?.dataset.fieldIdx);
      if (idx < 0 || idx >= editDraft.fields.length - 1) return;
      const tmp = editDraft.fields[idx + 1];
      editDraft.fields[idx + 1] = editDraft.fields[idx];
      editDraft.fields[idx] = tmp;
      renderEditFields();
    });
    KFTap.bindPress(document, '[data-del-field]', (ev, btn) => {
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

    KFTap.bindPress(document, '[data-hgz-edit-add-col]', () => {
      try {
        syncDraftFromDom();
        const form = document.getElementById('hgzEditForm');
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

    KFTap.bindPress(document, '[data-hgz-delete-log]', async () => {
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
        ui.editDlg?.close();
        renderTabs();
        renderRows();
      } catch (e) {
        alert('Delete log failed: ' + e.message);
      }
    });
  }

  function bindShellRefs(rootEl) {
    ui.root = rootEl.querySelector('[data-hgz-root]') || rootEl;
    ui.tabsEl = ui.root.querySelector('[data-hgz-tabs]');
    ui.scrollEl = ui.root.querySelector('[data-hgz-scroll]');
    ui.bodyEl = ui.root.querySelector('[data-hgz-body]');
    ui.addLogDlg = document.getElementById('hgzAddLogDlg');
    ui.editDlg = document.getElementById('hgzEditDlg');
    ui.toTmplDlg = document.getElementById('hgzToTmplDlg');
    ui.rowDlg = document.getElementById('hgzRowDlg');
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

  function fieldLabel(f) {
    return f.unit ? `${f.label} (${f.unit})` : f.label;
  }

  function fieldInputHtml(rowid, field, val, prefilled) {
    const pf = prefilled ? ' hgz-prefill' : '';
    const rid = ` data-rowid="${rowid}"`;
    const df = ` data-field="${escapeHtml(field.key)}"`;
    const ph = ` placeholder="Enter ${escapeHtml(field.label)}"`;
    const kind = cellInputType(field);
    if (kind === 'select') {
      let opts = '<option value="">— select —</option>';
      for (const o of field.options || []) {
        const sel = String(val) === String(o) ? ' selected' : '';
        opts += `<option value="${escapeHtml(o)}"${sel}>${escapeHtml(o)}</option>`;
      }
      return `<select class="hgz-row-input${pf}"${rid}${df}>${opts}</select>`;
    }
    if (kind === 'number') {
      return `<input class="hgz-row-input${pf}"${rid}${df} value="${escapeHtml(val)}"${numberFieldAttrs(field)}${ph} />`;
    }
    return `<input type="text" class="hgz-row-input${pf}"${rid}${df} value="${escapeHtml(val)}" inputmode="text"${ph} />`;
  }

  function formatFieldDisplay(val, field) {
    if (val == null || String(val).trim() === '') return null;
    const s = String(val);
    return field.unit ? `${s} ${field.unit}` : s;
  }

  function renderRows() {
    const log = activeLog();
    const bodyEl = ui.bodyEl;
    const addBtn = ui.root?.querySelector('[data-hgz-add-row]');
    if (!bodyEl) return;

    if (!log) {
      bodyEl.innerHTML = '<p class="hgz-empty">No logs — tap + Log to start.</p>';
      if (addBtn) addBtn.hidden = true;
      return;
    }
    if (addBtn) addBtn.hidden = false;

    const fields = fieldsForLog(log);
    if (data.rows.length === 0) {
      bodyEl.innerHTML = '<p class="hgz-empty">No entries yet — tap <strong>+ Add entry</strong> below.</p>';
      return;
    }

    let html = '';
    for (const row of data.rows) {
      const prefilled = data.prefill.get(row.rowid) || new Set();
      html += `<div class="hgz-row-card" data-rowid="${row.rowid}" role="button" tabindex="0">`;
      html += `<div class="hgz-row-card-head">`;
      html += `<span class="hgz-row-card-time">${escapeHtml(formatTime(row.ts_ns))}</span>`;
      html += `<button type="button" class="hgz-delBtn" data-del-row="${row.rowid}" title="Delete entry">×</button>`;
      html += `</div><div class="hgz-row-card-body">`;
      for (const f of fields) {
        const val = row.values?.[f.key];
        const display = formatFieldDisplay(val, f);
        const emptyCls = display ? '' : ' hgz-empty-val';
        const text = display || '—';
        html += `<div class="hgz-field-line"><span class="lbl">${escapeHtml(f.label)}</span>`;
        html += `<span class="val${emptyCls}">${escapeHtml(text)}</span></div>`;
      }
      html += `</div></div>`;
    }
    bodyEl.innerHTML = html;
  }

  function wireRowInputs(container) {
    for (const inp of container.querySelectorAll('.hgz-row-input')) {
      const handler = () => onCellEdit(inp);
      inp.addEventListener('input', handler);
      inp.addEventListener('change', handler);
      inp.addEventListener('focus', () => {
        if (inp.classList.contains('hgz-prefill')) {
          inp.classList.remove('hgz-prefill');
          const rowid = Number(inp.dataset.rowid);
          clearPrefill(rowid, inp.dataset.field);
        }
      });
    }
  }

  function openRowDialog(rowid) {
    if (!ui.rowDlg) ui.rowDlg = document.getElementById('hgzRowDlg');
    const log = activeLog();
    const rid = Number(rowid);
    const row = data.rows.find((r) => Number(r.rowid) === rid);
    if (!log || !row || !ui.rowDlg) return;
    rowEditId = rid;
    const fields = fieldsForLog(log);
    const prefilled = data.prefill.get(rowid) || new Set();
    const titleEl = ui.rowDlg.querySelector('[data-hgz-row-title]');
    if (titleEl) titleEl.textContent = log.display_name || 'Entry';

    const fieldsEl = ui.rowDlg.querySelector('[data-hgz-row-fields]');
    if (!fieldsEl) return;

    let html = '';
    const timePrefill = prefilled.has('__time__');
    html +=
      `<label class="hgz-row-field">` +
      `<span class="hgz-row-label">Time (HH:MM)</span>` +
      `<input type="text" class="hgz-row-input hgz-row-input-time${timePrefill ? ' hgz-prefill' : ''}" data-rowid="${rowid}" data-field="__time__" value="${escapeHtml(formatTime(row.ts_ns))}" inputmode="numeric" placeholder="21:15" />` +
      `</label>`;

    if (fields.length === 0) {
      html += '<p class="hgz-empty hgz-row-no-cols">No columns yet — close and tap <strong>Edit</strong> to add fields.</p>';
    }

    for (const f of fields) {
      const val = row.values?.[f.key] ?? '';
      const isPrefill = prefilled.has(f.key);
      html +=
        `<label class="hgz-row-field">` +
        `<span class="hgz-row-label">${escapeHtml(fieldLabel(f))}</span>` +
        fieldInputHtml(rowid, f, val, isPrefill) +
        `</label>`;
    }
    fieldsEl.innerHTML = html;
    wireRowInputs(fieldsEl);
    ui.rowDlg.showModal();
    requestAnimationFrame(() => {
      const firstEmpty = fieldsEl.querySelector('.hgz-row-input:not(.hgz-row-input-time)');
      const focusEl = firstEmpty && !firstEmpty.value ? firstEmpty : fieldsEl.querySelector('.hgz-row-input');
      focusEl?.focus({ preventScroll: true });
    });
  }

  function closeRowDialog() {
    if (!ui.rowDlg?.open) return;
    for (const inp of ui.rowDlg.querySelectorAll('.hgz-row-input')) {
      flushPatch(inp, true);
    }
    ui.rowDlg.close();
  }

  function wireRowDialog() {
    if (!ui.rowDlg || ui.rowDlg.dataset.wired) return;
    ui.rowDlg.dataset.wired = '1';
    const fieldsEl = ui.rowDlg.querySelector('[data-hgz-row-fields]');
    KFTap.bindFieldFocus(fieldsEl, '.hgz-row-field', '.hgz-row-input');
    KFTap.bindPress(ui.rowDlg, '[data-hgz-row-done]', () => closeRowDialog());
    KFTap.bindPress(ui.rowDlg, '[data-hgz-row-del]', async () => {
      if (rowEditId == null) return;
      const id = rowEditId;
      closeRowDialog();
      await deleteRow(id);
    });
    ui.rowDlg.addEventListener('cancel', (ev) => {
      ev.preventDefault();
      closeRowDialog();
    });
    ui.rowDlg.addEventListener('close', () => {
      rowEditId = null;
      renderRows();
    });
  }

  function clearPrefill(rowid, field) {
    const set = data.prefill.get(rowid);
    if (!set) return;
    set.delete(field);
    if (set.size === 0) data.prefill.delete(rowid);
  }

  function onCellEdit(inp) {
    const rowid = Number(inp.dataset.rowid);
    if (!rowid) return;
    const field = inp.dataset.field;
    inp.classList.remove('hgz-prefill');
    clearPrefill(rowid, field);
    flushPatch(inp, false);
  }

  function flushPatch(inp, immediate) {
    const rowid = Number(inp.dataset.rowid);
    if (!rowid) return;
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
      renderRows();
      ui.scrollEl?.scrollTo({ top: ui.scrollEl.scrollHeight, behavior: 'smooth' });
      requestAnimationFrame(() => openRowDialog(row.rowid));
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
      renderRows();
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
      renderRows();
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
    if (form) {
      form.reset();
      const nameEl = form.querySelector('[name="display_name"]');
      if (nameEl) nameEl.value = editDraft.display_name;
    }
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
        renderRows();
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
      renderRows();
    } catch (e) {
      console.error('howgozit load', e);
      const body = ui.root && ui.root.querySelector('[data-hgz-body]');
      if (body) {
        body.innerHTML = `<p class="hgz-empty">Load error: ${escapeHtml(e.message)}</p>`;
      }
    }
  }

  return { show, mount };
})();
window.KFHowgozit = KFHowgozit;
