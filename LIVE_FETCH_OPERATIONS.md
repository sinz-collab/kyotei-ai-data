# SINZ EDGE Live Fetch VPS Operations

## Production Layout

- Repository and virtual environment: `/opt/sinz-edge`
- Morning data synchronized from GitHub: `/opt/sinz-edge/runtime/morning`
- Public live JSON only: `/opt/sinz-edge/data/live`
- Application log: `/opt/sinz-edge/logs/live_fetch.jsonl`
- Process and venue locks: `/opt/sinz-edge/run`
- Optional environment file: `/etc/sinz-edge/live-fetch.env`
- Service account: `sinz-edge` with `/usr/sbin/nologin`

The service account is not root and cannot log in. Code and the virtual
environment are root-owned and read-only to the service. Only live data,
morning runtime data, logs, and locks are writable. Nginx receives read ACLs
only for `data/live`; repository files are not web-accessible.

No password, private key, access token, or cookie belongs in this repository.
If a source later requires a session value, put it in
`/etc/sinz-edge/live-fetch.env`, set ownership to `root:sinz-edge`, and mode
`0640`. Never print that file in logs.

## Supported OS Preparation

Ubuntu 24.04:

```bash
sudo apt-get update
sudo apt-get install -y git nginx acl curl ca-certificates \
  python3.12 python3.12-venv certbot python3-certbot-nginx
```

Debian or Ubuntu releases without Python 3.12 must install Python 3.12 from a
trusted, maintained system repository before continuing. Confirm that both
`python3.12` and `python3.12 -m venv` work.

Rocky Linux or RHEL:

```bash
sudo dnf install -y git nginx acl curl ca-certificates python3.12 certbot
```

On RPM-based systems, set `NGINX_USER=nginx` when running the installer. Check
the Playwright Chromium dependency list for the installed distribution before
starting the service. Check SELinux before publishing. Keep enforcing mode
enabled and label only `/opt/sinz-edge/data/live` for Nginx read access.

## Initial Install

Run from a clean clone or downloaded release containing `deploy/`:

```bash
sudo bash deploy/install_vps.sh
```

RPM-based Nginx user:

```bash
sudo NGINX_USER=nginx bash deploy/install_vps.sh
```

The installer:

1. Creates the non-login `sinz-edge` account.
2. Clones or fast-forward pulls GitHub `main`.
3. Creates a Python 3.12 virtual environment.
4. Installs requirements and Chromium with Playwright OS dependencies.
5. Applies least-privilege ownership and Nginx ACLs.
6. Creates an empty `0640` environment file.
7. Runs unit tests.
8. Installs, enables, and starts `sinz-live-fetch.service`.

The daemon performs no race-source access before 08:20 JST or at/after 23:00
JST. At the first in-window cycle, it downloads the morning manifest only when
the local copy is missing, incomplete, or not today's JST date. Venue payloads
are validated and written atomically before the manifest becomes active.

## HTTPS And Nginx

Choose a DNS name, for example `live.example.com`, and point it to the VPS.
Replace placeholders in the bootstrap file and request a certificate:

```bash
sudo install -d -m 0755 /var/www/certbot
sed 's/__LIVE_DOMAIN__/live.example.com/g' \
  nginx/sinz-live-bootstrap.conf.example |
  sudo tee /etc/nginx/sites-available/sinz-live-bootstrap.conf >/dev/null
sudo ln -sfn /etc/nginx/sites-available/sinz-live-bootstrap.conf \
  /etc/nginx/sites-enabled/sinz-live-bootstrap.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot -d live.example.com
```

Install the final configuration. The second argument is the exact browser
origin, not a path:

```bash
sudo bash deploy/configure_nginx.sh \
  live.example.com \
  https://sinz-collab.github.io
```

The final server exposes only `/live/`, disables directory listings, returns
404 for unknown and internal paths, permits only GET/HEAD/OPTIONS, sends JSON
with `application/json`, disables stale caching, and emits CORS only for the
SINZ EDGE origin.

Expected URL:

```text
https://live.example.com/live/YYYY-MM-DD/karatsu/07/status.json
```

## Site Configuration

After the HTTPS endpoint passes verification, set the site configuration:

```javascript
window.KYOTEI_LIVE_DATA_BASES = ["https://live.example.com/live"];
```

Commit only the public URL. Do not place server credentials in `config.js`.
The browser refreshes the selected race JSON every four minutes without
triggering a site build.

## Verification

```bash
sudo bash deploy/verify_vps.sh \
  https://live.example.com \
  https://sinz-collab.github.io
```

Time and target selection without race-source access can be checked after the
morning runtime data exists:

```bash
sudo -u sinz-edge env PYTHONDONTWRITEBYTECODE=1 \
  /opt/sinz-edge/.venv/bin/python \
  /opt/sinz-edge/scripts/live_fetch_once.py \
  --manifest /opt/sinz-edge/runtime/morning/manifest.json \
  --dry-run
```

Run tests:

```bash
sudo -u sinz-edge env PYTHONDONTWRITEBYTECODE=1 \
  /opt/sinz-edge/.venv/bin/python -m unittest discover \
  -s /opt/sinz-edge/tests -v
```

## Service And Logs

```bash
sudo systemctl status sinz-live-fetch.service
sudo journalctl -u sinz-live-fetch.service -f
sudo tail -f /opt/sinz-edge/logs/live_fetch.jsonl
```

Stop, start, and restart:

```bash
sudo systemctl stop sinz-live-fetch.service
sudo systemctl start sinz-live-fetch.service
sudo systemctl restart sinz-live-fetch.service
```

Check for browser processes after stopping:

```bash
pgrep -a -u sinz-edge 'python|chromium|chrome' || true
```

## Updating

The updater refuses tracked local changes, stops the service, performs a
fast-forward-only pull, installs dependencies, runs tests, refreshes the
service file, and starts the service:

```bash
sudo bash /opt/sinz-edge/deploy/update_vps.sh
```

If tests fail, inspect the error before starting the service. Do not overwrite
local changes or force-reset the repository.

## Failure Recovery

1. Confirm the current JST date in `runtime/morning/manifest.json`.
2. Confirm the venue is `open=true`, has 12 races, six racers per race, and all
   deadlines.
3. Check `journalctl` for venue, race, item, source, HTTP status, and retry.
4. Confirm Nginx can read only a generated JSON file.
5. Restart the service after correcting the cause.
6. Existing complete JSON remains when a new fetch is incomplete or fails.

Live updates do not run the optional Git publisher. Normal live operation
therefore creates zero Git pushes and zero Netlify builds.
