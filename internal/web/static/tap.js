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
  // options.stableKey — match dataset[stableKey] when live DOM re-renders replace nodes
  // options.slop — max pointer movement (px) for a tap; default 12 when stableKey set
  function bindPress(root, selector, handler, options) {
    if (!root) return;
    const stableKey = options?.stableKey || '';
    const slop = options?.slop ?? (stableKey ? 12 : 0);
    let downEl = null;
    let downKey = null;
    let downX = 0;
    let downY = 0;

    const clear = () => {
      downEl = null;
      downKey = null;
    };

    root.addEventListener('pointerdown', (ev) => {
      downEl = ev.target.closest(selector);
      if (!downEl) return;
      downKey = stableKey ? downEl.dataset[stableKey] : null;
      downX = ev.clientX;
      downY = ev.clientY;
    });

    root.addEventListener('pointerup', (ev) => {
      if (!downEl) return;
      if (slop > 0) {
        const dx = ev.clientX - downX;
        const dy = ev.clientY - downY;
        if (dx * dx + dy * dy > slop * slop) {
          clear();
          return;
        }
      }
      const upEl = ev.target.closest(selector);
      let match = false;
      if (stableKey && downKey != null) {
        const upKey = upEl?.dataset?.[stableKey];
        match = upKey === downKey || (!upEl && slop > 0);
      } else {
        match = upEl === downEl;
      }
      if (!match) {
        clear();
        return;
      }
      if (ev.cancelable) ev.preventDefault();
      handler(ev, upEl || downEl);
      clear();
    });

    root.addEventListener('pointercancel', clear);
  }

  function isFormControl(el) {
    return (
      el instanceof HTMLInputElement ||
      el instanceof HTMLTextAreaElement ||
      el instanceof HTMLSelectElement
    );
  }

  /** Visible viewport band (shrinks when the soft keyboard is open on mobile). */
  function visibleViewportBounds(marginPx) {
    const margin = marginPx ?? 12;
    const vv = window.visualViewport;
    if (vv) {
      return {
        top: vv.offsetTop + margin,
        bottom: vv.offsetTop + vv.height - margin,
      };
    }
    return { top: margin, bottom: window.innerHeight - margin };
  }

  function isRectInBounds(rect, bounds) {
    return rect.top >= bounds.top && rect.bottom <= bounds.bottom;
  }

  /** Keep a focused control above the soft keyboard by scrolling scrollable ancestors. */
  function scrollFormControlIntoView(inp) {
    if (!inp?.isConnected || !isFormControl(inp)) return;

    const bounds = visibleViewportBounds(12);
    let rect = inp.getBoundingClientRect();
    if (isRectInBounds(rect, bounds)) return;

    let el = inp.parentElement;
    while (el && el !== document.documentElement) {
      const style = getComputedStyle(el);
      const oy = style.overflowY;
      if (
        (oy === 'auto' || oy === 'scroll' || oy === 'overlay') &&
        el.scrollHeight > el.clientHeight + 1
      ) {
        rect = inp.getBoundingClientRect();
        if (rect.bottom > bounds.bottom) {
          el.scrollTop += rect.bottom - bounds.bottom + 8;
        } else if (rect.top < bounds.top) {
          el.scrollTop -= bounds.top - rect.top + 8;
        }
        rect = inp.getBoundingClientRect();
        if (isRectInBounds(rect, bounds)) return;
      }
      el = el.parentElement;
    }

    try {
      inp.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
    } catch {
      inp.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
  }

  const keyboardScrollTimers = new WeakMap();

  function scheduleKeyboardScroll(inp) {
    if (!inp) return;
    const prev = keyboardScrollTimers.get(inp);
    if (prev) prev.forEach(clearTimeout);
    const ids = [0, 80, 200, 450, 750].map((ms) =>
      window.setTimeout(() => {
        if (document.activeElement === inp) scrollFormControlIntoView(inp);
      }, ms)
    );
    keyboardScrollTimers.set(inp, ids);
  }

  let keyboardScrollWired = false;

  function wireKeyboardScroll() {
    if (keyboardScrollWired) return;
    keyboardScrollWired = true;

    const onViewportChange = () => {
      const el = document.activeElement;
      if (isFormControl(el)) scrollFormControlIntoView(el);
    };

    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', onViewportChange);
      window.visualViewport.addEventListener('scroll', onViewportChange);
    }

    document.addEventListener(
      'focusin',
      (ev) => {
        if (!isFormControl(ev.target)) return;
        scheduleKeyboardScroll(ev.target);
      },
      true
    );
  }

  // Blur-then-focus so iOS WebKit picks up inputmode/type when switching fields.
  function focusFormControl(inp) {
    if (!inp || inp.disabled) return;
    if (document.activeElement === inp) return;

    const active = document.activeElement;
    const switching = isFormControl(active) && active !== inp;

    const apply = () => {
      if (inp instanceof HTMLInputElement && inp.hasAttribute('inputmode')) {
        inp.inputMode = inp.getAttribute('inputmode');
      }
      inp.focus({ preventScroll: true });
      scheduleKeyboardScroll(inp);
    };

    if (switching) {
      active.blur();
      window.setTimeout(apply, 10);
    } else {
      apply();
    }
  }

  // Focus a nested control on pointerdown — iOS WebKit often drops the delayed
  // click on busy main thread; use on dialog form fields (not just buttons).
  function bindFieldFocus(root, fieldSelector, inputSelector) {
    if (!root || root.dataset.kfFocusWired === '1') return;
    root.dataset.kfFocusWired = '1';
    root.addEventListener(
      'pointerdown',
      (ev) => {
        const field = ev.target.closest(fieldSelector);
        if (!field) return;
        if (field.classList.contains('cfgCheckbox')) return;
        if (ev.target.closest('label.cfgCheckbox')) return;
        if (ev.target.closest('button, a, [role="button"]')) return;
        const inp = field.querySelector(inputSelector);
        if (!inp || inp.disabled || inp.type === 'checkbox') return;
        if (ev.target.closest(inputSelector) === inp && document.activeElement === inp) return;
        if (inp.tagName !== 'SELECT' && ev.cancelable) ev.preventDefault();
        focusFormControl(inp);
      },
      { passive: false }
    );
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
    // Block the delayed native click (Mac/desktop) after we toggle on pointerup.
    document.addEventListener(
      'click',
      (ev) => {
        if (ev.target.closest('label.cfgCheckbox')) {
          ev.preventDefault();
        }
      },
      true
    );
    bindPress(document, 'label.cfgCheckbox', (ev, label) => {
      const inp = label.querySelector('input[type="checkbox"]');
      if (!inp || inp.disabled) return;
      if (ev.cancelable) ev.preventDefault();
      inp.checked = !inp.checked;
      inp.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  return { bindTap, bindPress, bindFieldFocus, focusFormControl, wireDialogCloses, wireCheckboxLabels, wireKeyboardScroll };
})();

KFTap.wireKeyboardScroll();
