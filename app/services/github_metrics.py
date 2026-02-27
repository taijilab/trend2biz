from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import httpx


class GithubMetricsError(Exception):
    pass


class RateLimitError(GithubMetricsError):
    def __init__(self, message: str, reset_at: Optional[int] = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at  # Unix timestamp when rate limit resets


def _check_rate_limit(resp: httpx.Response) -> None:
    """Raise RateLimitError if the response signals a GitHub rate limit."""
    if resp.status_code in (429, 403):
        reset_ts: Optional[int] = None
        retry_after = resp.headers.get("Retry-After")
        x_reset = resp.headers.get("X-RateLimit-Reset")
        if retry_after:
            reset_ts = int(time.time()) + int(retry_after)
        elif x_reset:
            reset_ts = int(x_reset)
        raise RateLimitError(
            f"GitHub rate limit exceeded (HTTP {resp.status_code})",
            reset_at=reset_ts,
        )
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None and int(remaining) == 0:
        x_reset = resp.headers.get("X-RateLimit-Reset")
        raise RateLimitError(
            "GitHub rate limit exhausted",
            reset_at=int(x_reset) if x_reset else None,
        )


def fetch_repo_metrics(repo_full_name: str, token: Optional[str] = None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Trend2Biz/0.2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repo_url = f"https://api.github.com/repos/{repo_full_name}"
    resp = httpx.get(repo_url, timeout=20.0, headers=headers)
    _check_rate_limit(resp)
    if resp.status_code >= 400:
        raise GithubMetricsError(f"GitHub API error for {repo_full_name}: {resp.status_code}")
    data = resp.json()

    # Contributor stats: GitHub returns 202 while computing; retry up to 3 times.
    pushes_url = f"https://api.github.com/repos/{repo_full_name}/stats/contributors"
    contributors_90d = None
    commits_30d = None
    commits_90d = None

    for attempt in range(3):
        contrib_resp = httpx.get(pushes_url, timeout=20.0, headers=headers)
        _check_rate_limit(contrib_resp)
        if contrib_resp.status_code == 202:
            if attempt < 2:
                time.sleep(2 ** attempt)  # 1s, 2s
            continue
        if contrib_resp.status_code == 200 and isinstance(contrib_resp.json(), list):
            contributors = contrib_resp.json()
            now = datetime.now(timezone.utc)
            active = 0
            c30 = 0
            c90 = 0
            for contributor in contributors:
                weeks = contributor.get("weeks", [])
                weeks_sorted = [w for w in weeks if "w" in w and "c" in w]
                for w in weeks_sorted:
                    week_ts = datetime.fromtimestamp(w["w"], tz=timezone.utc)
                    delta_days = (now - week_ts).days
                    if delta_days <= 30:
                        c30 += int(w.get("c", 0))
                    if delta_days <= 90:
                        c90 += int(w.get("c", 0))
                if any(
                    (now - datetime.fromtimestamp(w.get("w", 0), tz=timezone.utc)).days <= 90
                    and w.get("c", 0) > 0
                    for w in weeks_sorted
                ):
                    active += 1
            contributors_90d = active
            commits_30d = c30
            commits_90d = c90
        break  # Non-202 response (success or error) — stop retrying

    return {
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "watchers": data.get("subscribers_count") or data.get("watchers_count"),
        "open_issues": data.get("open_issues_count"),
        "commits_30d": commits_30d,
        "commits_90d": commits_90d,
        "contributors_90d": contributors_90d,
        "license_spdx": (data.get("license") or {}).get("spdx_id"),
        "description": data.get("description"),
        "primary_language": data.get("language"),
        "created_at_github": data.get("created_at"),
        "updated_at_github": data.get("updated_at"),
        "pushed_at_github": data.get("pushed_at"),
    }
