# Security policy

Mirdain Print Hub controls physical equipment. Treat access to the dashboard as
access to every connected printer.

## Supported version

Security fixes are made on the latest release and the default branch. Older
releases may not receive patches.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's
**Security** tab and choose **Report a vulnerability** to send a private
advisory to the maintainers. Include:

- the affected version or commit;
- the deployment method (desktop or Docker);
- reproduction steps and expected impact; and
- whether a printer command, credential, or uploaded file is involved.

Do not test against printers or networks you do not own or administer. Please
allow a reasonable remediation window before publishing details.

## Deployment boundary

Mirdain Print Hub does not currently provide dashboard accounts,
authorization, TLS termination, or brute-force protection.

- The desktop launcher and Docker Compose configuration bind to `127.0.0.1` by
  default.
- The API and bundled Nginx frontend accept only localhost `Host` values by
  default. LAN/VPN deployments must explicitly list each exact trusted name or
  address in `MIRDAIN_ALLOWED_HOSTS`; wildcards are not supported. Unsafe
  browser requests with a different `Origin` are rejected. These checks reduce
  DNS-rebinding and CSRF exposure but do not authenticate users.
- Do not port-forward the dashboard from an internet router.
- For remote access, use a private VPN such as WireGuard, Tailscale, or an
  equivalent authenticated tunnel, and restrict access with the host firewall.
- A reverse proxy is not sufficient by itself. It must enforce authentication
  and HTTPS before exposing Mirdain beyond a trusted private network.
- Run exactly one Mirdain API process. The command, revocation, and endpoint
  safety gates are process-local; Uvicorn `--workers`, multiple containers, or
  active replicas are not supported and can issue conflicting physical
  operations.
- `MIRDAIN_API_TOKEN` can require a Bearer token on the versioned
  `/api/public/v1` automation endpoints. It does **not** authenticate the
  dashboard or its local control endpoints, so it does not replace the
  loopback/VPN boundary.
- Keep printer firmware and OctoPrint, Moonraker, or PrusaLink installations
  patched. Give API keys or PrusaLink Digest credentials only the permissions
  the adapter needs.
- Network adapters re-resolve every configured hostname to local/private
  address space before network operations, reject ambiguous base paths, refuse
  HTTP redirects, and fail closed when records drift to or collide on one
  resolved endpoint. DNS can still change between final resolution and the
  socket connection, including during a file upload; a changed private
  endpoint could receive that file or credential even though the later START
  gate fails closed. DNS resolution also uses the operating system resolver,
  whose delays can briefly hold the fleet identity gate. Prefer reserved
  numeric LAN addresses, trusted LAN DNS, and a host firewall.
- The supported printer APIs do not provide one universal stable hardware
  fingerprint. Mirdain cannot detect a different machine or controller that
  later appears at the exact same scheme, address, port, and base path.
  Unattended physical START therefore requires an operator-attested trusted
  static address or DHCP reservation and confirmation that the physical
  controller is not registered through another URL or printer record.
  Reconnect, inspect, confirm **READY**, and explicitly re-arm after any
  printer, controller, or network change.

Desktop installs first use the operating system credential vault through Python
`keyring`. When a vault is unavailable (including most headless containers),
Mirdain encrypts credentials with Fernet. The key comes from
`MIRDAIN_SECRET_KEY` or is generated in `backend/data/.secret-key`. Encrypted
records are stored under `backend/data/secrets/`. Protect the environment,
Docker access, the key file, and backups; anyone with the key and encrypted
records can recover printer credentials.

## Physical safety

Software cannot verify an empty build plate, correct material, safe
temperatures, sound wiring, ventilation, or a mechanically reliable
auto-ejection system. Keep printers within their manufacturer's operating
requirements and never leave an unvalidated automated workflow unattended.

Automatic starts are opt-in and require printer-level enablement through the
explicit `ARM STATIC ENDPOINT` action, a task armed for restart, and a file
marked as tested for auto-ejection. Automatic
bed-clearance release is a separate task-level opt-in and is honored only after
an adapter reports explicit successful completion for a run that telemetry
first observed printing. Manual completion and generic idle telemetry always
leave the printer waiting for an operator to confirm **READY**. A service
restart, monitoring interruption, or changed resolved endpoint also invalidates
stored clearance, disarms automation, and requires a fresh inspection. These
checks reduce accidental starts; they do not certify endpoint ownership, a
machine, ejection mechanism, or G-code as safe.
