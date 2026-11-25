# Engine release gateway (bootstrap required)

The application code in this directory is **not installed by merging it to main**.
It is installed once by a VPS administrator. It does not change the existing
morning-fetch SSH gateway or its keys.

## One-time administrator setup

1. Generate a **new, dedicated** ed25519 deploy key pair outside the repository.
2. Review the three scripts in this directory. On the VPS, run the installer
   from a verified main checkout:
   `sudo bash deploy/engine-release/bootstrap.sh /path/to/deploy_public_key.pub`
3. Add three GitHub Actions **environment** secrets to environment `engine-release`:
   `ENGINE_VPS_HOST`, `ENGINE_VPS_SSH_KEY` (the new private key), and
   `ENGINE_VPS_KNOWN_HOSTS` (pinned and independently verified host key).
4. Require manual approval for the GitHub environment if available.

## Release from ChatGPT or GitHub

Release `main` by creating/updating the `engine-deploy/tokoname` branch **to
the current main SHA**. This triggers the workflow. `workflow_dispatch` is also
available from GitHub Actions. The workflow rejects non-main commits.

The release gate checks the latest publisher manifest for all 24 venues. Every
active venue must have 12 valid deadlines and its last deadline must be at least
45 minutes in the past. A missing/stale/incomplete manifest fails closed. In
addition, the deployer holds the common publisher lock and all 12 LIVE venue
locks during installation and restart, or rejects if any is busy.

The script accepts only a trusted, root-installed venue manifest and records a
root-owned checksum baseline during bootstrap. A release refuses to overwrite
any file changed outside the gateway, stages and compiles files before copying,
backs up originals, restarts only `sinz-live-fetch.service`, and rolls back if
deployment or the health check fails.

### Upgrading an already-installed gateway

After reviewing and merging this change, a VPS administrator must run once
from a verified main checkout:

`sudo bash deploy/engine-release/upgrade-safety-gate.sh`

This does **not** restart LIVE or remove the previously installed 23:00–06:00
restriction until the new root-owned script is installed. The upgrade retains
a backup of the prior script. Re-test a denied race-in-progress deployment and
an allowed post-race deployment before enabling routine daytime updates.

The installer adds an sshd `Match User` restriction and reloads (does not
restart) SSH after `sshd -t` succeeds. It does not modify the existing morning
data account, key, authorized keys, or forced command.

Do not configure secrets or publish a deploy branch until the VPS bootstrap is
completed. Other venues require an administrator-installed allowlist manifest
and an explicit addition to the workflow venue choices.
