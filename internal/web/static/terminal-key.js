// Browser-side Ed25519 keys for terminal public-key login.
// Uses tweetnacl so key generation works over plain HTTP (Web Crypto needs HTTPS).
(function (global) {
  const IDB_NAME = 'kingfisher-terminal';
  const IDB_STORE = 'keys';
  const IDB_KEY = 'ed25519';

  function requireNacl() {
    if (typeof nacl === 'undefined' || !nacl.sign || !nacl.randomBytes) {
      throw new Error('Ed25519 support failed to load (missing tweetnacl)');
    }
  }

  function u32be(n) {
    return new Uint8Array([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]);
  }

  function concat(...parts) {
    const len = parts.reduce((s, p) => s + p.length, 0);
    const out = new Uint8Array(len);
    let off = 0;
    for (const p of parts) {
      out.set(p, off);
      off += p.length;
    }
    return out;
  }

  function readSSHString(data, off) {
    if (off + 4 > data.length) throw new Error('truncated ssh string');
    const n = (data[off] << 24) | (data[off + 1] << 16) | (data[off + 2] << 8) | data[off + 3];
    off += 4;
    if (off + n > data.length) throw new Error('truncated ssh string payload');
    return { bytes: data.subarray(off, off + n), next: off + n };
  }

  function sshStringBytes(bytes) {
    return concat(u32be(bytes.length), bytes);
  }

  function b64(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }

  function b64dec(s) {
    const bin = atob(s.replace(/\s/g, ''));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function openSSHPublicLine(rawPub32, comment) {
    const algo = new TextEncoder().encode('ssh-ed25519');
    const blob = concat(sshStringBytes(algo), sshStringBytes(rawPub32));
    return 'ssh-ed25519 ' + b64(blob) + ' ' + (comment || 'kingfisher-terminal');
  }

  function keyPairFromSeed(seed) {
    requireNacl();
    if (seed.length !== 32) throw new Error('invalid Ed25519 seed length');
    return nacl.sign.keyPair.fromSeed(seed);
  }

  async function idbOpen() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore(IDB_STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function saveRecord(rec) {
    const db = await idbOpen();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, 'readwrite');
      tx.objectStore(IDB_STORE).put(rec, IDB_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function loadRecord() {
    const db = await idbOpen();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, 'readonly');
      const req = tx.objectStore(IDB_STORE).get(IDB_KEY);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  }

  async function clearStoredKey() {
    const db = await idbOpen();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, 'readwrite');
      tx.objectStore(IDB_STORE).delete(IDB_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function loadSeed() {
    const rec = await loadRecord();
    if (!rec) return null;
    if (rec.seed_b64) return b64dec(rec.seed_b64);
    // Legacy Web Crypto JWK stored before tweetnacl switch.
    if (rec.d) return b64dec(rec.d);
    return null;
  }

  async function saveSeed(seed) {
    await saveRecord({ seed_b64: b64(seed) });
  }

  async function generateKeyPair() {
    requireNacl();
    const seed = nacl.randomBytes(32);
    const kp = keyPairFromSeed(seed);
    await saveSeed(seed);
    return {
      publicLine: openSSHPublicLine(kp.publicKey, 'kingfisher-terminal'),
      rawPublic: kp.publicKey,
    };
  }

  async function hasStoredKey() {
    const seed = await loadSeed();
    return !!seed;
  }

  async function storedPublicLine() {
    const seed = await loadSeed();
    if (!seed) return null;
    const kp = keyPairFromSeed(seed);
    return openSSHPublicLine(kp.publicKey, 'kingfisher-terminal');
  }

  async function sign(message) {
    const seed = await loadSeed();
    if (!seed) throw new Error('no terminal key in this browser');
    const kp = keyPairFromSeed(seed);
    const msg = message instanceof Uint8Array ? message : new Uint8Array(message);
    return nacl.sign.detached(msg, kp.secretKey);
  }

  function parseOpenSSHEd25519Private(pemText) {
    const m = pemText.match(/-----BEGIN OPENSSH PRIVATE KEY-----([\s\S]+?)-----END OPENSSH PRIVATE KEY-----/);
    if (!m) throw new Error('not an OpenSSH private key');
    const data = b64dec(m[1]);
    const magic = new TextEncoder().encode('openssh-key-v1\0');
    for (let i = 0; i < magic.length; i++) {
      if (data[i] !== magic[i]) throw new Error('unsupported key format');
    }
    let off = magic.length;
    let part = readSSHString(data, off); off = part.next;
    const cipher = new TextDecoder().decode(part.bytes);
    part = readSSHString(data, off); off = part.next;
    const kdf = new TextDecoder().decode(part.bytes);
    part = readSSHString(data, off); off = part.next;
    if (cipher !== 'none' || kdf !== 'none') {
      throw new Error('encrypted keys are not supported; use an unencrypted key or generate a browser key');
    }
    if (off + 4 > data.length) throw new Error('truncated key');
    const nkeys = (data[off] << 24) | (data[off + 1] << 16) | (data[off + 2] << 8) | data[off + 3];
    off += 4;
    if (nkeys !== 1) throw new Error('only single-key OpenSSH files are supported');
    part = readSSHString(data, off); off = part.next;
    part = readSSHString(data, off); off = part.next;
    const priv = part.bytes;
    if (priv.length < 8) throw new Error('truncated private section');
    const check1 = (priv[0] << 24) | (priv[1] << 16) | (priv[2] << 8) | priv[3];
    const check2 = (priv[4] << 24) | (priv[5] << 16) | (priv[6] << 8) | priv[7];
    if (check1 !== check2) throw new Error('private key checksum mismatch');
    let poff = 8;
    part = readSSHString(priv, poff); poff = part.next;
    const keyType = new TextDecoder().decode(part.bytes);
    if (keyType !== 'ssh-ed25519') throw new Error('only Ed25519 keys are supported');
    part = readSSHString(priv, poff); poff = part.next;
    const pub = part.bytes;
    part = readSSHString(priv, poff); poff = part.next;
    const priv64 = part.bytes;
    if (pub.length !== 32 || priv64.length !== 64) throw new Error('invalid Ed25519 key material');
    const seed = priv64.subarray(0, 32);
    return { seed, pub };
  }

  async function importOpenSSHPrivateKeyFile(text) {
    const { seed, pub } = parseOpenSSHEd25519Private(text);
    const kp = keyPairFromSeed(seed);
    for (let i = 0; i < pub.length; i++) {
      if (kp.publicKey[i] !== pub[i]) {
        throw new Error('private key does not match its public half');
      }
    }
    await saveSeed(seed);
    return openSSHPublicLine(kp.publicKey, 'kingfisher-terminal');
  }

  global.TerminalKey = {
    generateKeyPair,
    hasStoredKey,
    storedPublicLine,
    sign,
    clearStoredKey,
    importOpenSSHPrivateKeyFile,
    openSSHPublicLine,
  };
})(typeof window !== 'undefined' ? window : globalThis);
