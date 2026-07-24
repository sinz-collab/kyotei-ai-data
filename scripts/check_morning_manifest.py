from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from trigger_morning_workflow import GitHubActionsClient, parse_timestamp


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/"
    "sinz-collab/kyotei-ai-data/main/data/manifest.json"
)
DEFAULT_REPOSITORY = "sinz-collab/kyotei-ai-data"
DEFAULT_WORKFLOW = "morning-data.yml"
DEFAULT_STATE_DIR = Path("/var/lib/sinz-edge-morning-fallback")
TRIGGER_WINDOW_START = time(6, 35)
TRIGGER_WINDOW_END = time(6, 55)


def emit(event: str, **fields: Any) -> None:
    record = {
        "timestamp": datetime.now(JST).isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)


def fetch_manifest(url: str, timeout: int = 20) -> dict[str, Any]:
    separator = "&" if "?" in url else "?"
    cache_busted = f"{url}{separator}fallback_check={int(datetime.now().timestamp())}"
    request = urllib.request.Request(
        cache_busted,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "sinz-morning-fallback/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"manifest HTTP status {response.status}")
        result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("manifest response is not an object")
        return result


def manifest_is_current(
    manifest: dict[str, Any] | None,
    expected_date: str,
) -> tuple[bool, str | None, str | None]:
    if not manifest:
        return False, None, None
    manifest_date = str(manifest.get("date") or "") or None
    updated_text = str(manifest.get("updatedAt") or "") or None
    if manifest_date != expected_date or not updated_text:
        return False, manifest_date, updated_text
    try:
        updated = parse_timestamp(updated_text).astimezone(JST)
    except ValueError:
        return False, manifest_date, updated_text
    return updated.date().isoformat() == expected_date, manifest_date, updated_text


def same_day_runs(
    runs: list[dict[str, Any]],
    expected_date: str,
) -> list[dict[str, Any]]:
    found = []
    for run in runs:
        created_text = str(run.get("created_at") or "")
        if not created_text:
            continue
        try:
            created_date = parse_timestamp(created_text).astimezone(JST).date()
        except ValueError:
            continue
        if created_date.isoformat() == expected_date:
            found.append(run)
    return sorted(found, key=lambda run: str(run.get("created_at") or ""))


def workflow_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    active = [
        run for run in runs
        if run.get("status") in {"queued", "in_progress", "waiting", "pending"}
    ]
    successes = [
        run for run in runs
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    latest = runs[-1] if runs else {}
    return {
        "active": active,
        "successes": successes,
        "latest": latest,
        "state": (
            str(active[-1].get("status"))
            if active
            else (
                "completed_success"
                if successes
                else (
                    f"completed_{latest.get('conclusion')}"
                    if latest
                    else "missing"
                )
            )
        ),
    }


def _lock_file(file_object: Any) -> None:
    if os.name == "nt":
        import msvcrt

        file_object.seek(0)
        if file_object.read(1) == "":
            file_object.write("0")
            file_object.flush()
        file_object.seek(0)
        msvcrt.locking(file_object.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file_object.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(file_object: Any) -> None:
    if os.name == "nt":
        import msvcrt

        file_object.seek(0)
        msvcrt.locking(file_object.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file_object.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state_dir / "check.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        _lock_file(lock)
        try:
            yield
        finally:
            _unlock_file(lock)


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def reserve_dispatch(path: Path, record: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return True


def update_state(path: Path, record: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_check(
    *,
    mode: str,
    now: datetime,
    manifest_loader: Callable[[], dict[str, Any]],
    github: Any,
    state_dir: Path,
    dispatch_enabled: bool,
    dry_run: bool,
    log: Callable[..., None] = emit,
) -> dict[str, Any]:
    now = now.astimezone(JST)
    expected_date = now.date().isoformat()
    log(
        "morning_fallback_check",
        check_time=now.isoformat(timespec="seconds"),
        expected_date=expected_date,
        mode=mode,
        dispatch_enabled=dispatch_enabled,
        dry_run=dry_run,
    )

    manifest = None
    manifest_error = None
    try:
        manifest = manifest_loader()
    except Exception as exc:
        manifest_error = type(exc).__name__
    current, manifest_date, updated_at = manifest_is_current(
        manifest,
        expected_date,
    )
    log(
        "morning_manifest_status",
        expected_date=expected_date,
        manifest_date=manifest_date,
        updated_at=updated_at,
        current=current,
        error=manifest_error,
    )
    if current:
        result = {
            "action": "none",
            "reason": "manifest_current",
            "manifest_current": True,
        }
        log("morning_fallback_result", **result)
        return result

    state_path = state_dir / f"{expected_date}.json"
    state = load_state(state_path)
    try:
        runs = same_day_runs(github.list_runs(), expected_date)
    except Exception as exc:
        result = {
            "action": "none",
            "reason": "github_api_error",
            "manifest_current": False,
            "error": type(exc).__name__,
        }
        log("morning_workflow_status", state="api_error", run_id=None)
        log("morning_fallback_result", **result)
        return result

    summary = workflow_summary(runs)
    latest = summary["latest"]
    log(
        "morning_workflow_status",
        state=summary["state"],
        run_id=latest.get("id"),
        trigger_event=latest.get("event"),
        conclusion=latest.get("conclusion"),
    )

    if mode == "verify":
        run = None
        if state and state.get("runId"):
            try:
                run = github.get_run(int(state["runId"]))
            except Exception:
                run = None
        if run is None and runs:
            run = latest
        try:
            verified_manifest = manifest_loader()
        except Exception:
            verified_manifest = None
        verified, verified_date, verified_updated = manifest_is_current(
            verified_manifest,
            expected_date,
        )
        result = {
            "action": "none",
            "reason": "verify_only",
            "run_id": (run or {}).get("id"),
            "workflow_state": (run or {}).get("status") or summary["state"],
            "conclusion": (run or {}).get("conclusion"),
            "manifest_current": verified,
            "manifest_date": verified_date,
            "updated_at": verified_updated,
        }
        log("morning_fallback_result", **result)
        return result

    if summary["active"]:
        result = {
            "action": "none",
            "reason": "workflow_active",
            "run_id": summary["active"][-1].get("id"),
            "manifest_current": False,
        }
        log("morning_fallback_result", **result)
        return result

    if summary["successes"]:
        try:
            refreshed = manifest_loader()
        except Exception:
            refreshed = None
        refreshed_current, refreshed_date, refreshed_updated = manifest_is_current(
            refreshed,
            expected_date,
        )
        result = {
            "action": "none",
            "reason": "workflow_already_succeeded",
            "run_id": summary["successes"][-1].get("id"),
            "manifest_current": refreshed_current,
            "manifest_date": refreshed_date,
            "updated_at": refreshed_updated,
        }
        log("morning_fallback_result", **result)
        return result

    if state is not None:
        result = {
            "action": "none",
            "reason": "daily_dispatch_already_attempted",
            "run_id": state.get("runId"),
            "manifest_current": False,
        }
        log("morning_fallback_result", **result)
        return result

    within_window = TRIGGER_WINDOW_START <= now.timetz().replace(tzinfo=None) < TRIGGER_WINDOW_END
    if not within_window:
        result = {
            "action": "none",
            "reason": "outside_dispatch_window",
            "manifest_current": False,
        }
        log("morning_fallback_result", **result)
        return result

    if dry_run or not dispatch_enabled:
        result = {
            "action": "would_dispatch",
            "reason": "dry_run" if dry_run else "dispatch_disabled",
            "manifest_current": False,
        }
        log("morning_fallback_result", **result)
        return result

    attempt_time = now.astimezone(ZoneInfo("UTC"))
    record = {
        "date": expected_date,
        "dispatchAttemptedAt": now.isoformat(timespec="seconds"),
        "dispatchRequested": False,
        "runId": None,
        "outcome": "reserved",
    }
    if not reserve_dispatch(state_path, record):
        result = {
            "action": "none",
            "reason": "daily_dispatch_already_attempted",
            "manifest_current": False,
        }
        log("morning_fallback_result", **result)
        return result

    try:
        github.dispatch(expected_date)
        record["dispatchRequested"] = True
        record["outcome"] = "requested"
        found = github.find_dispatched_run(
            expected_date,
            attempt_time - timedelta(seconds=5),
        )
        if found:
            record["runId"] = found.get("id")
            record["outcome"] = "run_observed"
        update_state(state_path, record)
    except Exception as exc:
        record["outcome"] = "dispatch_error"
        record["error"] = type(exc).__name__
        update_state(state_path, record)
        result = {
            "action": "dispatch_attempted",
            "reason": "dispatch_error",
            "run_id": None,
            "manifest_current": False,
            "error": type(exc).__name__,
        }
        log("morning_fallback_result", **result)
        return result

    result = {
        "action": "dispatched",
        "reason": "fallback_required",
        "run_id": record["runId"],
        "manifest_current": False,
    }
    log("morning_fallback_result", **result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fallback", "verify"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--manifest-url",
        default=os.environ.get("MORNING_MANIFEST_URL", DEFAULT_MANIFEST_URL),
    )
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
    )
    parser.add_argument(
        "--workflow",
        default=os.environ.get("GITHUB_WORKFLOW", DEFAULT_WORKFLOW),
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("MORNING_FALLBACK_STATE_DIR", DEFAULT_STATE_DIR)),
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    dispatch_enabled = (
        os.environ.get("MORNING_FALLBACK_DISPATCH_ENABLED", "0") == "1"
    )
    github = GitHubActionsClient(
        args.repository,
        args.workflow,
        token,
    )
    try:
        with exclusive_lock(args.state_dir):
            result = run_check(
                mode=args.mode,
                now=datetime.now(JST),
                manifest_loader=lambda: fetch_manifest(args.manifest_url),
                github=github,
                state_dir=args.state_dir,
                dispatch_enabled=dispatch_enabled,
                dry_run=args.dry_run,
            )
    except BlockingIOError:
        emit(
            "morning_fallback_result",
            action="none",
            reason="lock_busy",
            manifest_current=False,
        )
        return 0
    return 1 if result.get("reason") == "github_api_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
