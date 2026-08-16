# Docker Browser VNC Design

## Goal

Allow an operator on the Docker host to interact with the exact Chromium session
used by Playwright for Xianyu password and risk-control recovery. Cookies created
by the verification flow must remain in that Playwright context so they can be
written back to the account record and used by the message WebSocket.

## Scope

- Add an optional virtual display, VNC server, and noVNC browser client to the
  existing application container.
- Run password-login recovery in headed mode when the account's `show_browser`
  flag is enabled.
- Publish VNC only on the host loopback interface.
- Require file-backed VNC authentication inside the container network.
- Keep the feature disabled by default for deployments that do not need manual
  browser access.
- Do not change auto-delivery matching, inventory, or message handling.

## Design

The base Compose file does not publish VNC. The opt-in
`docker-compose.vnc.yml` override mounts a password through a Compose secret,
binds host loopback to container ports 5900 and 6080, and sets
`ENABLE_VNC=true`.
The container entrypoint then starts Xvfb on `DISPLAY=:99`,
starts Fluxbox, and exposes that display through x11vnc on container port 5900.
The application is launched with the same `DISPLAY` value.

The target account is configured with `show_browser=true`. Existing browser
launch code therefore creates a headed persistent Chromium context at
`browser_data/user_<account-id>`. The operator connects to
`http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale` and acts directly
on that context. Raw VNC remains available for compatible clients. After verification,
the existing Playwright recovery loop reads cookies from the context, persists
them through the existing database path, and reconnects the account instance.

## Security

- The host mappings for ports 5900 and 6080 use `127.0.0.1`; they are not
  reachable from other hosts unless the operator deliberately adds a tunnel.
- x11vnc uses a generated authentication file derived from the mounted Compose
  secret. The password is not stored in container environment metadata.
- Account passwords and browser cookies are not logged or copied to the host.
- The browser profile is persisted in a dedicated host-mounted directory and
  must remain excluded from Git.

## Failure Handling

- The entrypoint fails fast if Xvfb or x11vnc cannot start while VNC is enabled.
- Stale X display locks are removed only for the configured virtual display at
  container startup.
- When VNC is disabled, startup behavior remains unchanged.
- If verification times out, the existing human-verification timeout and
  recovery status remain authoritative.

## Verification

- Shell-level startup test checks enabled and disabled entrypoint branches.
- Base `docker compose config` confirms no VNC port is reserved; the config with
  `docker-compose.vnc.yml` confirms loopback-only publication and secret mount.
- A rebuilt container must show Xvfb, Fluxbox, x11vnc, websockify, and headed
  Chromium.
- The local noVNC page must display the active Xianyu verification page.
- Completion requires `Token` refresh success and account state transition to
  `connected`; application health alone is insufficient.
