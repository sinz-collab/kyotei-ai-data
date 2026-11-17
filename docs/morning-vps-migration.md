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
- One non-blocking `flock` per shard; workflow concurrency prevents a second daily run
- Dummy timeout: 5 minutes; production fetch has absolute JST cutoffs
- Transient systemd unit: `CPUQuota=50%`, `MemoryMax=1536M`, `TasksMax=256`,
  `Nice=10`, `IOWeight=10`
- Start is accepted only from 06:00 through 06:45 JST. Venue fetches stop at
  08:15 and the outer process is terminated by 08:18, before LIVE processing.
- A BOATERS security-verification page returns exit code 86, writes a shared
  stop marker, and prevents further venue requests for that date.
- Production fetch gate: `/etc/sinz-morning-fetch/enabled` containing exactly
  `ENABLED`. The installer removes this marker and never enables it.

The handoff test workflow is manual-only and requests only `dummy DATE SHARD`. It
receives the same `work/races/**` layout and uploads artifacts using the current
`morning-east-DATE` and `morning-west-DATE` names. It validates/merges those
artifacts and executes `build_site_data.py` against temporary data, stopping
before any commit, push, or publication.

Required repository secrets are `MORNING_VPS_HOST`, `MORNING_VPS_SSH_KEY`, and
`MORNING_VPS_KNOWN_HOSTS`.

## Production enablement

After approval and deployment, an administrator enables the isolated executor:

```bash
printf 'ENABLED\n' | sudo tee /etc/sinz-morning-fetch/enabled >/dev/null
sudo chmod 0600 /etc/sinz-morning-fetch/enabled
```

The production `morning-data.yml` runs at 06:05 JST and rejects starts outside
06:00-06:45 JST. It replaces only the fetch job with the forced `fetch` command;
the artifact names and existing prediction/publication process remain unchanged.
Removing the enable marker disables VPS production fetch again. Access-control
bypasses are outside this design.
