# Caddy reverse proxy

Caddy terminates TLS in front of kingfisher (which stays plain HTTP on
`localhost:8080`) and serves it as HTTPS on the AP and home LAN. TLS keeps the
`eric` password (ssh + web terminal) unsniffable on the deliberately-open
`kingfisher` AP — see `deploy/system-rebuild-playbook.md` Appendix A.

## Files

- [`Caddyfile`](Caddyfile) — the site config. Install to `/etc/caddy/Caddyfile`.

```sh
sudo install -m 644 deploy/caddy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo caddy trust          # trust the internal CA locally (for curl over https on the Pi)
```

## Trusting the internal CA (why devices show "Not Secure")

Caddy signs the site cert with its own **internal CA**, which no device trusts
by default. Each device must install and trust the CA **root** certificate
once. The root lives on the Pi at:

```
/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
```

> **The CA regenerates whenever the caddy data dir is wiped — i.e. on any SSD
> rebuild/reflash.** After a rebuild every device shows "Not Secure" again and
> must re-trust the *new* root. (This bit us on the 2026-07-12 rebuild.)

### Get the root off the Pi

It is root-owned, so copy it somewhere world-readable first, then pull it:

```sh
# on the Pi:
sudo cp /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt /tmp/kingfisher-root.crt
sudo chmod a+r /tmp/kingfisher-root.crt

# on your workstation:
scp eric@kingfisher:/tmp/kingfisher-root.crt .
```

Then get `kingfisher-root.crt` onto each device (AirDrop, email, USB, a web
download — it's a public certificate, not a secret).

### iOS / iPadOS (the EFBs) — two steps, both required

1. Open `kingfisher-root.crt` on the device → **Settings → General → VPN &
   Device Management → Install** the profile.
2. **Then also enable full trust**: **Settings → General → About →
   Certificate Trust Settings** → toggle the "Caddy Local Authority" root
   **on**. Without this second step iOS installs but does **not** trust the CA,
   and Safari/EFB apps still reject HTTPS.

### macOS

Double-click the `.crt` → Keychain Access → add to the **System** keychain →
open it → Trust → **Always Trust**. (Or on the Pi itself, `sudo caddy trust`
already handles the local trust store.)

### Android

**Settings → Security → Encryption & credentials → Install a certificate → CA
certificate** → pick the file. Some apps ignore user CAs; browsers honour it.

### Linux workstation

```sh
sudo cp kingfisher-root.crt /usr/local/share/ca-certificates/kingfisher-root.crt
sudo update-ca-certificates
```

## Access control (app-enforced, AP-scoped)

**Enforcement lives in kingfisher, not Caddy** (see `internal/web/access.go`).
kingfisher binds `127.0.0.1:8080` (loopback), so Caddy is the sole ingress — a
public bind would let AP clients skip the gate by hitting `:8080` directly.
Caddy just terminates TLS and passes the real client IP:

```caddyfile
reverse_proxy 127.0.0.1:8080 {
    header_up X-Real-Client-IP {remote_host}
}
```

`{remote_host}` is the TCP peer and `header_up` overwrites any client-sent
value, so the IP kingfisher trusts cannot be spoofed.

Gated paths (return `403` for untrusted AP clients):

| Path | Why it's gated |
|------|----------------|
| `/terminal*` | browser root shell (page) |
| `/api/terminal/*` | its login, challenge, and PTY WebSocket |
| `/api/power/*` | `POST /api/power/off` is unauthenticated poweroff |
| `/api/access*` | the trusted-device management API itself |

The **only untrusted network is the open kingfisher AP** (`config.access.ap_subnet`,
default `192.168.10.0/24`), so the gate restricts *only there*: those paths are
blocked for AP clients whose IP is not in `config.access.trusted_ips`. Everything
**off** the AP is always allowed — loopback, the home LAN over `wlan1`, and any
future Tailscale peer — so no home subnet is hardcoded and it survives a network
change. The EFBs (`192.168.10.158`, `192.168.10.230`) are seeded trusted because
in the aircraft `wlan1` is down and the pilot's tablet is the sole external
control surface (the cockpit power-off button posts `/api/power/off`).

**Managing trusted devices:** in the cockpit UI, **More → Trusted devices** lists
the devices on the AP (from `/proc/net/arp`, filtered to the AP subnet) and lets
an already-trusted device grant/revoke each and give it a friendly name (the ARP
table has no hostname). Names and the trusted list persist in
`~/.config/kingfisher/config.json` under `access` — no config editing or Caddy
reload.

**Accepted limitations** (threat model, Appendix A): same-L2 IP spoofing defeats
this; and because only the AP is guarded, any non-AP network `wlan1` joins is
implicitly trusted for these paths.

Verify (uses the Pi's own AP address `192.168.10.1`, not a trusted device, as a
stand-in stranger):

```sh
curl -sk https://127.0.0.1/terminal -o /dev/null -w '%{http_code}\n'                              # off-AP → not 403
curl -sk --interface 192.168.10.1 https://192.168.10.1/terminal -o /dev/null -w '%{http_code}\n'  # AP stranger → 403
```

## The wlan1 (home-LAN) address

The `Caddyfile` lists `192.168.86.151`, the Pi's wlan1 address, as a **site
address** so Caddy serves TLS on it over the home LAN. It is DHCP-reserved on
the home router (by the wlan1 MAC), so it stays put; if it ever changes, update
only the two site-address lists. mDNS names (`kingfisher.lan`, `kingfisher`) are
also served and don't depend on the lease.
