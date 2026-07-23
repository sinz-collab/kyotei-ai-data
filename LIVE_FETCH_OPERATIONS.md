# SINZ EDGE Live Fetch Operations

## Architecture

The production process is a normal Python 3.12 daemon managed by systemd. It
loads the current `data/manifest.json`, verifies that its date is today in
`Asia/Tokyo`, and fetches only `open=true` venues with complete 12-race morning
data. It does not change the morning workflow.

The preferred output root is `/opt/sinz-edge/data/live`. Serve that directory
from the VPS over HTTPS and set `window.KYOTEI_LIVE_DATA_BASES` in the site
configuration to the public `.../data/live` URL. The site falls back to the Git
repository copy when no VPS URL is configured.

## Install

```bash
sudo useradd --system --home /opt/sinz-edge --shell /usr/sbin/nologin sinz-edge
sudo git clone https://github.com/sinz-collab/kyotei-ai-data.git /opt/sinz-edge
sudo chown -R sinz-edge:sinz-edge /opt/sinz-edge
sudo -u sinz-edge python3.12 -m venv /opt/sinz-edge/.venv
sudo -u sinz-edge /opt/sinz-edge/.venv/bin/pip install -r /opt/sinz-edge/automation/requirements.txt
sudo -u sinz-edge /opt/sinz-edge/.venv/bin/playwright install chromium
sudo -u sinz-edge mkdir -p /opt/sinz-edge/data/live /opt/sinz-edge/logs /opt/sinz-edge/run
sudo install -m 0644 systemd/sinz-live-fetch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sinz-live-fetch.service
```

Configure nginx or Caddy to expose `/opt/sinz-edge/data/live` read-only over
HTTPS. Restrict CORS to the SINZ EDGE site origin.

## Stop And Restart

```bash
sudo systemctl stop sinz-live-fetch.service
sudo systemctl restart sinz-live-fetch.service
sudo systemctl disable --now sinz-live-fetch.service
```

`TimeoutStopSec=180` allows an in-flight fetch to finish. The browser and all
contexts are closed in `finally` blocks.

## Logs

```bash
journalctl -u sinz-live-fetch.service -f
tail -f /opt/sinz-edge/logs/live_fetch.jsonl
systemctl status sinz-live-fetch.service
```

Logs identify venue, race, item, source, retry, status, completeness, hash
change, and file update. Outside-window and no-target states are logged only
when the state changes.

## Manual Checks

Dry-run target selection performs no external fetch:

```bash
sudo -u sinz-edge /opt/sinz-edge/.venv/bin/python scripts/live_fetch_once.py --dry-run
```

Run the tests:

```bash
/opt/sinz-edge/.venv/bin/python -m unittest discover -s tests -v
```

## Optional Git Backup

VPS HTTPS delivery does not require Git pushes or site builds. Git backup is
disabled by default. If backup is required, run the following command from a
separate timer no more often than every 12 minutes and only during 08:20-23:00:

```bash
sudo install -m 0644 systemd/sinz-live-publish.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start sinz-live-publish.service
```

Do not schedule this service unless Git credentials for the `sinz-edge` account
are configured. No commit is created when hashes and files are unchanged.

## Recovery

1. Check that today's `data/manifest.json` exists and has the current JST date.
2. Check that the venue is `open=true`, has `entryCount=12`, and has a dated
   `dataPath`.
3. Check `journalctl` for HTTP status, parser errors, and browser launch errors.
4. Restart the service. Existing complete JSON remains intact when a retry
   returns incomplete or fails.
5. If the source HTML changed, stop the service before changing the parser.
6. Run unit tests and a single controlled race check, then restart.

At 23:00 JST no new fetch or retry starts. A request already in progress may
finish and save, after which the daemon remains idle until the next day's
08:20, and only after a current-date morning manifest exists.
