# Pi AP setup for EFB apps (ForeFlight, iFlyEFB)

Use when the Pi runs the **`kingfisher`** Wi‑Fi access point (NetworkManager
`ipv4.method: shared` on `wlan0`, typically **`192.168.10.1/24`**) and tablets
should receive GDL90 from kingfisher.

Kingfisher broadcasts Stratux-compatible GDL90 on UDP port **4000**. Enable in
`~/.config/kingfisher/config.json`:

```json
"gdl90": {
  "enabled": true,
  "static_ips": ["192.168.10.141", "192.168.10.40"]
}
```

On a NetworkManager AP, DHCP is served by **dnsmasq**. Kingfisher’s lease
parser expects **isc-dhcp** format (`dhcpd.leases`), so **`static_ips` is the
reliable client list** on this topology until dnsmasq lease parsing lands.
`dhcp_leases` in config is ignored for discovery here; reservations below keep
those IPs stable.

## 1. DHCP reservations (recommended)

Pin each tablet by MAC so it always gets the same address in the pool
(`192.168.10.10`–`192.168.10.254`).

### Find MAC addresses

After a device joins `kingfisher`:

```bash
sudo cat /var/lib/NetworkManager/dnsmasq-wlan0.leases
```

Columns: `epoch MAC IP hostname clientid`.

### Disable randomized Wi‑Fi MAC

iOS and Android may use a **different MAC per network**. Turn that off for
`kingfisher` or reservations will drift:

- **iPad:** Settings → Wi‑Fi → `i` on `kingfisher` → **Private Wi‑Fi Address**
  → **Off**
- **Android:** Wi‑Fi → `kingfisher` → details → **Privacy** / **MAC address
  type** → **Use device MAC** (wording varies by OEM)

Forget the network, rejoin, and confirm the MAC in the leases file again.

### Add dnsmasq host entries

```bash
sudo tee /etc/NetworkManager/dnsmasq-shared.d/kingfisher-efb.conf <<'EOF'
dhcp-host=aa:bb:cc:dd:ee:01,192.168.10.141,iPad
dhcp-host=aa:bb:cc:dd:ee:02,192.168.10.40,Pixel
EOF
```

Replace MACs and IPs with yours. Restart the hotspot:

```bash
nmcli connection down kingfisher && nmcli connection up kingfisher
```

On each device: **Forget** `kingfisher`, reconnect, verify the assigned IP in
Wi‑Fi details.

### Match `static_ips`

List the same IPs in `gdl90.static_ips` and restart kingfisher.

## 2. Device-side static IP (optional)

Prefer Pi DHCP reservations. If you set a manual IP on the tablet instead:

| Field | Value |
|-------|--------|
| IP | e.g. `192.168.10.141` |
| Subnet mask | `255.255.255.0` |
| Router / gateway | `192.168.10.1` |
| DNS | `192.168.10.1` |

Still add that IP to `gdl90.static_ips`.

## 3. EFB app settings

**ForeFlight:** connect to `kingfisher` → *More* → *Devices* → enable GDL90 at
**`192.168.10.1`**.

**iFlyEFB:** *Menu* → *About* → *Connected Devices* → *Wireless Device* →
Stratux/FlightBox; *Setup* → *ADS-B* → select Stratux/Flightbox manually
(auto-detect often fails). Heartbeat should increment; expect **GPS Data Only**
(no ADS-B traffic/weather from kingfisher).

**Android:** disable smart network switching / “switch to mobile data when Wi‑Fi
is poor”; force-quit other aviation apps if GDL90 does not bind.

Requires `ahrs.enabled` for attitude.

## 4. Verify

```bash
curl -s http://127.0.0.1:8080/api/status | jq .gdl90
sudo tcpdump -i wlan0 -n udp port 4000
```

Expect `clients` ≥ 1, rising `msgs_sent`, and `192.168.10.1` → each tablet IP
on port 4000.
