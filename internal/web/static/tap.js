// Reliable tap/click on iOS WebKit — use instead of click when the main thread
// may be busy (live sensor DOM updates). Loaded before app.js / howgozit.js.
//
// Buttons / nav:     KFTap.bindPress(root, selector, handler)
// Single controls:   KFTap.bindTap(el, handler)
// Dialog inputs:     KFTap.bindFieldFocus(container, fieldSelector, inputSelector)
// Cancel buttons:    KFTap.wireDialogCloses() once at init
// Checkboxes:        KFTap.wireCheckboxLabels() once at init
const KFTap = (function () {
  const DEBOUNCE_MS = 350;

  function bindTap(el, handler) {
    if (!el) return;
    let lastFire = 0;
    const fire = (ev) => {
      if (ev.type === 'pointerup' && ev.pointerType === 'mouse' && ev.button !== 0) return;
      const now = performance.now();
      if (now - lastFire < DEBOUNCE_MS) return;
      lastFire = now;
      if (ev.cancelable) ev.preventDefault();
      handler(ev);
    };
    el.addEventListener('pointerup', fire);
  }

  // pointerdown/up on the same matching element (ignores scroll drags).
  function bindPress(root, selector, handler) {
    if (!root) return;
    let downEl = null;
    root.addEventListener('pointerdown', (ev) => {
      downEl = ev.target.closest(selector);
    });
    root.addEventListener('pointerup', (ev) => {
      const upEl = ev.target.closest(selector);
      if (!upEl || upEl !== downEl) return;
      if (ev.cancelable) ev.preventDefault();
      handler(ev, upEl);
      downEl = null;
    });
  }

  // Focus a nested control on pointerdown — iOS WebKit often drops the delayed
  // click on busy main thread; use on dialog form fields (not just buttons).
  function bindFieldFocus(root, fieldSelector, inputSelector) {
    if (!root || root.dataset.kfFocusWired === '1') return;
    root.dataset.kfFocusWired = '1';
    root.addEventListener('pointerdown', (ev) => {
      const field = ev.target.closest(fieldSelector);
      if (!field) return;
      const inp = field.querySelector(inputSelector);
      if (inp && document.activeElement !== inp) inp.focus();
    });
  }

  let dialogCloseWired = false;
  let checkboxWired = false;

  function wireDialogCloses() {
    if (dialogCloseWired) return;
    dialogCloseWired = true;
    bindPress(document, 'dialog button[value="cancel"]', (ev, btn) => {
      const dlg = btn.closest('dialog');
      if (!dlg || !dlg.open) return;
      if (ev.cancelable) ev.preventDefault();
      dlg.close();
    });
  }

  function wireCheckboxLabels() {
    if (checkboxWired) return;
    checkboxWired = true;
    bindPress(document, 'label.cfgCheckbox', (ev, label) => {
      const inp = label.querySelector('input[type="checkbox"]');
      if (!inp || inp.disabled) return;
      if (ev.cancelable) ev.preventDefault();
      inp.checked = !inp.checked;
      inp.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  return { bindTap, bindPress, bindFieldFocus, wireDialogCloses, wireCheckboxLabels };
})();
