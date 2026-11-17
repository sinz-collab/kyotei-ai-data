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

## Manual recovery

`Morning race data` can also be started with `Run workflow`. Select `failed` to
retry only venues whose cached status is missing or failed, or `all` to inspect
every configured venue. A validated complete cache is reused in either mode and
is never fetched again. Manual recovery is limited to the current JST date.

The forced command accepts only `recovery-failed DATE SHARD` and
`recovery-all DATE SHARD`. GitHub runs the east and west shards sequentially;
the executor also holds a global non-blocking recovery lock. Before the run and
before every venue it verifies that `sinz-live-fetch.service` is active with a
live PID, at least 1 GiB of memory is available, and one-minute load does not
exceed the CPU count. Recovery uses `CPUQuota=25%`, `MemoryMax=1024M`,
`Nice=15`, `IOWeight=5`, a four-minute venue timeout, and a 26-minute shard
timeout. A failed safety check stops subsequent venue requests.

Existing predictions and prediction history are restored after recovery model
execution. A prediction created after its race deadline is marked
`manual_recovery_post_deadline`, has `performanceEligible=false`, is excluded
from prediction history, and carries an explicit metrics-exclusion marker.
