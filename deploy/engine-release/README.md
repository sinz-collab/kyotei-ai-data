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
active venue must still have 12 valid deadlines, and a missing, stale, or
incomplete manifest fails closed. The release window itself is scoped to
Tokoname: release is allowed when Tokoname is closed, at least 45 minutes before
its first deadline, or at least 45 minutes after its last deadline. Other venues
may remain active.

The deployer holds the common publisher lock, the existing Tokoname LIVE venue
lock, and `run/venues/tokoname-engine.lock`. The same engine lock is held by the
one-shot Tokoname prediction worker. A running Tokoname fetch/prediction rejects
the release, and a release in progress makes a prediction return `busy` without
loading a partial engine.

The script accepts only a trusted, root-installed venue manifest and records a
root-owned checksum baseline during bootstrap. A release refuses to overwrite
any file changed outside the gateway, verifies the requested SHA against current
`main`, stages and compiles files before copying, backs up originals, and
verifies every installed SHA-256. It then loads the engine, normal-ticket and
fly-ticket components in a fresh Python process. Any failure restores and
hash-checks the backup automatically.

The common `sinz-live-fetch.service` is not restarted. Its MainPID is captured
before release and must remain unchanged. Each Tokoname prediction runs in a
fresh child process, so the next prediction reads the new code and model files;
other venues continue in the existing LIVE process.

### Upgrading an already-installed gateway

After reviewing and merging this change, a VPS administrator must run once
from a verified main checkout:

`sudo bash deploy/engine-release/upgrade-safety-gate.sh`

This does **not** restart LIVE. It retains a backup of the prior script,
manifest, and checksum state, and expands the allowlist only after confirming
that previously managed files still match their recorded hashes.

The parent LIVE process must first contain the one-shot-worker integration from
this change. Activate that code only through the normal, all-venue maintenance
procedure; that one-time activation is separate from venue-scoped engine
releases. After activation, verify on the VPS: release allowed while another
venue is active and Tokoname is outside its window; release denied during
Tokoname racing; release denied while holding `tokoname-engine.lock`; worker
self-check reports all three components; LIVE MainPID is unchanged; and an
injected post-copy failure restores all file hashes. Do not apply to other
venues.

The installer adds an sshd `Match User` restriction and reloads (does not
restart) SSH after `sshd -t` succeeds. It does not modify the existing morning
data account, key, authorized keys, or forced command.

Do not configure secrets or publish a deploy branch until the VPS bootstrap is
completed. Other venues require an administrator-installed allowlist manifest
and an explicit addition to the workflow venue choices.
