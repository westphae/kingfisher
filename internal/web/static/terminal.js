(function () {
  const loginBox = document.getElementById('loginBox');
  const pubkeyUser = document.getElementById('pubkeyUser');
  const pubkeyLoginBtn = document.getElementById('pubkeyLoginBtn');
  const passwordForm = document.getElementById('passwordForm');
  const loginUser = document.getElementById('loginUser');
  const loginPass = document.getElementById('loginPass');
  const loginErr = document.getElementById('loginErr');
  const genKeyBtn = document.getElementById('genKeyBtn');
  const importKeyFile = document.getElementById('importKeyFile');
  const pubkeyLine = document.getElementById('pubkeyLine');
  const copyPubkeyBtn = document.getElementById('copyPubkeyBtn');
  const clearKeyBtn = document.getElementById('clearKeyBtn');
  const keySetup = document.getElementById('keySetup');
  const keySetupHint = document.getElementById('keySetupHint');
  const termEl = document.getElementById('terminal');
  const termUser = document.getElementById('termUser');
  const termLogout = document.getElementById('termLogout');

  let authCfg = null;
  let term = null;
  let fitAddon = null;
  let ws = null;
  let resizeObs = null;
  let resizeHandler = null;

  function showLogin() {
    loginBox.hidden = false;
    termEl.hidden = true;
    termUser.hidden = true;
    termLogout.hidden = true;
    teardownTerm();
    configureLoginUI();
  }

  function showTerminal(username) {
    loginBox.hidden = true;
    termEl.hidden = false;
    termUser.hidden = false;
    termLogout.hidden = false;
    termUser.textContent = username;
    try {
      mountTerm();
      connectWS();
    } catch (err) {
      termEl.hidden = false;
      termEl.textContent = 'Terminal init failed: ' + (err && err.message ? err.message : String(err));
    }
  }

  function setLoginError(msg) {
    if (!msg) {
      loginErr.hidden = true;
      loginErr.textContent = '';
      return;
    }
    loginErr.hidden = false;
    loginErr.textContent = msg;
  }

  async function loadAuthCfg() {
    try {
      const r = await fetch('/api/terminal/auth');
      if (!r.ok) return null;
      return await r.json();
    } catch {
      return null;
    }
  }

  async function showStoredPubkey() {
    const line = await TerminalKey.storedPublicLine();
    if (line) {
      pubkeyLine.hidden = false;
      pubkeyLine.textContent = line;
      copyPubkeyBtn.hidden = false;
      clearKeyBtn.hidden = false;
    } else {
      pubkeyLine.hidden = true;
      pubkeyLine.textContent = '';
      copyPubkeyBtn.hidden = true;
      clearKeyBtn.hidden = true;
    }
  }

  async function configureLoginUI() {
    authCfg = await loadAuthCfg();
    setLoginError('');
    const pubkey = authCfg && authCfg.pubkey_auth;
    const password = authCfg && authCfg.password_auth;
    passwordForm.hidden = !password;

    const hasKey = await TerminalKey.hasStoredKey();
    pubkeyLoginBtn.hidden = !pubkey;
    pubkeyLoginBtn.disabled = !hasKey;

    if (pubkey) {
      pubkeyUser.textContent = authCfg.user
        ? 'Shell runs as user ' + authCfg.user + '.'
        : '';
      if (keySetup) keySetup.open = false;
      if (keySetupHint) {
        keySetupHint.textContent = 'Add the public key below to terminal.authorized_keys, set terminal.user, then reload this page.';
      }
    } else {
      pubkeyUser.textContent = 'Set up a browser key below, add it to kingfisher config, then reload to enable Sign in.';
      if (keySetup) keySetup.open = true;
      if (keySetupHint) {
        keySetupHint.textContent = 'Step 1: generate or import a key here. Step 2: add the public key to terminal.authorized_keys and set terminal.user in ~/.config/kingfisher/config.json. Step 3: restart kingfisher and reload this page.';
      }
    }
    await showStoredPubkey();

    if (password) {
      loginUser.required = true;
      loginPass.required = true;
    }
  }

  async function refreshSession() {
    try {
      const r = await fetch('/api/terminal/session');
      if (!r.ok) {
        showLogin();
        return;
      }
      const j = await r.json();
      if (j.authenticated) {
        showTerminal(j.username || '');
      } else {
        showLogin();
      }
    } catch {
      showLogin();
    }
  }

  async function pubkeyLogin() {
    setLoginError('');
    try {
      const chR = await fetch('/api/terminal/challenge');
      if (chR.status === 404) {
        setLoginError('Terminal is disabled or public-key auth is not configured.');
        return;
      }
      if (chR.status === 429) {
        setLoginError('Too many login attempts. Wait a minute and try again.');
        return;
      }
      if (!chR.ok) {
        setLoginError('Could not fetch login challenge.');
        return;
      }
      const ch = await chR.json();
      const message = b64dec(ch.message_b64);
      const sig = await TerminalKey.sign(message);
      const r = await fetch('/api/terminal/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          challenge_id: ch.id,
          signature: b64enc(sig),
        }),
      });
      if (r.status === 429) {
        setLoginError('Too many login attempts. Wait a minute and try again.');
        return;
      }
      if (!r.ok) {
        setLoginError('Authentication failed. Check authorized_keys and your browser key.');
        return;
      }
      const j = await r.json();
      showTerminal(j.username || (authCfg && authCfg.user) || '');
    } catch (err) {
      setLoginError(err && err.message ? err.message : 'Could not sign in.');
    }
  }

  function b64enc(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }

  function b64dec(s) {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  pubkeyLoginBtn.addEventListener('click', () => pubkeyLogin());

  genKeyBtn.addEventListener('click', async () => {
    setLoginError('');
    try {
      const { publicLine } = await TerminalKey.generateKeyPair();
      pubkeyLine.hidden = false;
      pubkeyLine.textContent = publicLine;
      copyPubkeyBtn.hidden = false;
      clearKeyBtn.hidden = false;
      pubkeyLoginBtn.disabled = false;
    } catch (err) {
      setLoginError(err && err.message ? err.message : 'Could not generate key.');
    }
  });

  importKeyFile.addEventListener('change', async () => {
    const file = importKeyFile.files && importKeyFile.files[0];
    importKeyFile.value = '';
    if (!file) return;
    setLoginError('');
    try {
      const text = await file.text();
      const publicLine = await TerminalKey.importOpenSSHPrivateKeyFile(text);
      pubkeyLine.hidden = false;
      pubkeyLine.textContent = publicLine;
      copyPubkeyBtn.hidden = false;
      clearKeyBtn.hidden = false;
      pubkeyLoginBtn.disabled = false;
    } catch (err) {
      setLoginError(err && err.message ? err.message : 'Could not import key.');
    }
  });

  function copyText(text) {
    if (!text) return Promise.reject(new Error('nothing to copy'));
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    ta.style.left = '0';
    ta.style.top = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
    }
    if (ok) return Promise.resolve();
    if (pubkeyLine && !pubkeyLine.hidden) {
      const range = document.createRange();
      range.selectNodeContents(pubkeyLine);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      try {
        ok = document.execCommand('copy');
      } finally {
        sel.removeAllRanges();
      }
      if (ok) return Promise.resolve();
    }
    return Promise.reject(new Error('copy failed'));
  }

  copyPubkeyBtn.addEventListener('click', async () => {
    const text = pubkeyLine.textContent;
    if (!text) return;
    setLoginError('');
    try {
      await copyText(text);
    } catch {
      setLoginError('Copy failed — tap the key text above and copy manually.');
    }
  });

  clearKeyBtn.addEventListener('click', async () => {
    await TerminalKey.clearStoredKey();
    pubkeyLoginBtn.disabled = true;
    await showStoredPubkey();
  });

  passwordForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    setLoginError('');
    try {
      const r = await fetch('/api/terminal/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: loginUser.value,
          password: loginPass.value,
        }),
      });
      if (r.status === 404) {
        setLoginError('Terminal is disabled in kingfisher config.');
        return;
      }
      if (r.status === 429) {
        setLoginError('Too many login attempts. Wait a minute and try again.');
        return;
      }
      if (!r.ok) {
        setLoginError('Invalid username or password.');
        loginPass.value = '';
        return;
      }
      const j = await r.json();
      loginPass.value = '';
      showTerminal(j.username || loginUser.value);
    } catch {
      setLoginError('Could not reach kingfisher.');
    }
  });

  termLogout.addEventListener('click', async () => {
    try {
      await fetch('/api/terminal/logout', { method: 'POST' });
    } catch {}
    showLogin();
  });

  function resolveFitAddonCtor() {
    if (typeof FitAddon === 'function') return FitAddon;
    if (FitAddon && typeof FitAddon.FitAddon === 'function') return FitAddon.FitAddon;
    return null;
  }

  function mountTerm() {
    if (term) return;
    if (typeof Terminal === 'undefined') {
      termEl.textContent = 'xterm.js failed to load.';
      return;
    }
    const FitCtor = resolveFitAddonCtor();
    term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      theme: {
        background: '#0b1320',
        foreground: '#d6deec',
        cursor: '#4cb4ff',
      },
    });
    if (FitCtor) {
      fitAddon = new FitCtor();
      term.loadAddon(fitAddon);
    }
    term.open(termEl);
    term.write('Connecting\u2026');
    requestAnimationFrame(() => {
      fitAndNotify();
      term.focus();
    });
    term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });
    resizeObs = new ResizeObserver(() => fitAndNotify());
    resizeObs.observe(termEl);
    resizeHandler = () => fitAndNotify();
    window.addEventListener('resize', resizeHandler);
  }

  function fitAndNotify() {
    if (!term) return;
    if (fitAddon) {
      try { fitAddon.fit(); } catch {}
    }
    if (ws && ws.readyState === WebSocket.OPEN && term.cols > 0 && term.rows > 0) {
      ws.send(JSON.stringify({
        type: 'resize',
        cols: term.cols,
        rows: term.rows,
      }));
    }
  }

  function closeWS() {
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.close();
      ws = null;
    }
  }

  function connectWS() {
    closeWS();
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws`);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      if (term) term.reset();
      requestAnimationFrame(() => fitAndNotify());
    };
    ws.onmessage = (ev) => {
      if (!term) return;
      if (typeof ev.data === 'string') {
        term.write(ev.data);
      } else {
        term.write(new Uint8Array(ev.data));
      }
    };
    ws.onclose = () => {
      if (term) {
        term.write('\r\n*** connection closed ***\r\n');
      }
    };
    ws.onerror = () => {
      if (term) {
        term.write('\r\n*** connection error ***\r\n');
      }
    };
  }

  function teardownTerm() {
    closeWS();
    if (resizeHandler) {
      window.removeEventListener('resize', resizeHandler);
      resizeHandler = null;
    }
    if (resizeObs) {
      resizeObs.disconnect();
      resizeObs = null;
    }
    if (term) {
      term.dispose();
      term = null;
      fitAddon = null;
      termEl.innerHTML = '';
    }
  }

  refreshSession();
})();
