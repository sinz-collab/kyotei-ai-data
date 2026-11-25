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

The VPS script refuses to run outside 23:00–06:00 JST, accepts only a trusted,
root-installed venue manifest, stages files before copying, backs up originals,
restarts only `sinz-live-fetch.service`, and rolls back if deployment fails.

Do not configure secrets or publish a deploy branch until the VPS bootstrap is
completed. Other venues require an administrator-installed allowlist manifest
and an explicit addition to the workflow venue choices.
