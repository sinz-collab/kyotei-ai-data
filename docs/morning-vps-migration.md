# Morning fetch VPS preparation

This is an isolated, disabled-by-default path for moving only the morning fetch
to the existing VPS. It does not alter `/opt/sinz-edge`, its service, the live
fetch, prediction engines, coefficients, tickets, or history.

## Isolation and controls

- Application: `/opt/sinz-morning-fetch/app` (root-owned)
- Virtualenv: `/opt/sinz-morning-fetch/venv` (root-owned)
- Browser cache: `/opt/sinz-morning-fetch/playwright`
- Work/state: `/var/lib/sinz-morning-fetch`
- SSH gateway user: `sinz-morning-gateway`
- Execution user: `sinz-morning` (no login shell)
- Forced command: `/usr/local/sbin/sinz-morning-gateway`
- One global non-blocking `flock`
- Dummy timeout: 5 minutes; fetch timeout: 90 minutes
- Transient systemd unit: `CPUQuota=100%`, `MemoryMax=1536M`, `TasksMax=256`
- Production fetch gate: `/etc/sinz-morning-fetch/enabled` containing exactly
  `ENABLED`. The installer removes this marker and never enables it.

The GitHub workflow is manual-only and requests only `dummy DATE SHARD`. It
receives the same `work/races/**` layout and uploads artifacts using the current
`morning-east-DATE` and `morning-west-DATE` names. It validates/merges those
artifacts and executes `build_site_data.py` against temporary data, stopping
before any commit, push, or publication.

Required repository secrets are `MORNING_VPS_HOST`, `MORNING_VPS_SSH_KEY`, and
`MORNING_VPS_KNOWN_HOSTS`.

## Explicit production enablement (not part of preparation)

Only after approval, an administrator may run:

```bash
printf 'ENABLED\n' | sudo tee /etc/sinz-morning-fetch/enabled >/dev/null
sudo chmod 0600 /etc/sinz-morning-fetch/enabled
```

The production `morning-data.yml` remains unchanged. A separate reviewed change
is required to replace its existing fetch job with the forced `fetch` command.
Removing the enable marker disables VPS production fetch again. BOATERS-specific
automation and access-control bypasses are outside this design.
