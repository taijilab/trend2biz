from __future__ import annotations

import base64
import re
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
    bus_factor_top1_share = None
    top_maintainers_90d: list[dict] = []

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
            contrib_90d_by_user: list[tuple[str, int]] = []
            for contributor in contributors:
                weeks = contributor.get("weeks", [])
                weeks_sorted = [w for w in weeks if "w" in w and "c" in w]
                c90_user = 0
                for w in weeks_sorted:
                    week_ts = datetime.fromtimestamp(w["w"], tz=timezone.utc)
                    delta_days = (now - week_ts).days
                    if delta_days <= 30:
                        c30 += int(w.get("c", 0))
                    if delta_days <= 90:
                        wc = int(w.get("c", 0))
                        c90 += wc
                        c90_user += wc
                if any(
                    (now - datetime.fromtimestamp(w.get("w", 0), tz=timezone.utc)).days <= 90
                    and w.get("c", 0) > 0
                    for w in weeks_sorted
                ):
                    active += 1
                if c90_user > 0:
                    author = contributor.get("author") or {}
                    login = author.get("login") or "unknown"
                    contrib_90d_by_user.append((login, c90_user))
            contributors_90d = active
            commits_30d = c30
            commits_90d = c90
            if c90 > 0 and contrib_90d_by_user:
                contrib_90d_by_user.sort(key=lambda x: x[1], reverse=True)
                bus_factor_top1_share = contrib_90d_by_user[0][1] / c90
                top_maintainers_90d = [
                    {
                        "login": login,
                        "commits_90d": commits,
                        "share_90d": round(commits / c90, 4),
                        "profile_url": f"https://github.com/{login}" if login != "unknown" else "",
                    }
                    for login, commits in contrib_90d_by_user[:5]
                ]
        break  # Non-202 response (success or error) — stop retrying

    owner = data.get("owner") or {}
    return {
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "watchers": data.get("subscribers_count") or data.get("watchers_count"),
        "open_issues": data.get("open_issues_count"),
        "commits_30d": commits_30d,
        "commits_90d": commits_90d,
        "contributors_90d": contributors_90d,
        "bus_factor_top1_share": bus_factor_top1_share,
        "top_maintainers_90d": top_maintainers_90d,
        "license_spdx": (data.get("license") or {}).get("spdx_id"),
        "description": data.get("description"),
        "primary_language": data.get("language"),
        "created_at_github": data.get("created_at"),
        "updated_at_github": data.get("updated_at"),
        "pushed_at_github": data.get("pushed_at"),
        "owner_login": owner.get("login"),
        "owner_type": owner.get("type"),  # "User" | "Organization"
    }


def fetch_star_history(
    repo_full_name: str,
    token: Optional[str] = None,
    max_samples: int = 30,
) -> list[tuple[str, int]]:
    """Fetch star growth history using the same technique as star-history.com.

    Samples evenly across all stargazer pages (GitHub caps at 400 pages × 100 = 40k
    stargazers). Returns a sorted list of (date_str, cumulative_stars) pairs.
    Returns an empty list on failure or when the repo has no stars.
    """
    star_headers = {
        "Accept": "application/vnd.github.v3.star+json",
        "User-Agent": "Trend2Biz/0.2",
    }
    json_headers = {"Accept": "application/vnd.github+json", "User-Agent": "Trend2Biz/0.2"}
    if token:
        star_headers["Authorization"] = f"Bearer {token}"
        json_headers["Authorization"] = f"Bearer {token}"

    # Step 1: get total star count
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo_full_name}",
            timeout=15.0,
            headers=json_headers,
        )
        _check_rate_limit(resp)
        if resp.status_code >= 400:
            return []
        total_stars: int = resp.json().get("stargazers_count", 0)
    except Exception:
        return []

    if total_stars == 0:
        return []

    # Step 2: determine pages to sample
    per_page = 100
    total_pages = min((total_stars + per_page - 1) // per_page, 400)  # GitHub max 400 pages

    if total_pages == 0:
        return []

    if total_pages <= max_samples:
        sample_pages = list(range(1, total_pages + 1))
    else:
        step = (total_pages - 1) / (max_samples - 1)
        sample_pages = sorted({round(1 + i * step) for i in range(max_samples)})
        if 1 not in sample_pages:
            sample_pages = [1] + sample_pages
        if total_pages not in sample_pages:
            sample_pages = sample_pages + [total_pages]

    # Step 3: fetch each sampled page and record date + cumulative count
    results: list[tuple[str, int]] = []
    for page in sample_pages:
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{repo_full_name}/stargazers"
                f"?per_page={per_page}&page={page}",
                timeout=15.0,
                headers=star_headers,
            )
            _check_rate_limit(r)
            if r.status_code >= 400:
                continue
            items = r.json()
            if not items:
                continue
            starred_at: str = items[-1].get("starred_at", "")
            if not starred_at:
                continue
            date_str = starred_at[:10]
            cumulative = (page - 1) * per_page + len(items)
            results.append((date_str, cumulative))
        except RateLimitError:
            break  # stop sampling, return what we have
        except Exception:
            continue

    if not results:
        return []

    results.sort(key=lambda x: x[0])

    # Deduplicate: keep highest cumulative count per date
    seen: dict[str, int] = {}
    for date_str, count in results:
        seen[date_str] = max(seen.get(date_str, 0), count)

    return sorted(seen.items())


def fetch_readme(repo_full_name: str, token: Optional[str] = None, max_chars: int = 4000) -> Optional[str]:
    """Fetch and return plain-text README content for a repo (up to max_chars).

    Returns None if the repo has no README or the request fails.
    Strips markdown images, badge lines, and HTML comments to reduce noise.
    """
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Trend2Biz/0.2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{repo_full_name}/readme"
    try:
        resp = httpx.get(url, timeout=15.0, headers=headers)
    except Exception:
        return None

    if resp.status_code == 404:
        return None
    _check_rate_limit(resp)
    if resp.status_code >= 400:
        return None

    payload = resp.json()
    encoding = payload.get("encoding", "")
    raw_content = payload.get("content", "")
    if encoding == "base64":
        try:
            text = base64.b64decode(raw_content).decode("utf-8", errors="replace")
        except Exception:
            return None
    else:
        text = raw_content

    # Strip noise: HTML comments, badge lines ([![...](...)]), pure image lines
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"^\s*\[!\[.*?\]\(.*?\)\]\(.*?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*!\[.*?\]\(.*?\)\s*$", "", text, flags=re.MULTILINE)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()[:max_chars] or None
