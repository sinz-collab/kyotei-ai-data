from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
API_ROOT = "https://api.github.com"


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHubActionsClient:
    def __init__(
        self,
        repository: str,
        workflow: str,
        token: str = "",
        *,
        timeout: int = 20,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.repository = repository
        self.workflow = workflow
        self.token = token
        self.timeout = timeout
        self.opener = opener

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "sinz-morning-fallback/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{API_ROOT}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        with self.opener(request, timeout=self.timeout) as response:
            raw = response.read()
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
            return int(response.status), decoded

    def list_runs(self) -> list[dict[str, Any]]:
        workflow = urllib.parse.quote(self.workflow, safe="")
        path = (
            f"/repos/{self.repository}/actions/workflows/{workflow}/runs"
            "?per_page=100"
        )
        _, result = self._request("GET", path)
        return list(result.get("workflow_runs") or [])

    def get_run(self, run_id: int) -> dict[str, Any]:
        _, result = self._request(
            "GET",
            f"/repos/{self.repository}/actions/runs/{run_id}",
        )
        return result

    def dispatch(self, target_date: str) -> None:
        workflow = urllib.parse.quote(self.workflow, safe="")
        status, _ = self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{workflow}/dispatches",
            {"ref": "main", "inputs": {"date": target_date}},
        )
        if status != 204:
            raise RuntimeError(f"unexpected workflow dispatch status: {status}")

    def find_dispatched_run(
        self,
        target_date: str,
        not_before: datetime,
        *,
        attempts: int = 3,
        delay_seconds: float = 2.0,
    ) -> dict[str, Any] | None:
        for attempt in range(attempts):
            candidates = []
            for run in self.list_runs():
                created_text = str(run.get("created_at") or "")
                if run.get("event") != "workflow_dispatch" or not created_text:
                    continue
                created = parse_timestamp(created_text)
                if (
                    created.astimezone(JST).date().isoformat() == target_date
                    and created >= not_before
                ):
                    candidates.append(run)
            if candidates:
                return max(
                    candidates,
                    key=lambda item: str(item.get("created_at") or ""),
                )
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
        return None
