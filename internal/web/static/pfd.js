// PFD (primary-flight-display) hero for the /instruments tab.
//
// Glass-cockpit layout for an in-flight glance: a big magnetic-heading
// number on top, an SVG attitude indicator in the centre, and IAS / ALT
// "tapes" on the flanks. The number the eye lands on is always a large,
// static centre box; the tape context labels around it are dim and only
// relabel when the rounded value changes (a dead-band), so nothing slews
// or jitters in turbulence. The DB still records every raw sample — all
// smoothing here is presentation-only.
//
// Static DOM is built once (mount); each WS tick only mutates text and the
// horizon transform. Sources: ahrs(roll,pitch), compass(heading_mag_deg /
// heading_sensor_deg + align_active), airspeed(ias_kt), press_alt
// (indicated_alt_ft / pressure_alt_ft), gps(vs). Each sub-panel carries a
// data-device attribute so the shared markStaleness pass dims it when its
// source goes quiet. Absent/NaN sources render "—", never a bogus 0.
//
// SAFETY: the attitude indicator and pre-align heading are TREND/awareness
// aids stamped at compute time (see docs/timestamps.md), NOT certified
// instruments — labelled as such; never a primary attitude reference in IMC.
const KFPFD = (function () {
  const MPS_TO_FPM = 196.8503937;
  const PITCH_PX_PER_DEG = 1.4; // horizon vertical travel
  const ROLL_EMA = 0.35;        // attitude smoothing (presentation only)

  // Smoothed/last-shown state across ticks (single PFD instance). The
  // *Txt fields cache the last rendered string so every readout follows
  // the same dead-band discipline (only touch the DOM on a real change).
  const s = { roll: null, pitch: null, hdg: null, ias: null, alt: null, rpTxt: null, flagTxt: null, vsiTxt: null };

  function num(sample, ch) {
    const v = sample?.values?.[ch];
    return v != null && Number.isFinite(Number(v)) ? Number(v) : null;
  }

  function ema(prev, next, a) {
    if (next == null) return prev;
    if (prev == null) return next;
    return prev + a * (next - prev);
  }

  function wrap360(d) {
    return ((Math.round(d) % 360) + 360) % 360;
  }

  function pad3(d) {
    return String(d).padStart(3, '0');
  }

  function mount(el) {
    if (el.querySelector('[data-pfd-root]')) return;
    el.innerHTML =
      `<div data-pfd-root class="pfd">` +
        `<div class="pfd-hdg" data-device="compass">` +
          `<div class="pfd-lbl">MAG HDG</div>` +
          `<div class="pfd-hdg-val" data-pfd-hdg>—</div>` +
          `<div class="pfd-hdg-flag" data-pfd-hdg-flag></div>` +
        `</div>` +
        `<div class="pfd-mid">` +
          // IAS tape
          `<div class="pfd-tape" data-device="airspeed">` +
            `<div class="pfd-tape-lbl">IAS kt</div>` +
            `<div class="pfd-tick" data-pfd-ias-hi>—</div>` +
            `<div class="pfd-box" data-pfd-ias>—</div>` +
            `<div class="pfd-tick" data-pfd-ias-lo>—</div>` +
          `</div>` +
          // Attitude indicator
          `<div class="pfd-ai" data-device="ahrs">` +
            `<svg viewBox="0 0 100 100" class="pfd-ai-svg" aria-hidden="true">` +
              `<defs><clipPath id="pfdAiClip"><rect x="4" y="4" width="92" height="92" rx="8"/></clipPath></defs>` +
              `<g clip-path="url(#pfdAiClip)">` +
                `<g data-pfd-horizon>` +
                  `<rect class="pfd-sky" x="-120" y="-160" width="340" height="210"/>` +
                  `<rect class="pfd-ground" x="-120" y="50" width="340" height="210"/>` +
                  `<line class="pfd-horizon-line" x1="-120" y1="50" x2="220" y2="50"/>` +
                  `<line class="pfd-ladder" x1="35" y1="36" x2="65" y2="36"/>` +
                  `<line class="pfd-ladder" x1="40" y1="43" x2="60" y2="43"/>` +
                  `<line class="pfd-ladder" x1="40" y1="57" x2="60" y2="57"/>` +
                  `<line class="pfd-ladder" x1="35" y1="64" x2="65" y2="64"/>` +
                `</g>` +
                // Static bank scale (0 / ±30 / ±60) and a fixed sky pointer
                // at 0. These don't move; bank is read from how far the
                // tilting horizon line rotates away from this fixed frame.
                `<line class="pfd-bank-tick" x1="50" y1="8" x2="50" y2="12"/>` +
                `<line class="pfd-bank-tick" x1="71" y1="13.6" x2="69" y2="17.1"/>` +
                `<line class="pfd-bank-tick" x1="29" y1="13.6" x2="31" y2="17.1"/>` +
                `<line class="pfd-bank-tick" x1="86.4" y1="29" x2="82.9" y2="31"/>` +
                `<line class="pfd-bank-tick" x1="13.6" y1="29" x2="17.1" y2="31"/>` +
                `<path class="pfd-roll-ptr" d="M50 12 l-2.5 4 h5 z"/>` +
                // fixed aircraft reference symbol (does not move)
                `<line class="pfd-ac" x1="30" y1="50" x2="43" y2="50"/>` +
                `<line class="pfd-ac" x1="57" y1="50" x2="70" y2="50"/>` +
                `<circle class="pfd-ac" cx="50" cy="50" r="1.6"/>` +
              `</g>` +
            `</svg>` +
            `<div class="pfd-rp" data-pfd-rp>—</div>` +
          `</div>` +
          // ALT tape
          `<div class="pfd-tape" data-device="press_alt">` +
            `<div class="pfd-tape-lbl">ALT ft</div>` +
            `<div class="pfd-tick" data-pfd-alt-hi>—</div>` +
            `<div class="pfd-box" data-pfd-alt>—</div>` +
            `<div class="pfd-tick" data-pfd-alt-lo>—</div>` +
          `</div>` +
        `</div>` +
        `<div class="pfd-foot">` +
          `<span class="pfd-vsi" data-device="gps"><span class="pfd-lbl">VSI</span> <span data-pfd-vsi>—</span> <span class="dim">fpm (GPS)</span></span>` +
          `<span class="pfd-caption dim">AHRS trend — not for primary nav</span>` +
        `</div>` +
      `</div>`;
  }

  // renderPanel(el, {ahrs, compass, airspeed, pressAlt, gps})
  function renderPanel(el, src) {
    mount(el);
    const root = el.querySelector('[data-pfd-root]');
    if (!root) return;
    src = src || {};

    // ---- Heading (align-gated) ----
    const cv = src.compass?.values ?? {};
    const aligned = cv.align_active === 1;
    let hdg = aligned ? num(src.compass, 'heading_mag_deg') : num(src.compass, 'heading_sensor_deg');
    const hdgEl = root.querySelector('[data-pfd-hdg]');
    const flagEl = root.querySelector('[data-pfd-hdg-flag]');
    if (hdg == null) {
      if (s.hdg !== '—') { hdgEl.textContent = '—'; s.hdg = '—'; }
    } else {
      const w = wrap360(hdg);
      if (s.hdg !== w) { hdgEl.textContent = pad3(w); s.hdg = w; } // dead-band: whole deg
    }
    const flagTxt = (hdg != null && !aligned) ? 'sensor (not aligned)' : '';
    if (flagEl && s.flagTxt !== flagTxt) {
      flagEl.textContent = flagTxt;
      s.flagTxt = flagTxt;
    }

    // ---- IAS tape ----
    setTape(root, 'ias', num(src.airspeed, 'ias_kt'), 1, 10, (v) => String(v));

    // ---- ALT tape (indicated preferred, pressure-alt fallback) ----
    let alt = num(src.pressAlt, 'indicated_alt_ft');
    if (alt == null) alt = num(src.pressAlt, 'pressure_alt_ft');
    setTape(root, 'alt', alt, 10, 100, (v) => v.toLocaleString('en-US'));

    // ---- Attitude (smoothed, presentation only) ----
    const roll = num(src.ahrs, 'roll');
    const pitch = num(src.ahrs, 'pitch');
    s.roll = ema(s.roll, roll, ROLL_EMA);
    s.pitch = ema(s.pitch, pitch, ROLL_EMA);
    const hz = root.querySelector('[data-pfd-horizon]');
    const rpEl = root.querySelector('[data-pfd-rp]');
    if (hz) {
      if (s.roll == null || s.pitch == null) {
        hz.setAttribute('transform', 'translate(0 0) rotate(0 50 50)');
      } else {
        const dy = Math.max(-34, Math.min(34, s.pitch * PITCH_PX_PER_DEG));
        hz.setAttribute('transform', `translate(0 ${dy.toFixed(1)}) rotate(${(-s.roll).toFixed(1)} 50 50)`);
      }
    }
    // Drive the readout from the SAME smoothed values as the horizon (so
    // the number and the picture agree), with a whole-degree dead-band so
    // the digits don't buzz in chop.
    let rpTxt;
    if (s.roll == null || s.pitch == null) {
      rpTxt = '—';
    } else {
      const r = Math.round(s.roll);
      const p = Math.round(s.pitch);
      const rTxt = r === 0 ? 'WL' : Math.abs(r) + (r > 0 ? 'R' : 'L');
      rpTxt = `${rTxt}  ${p >= 0 ? '+' : ''}${p}°`;
    }
    if (rpEl && s.rpTxt !== rpTxt) {
      rpEl.textContent = rpTxt;
      s.rpTxt = rpTxt;
    }

    // ---- VSI (GPS-derived trend) ----
    const vsiEl = root.querySelector('[data-pfd-vsi]');
    const vs = num(src.gps, 'vs');
    let vsiTxt;
    if (vs == null) {
      vsiTxt = '—';
    } else {
      const fpm = Math.round(vs * MPS_TO_FPM / 10) * 10;
      vsiTxt = (fpm > 0 ? '+' : '') + fpm.toLocaleString('en-US');
    }
    if (vsiEl && s.vsiTxt !== vsiTxt) {
      vsiEl.textContent = vsiTxt;
      s.vsiTxt = vsiTxt;
    }
  }

  // setTape writes the centre box + the two dim context ticks. round =
  // display granularity (kills last-digit jitter); step = context spacing.
  function setTape(root, key, value, round, step, fmt) {
    const box = root.querySelector(`[data-pfd-${key}]`);
    const hi = root.querySelector(`[data-pfd-${key}-hi]`);
    const lo = root.querySelector(`[data-pfd-${key}-lo]`);
    if (value == null) {
      if (s[key] !== '—') {
        if (box) box.textContent = '—';
        if (hi) hi.textContent = '';
        if (lo) lo.textContent = '';
        s[key] = '—';
      }
      return;
    }
    const r = Math.round(value / round) * round;
    if (s[key] === r) return; // dead-band: only redraw on a rounded change
    s[key] = r;
    if (box) box.textContent = fmt(r);
    if (hi) hi.textContent = fmt(r + step);
    if (lo) lo.textContent = fmt(Math.max(0, r - step));
  }

  return { renderPanel, mount };
})();
