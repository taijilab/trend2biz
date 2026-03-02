"""
company_research.py — 自动抓取组织/公司背景数据

数据源优先级：
1. GitHub Org API     → 官网 URL、GitHub Verified、描述、Twitter
2. 官网 HTTP 爬取     → 定价线索、团队规模、产品介绍
3. DuckDuckGo 搜索   → 融资新闻、公司近期动态
"""
from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

_HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_TIMEOUT = 10


# ── 1. GitHub Org API ──────────────────────────────────────────────────────────

def fetch_org_github_info(org_login: str, token: Optional[str] = None) -> dict:
    """Fetch org metadata from GitHub /orgs/{org} endpoint."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(f"https://api.github.com/orgs/{org_login}", headers=headers)
        if r.status_code != 200:
            return {}
        d = r.json()
        website = d.get("blog") or ""
        # Normalize website URL
        if website and not website.startswith("http"):
            website = "https://" + website
        return {
            "website":        website,
            "description":    d.get("description") or "",
            "location":       d.get("location") or "",
            "email":          d.get("email") or "",
            "github_verified": bool(d.get("is_verified")),
            "twitter":        d.get("twitter_username") or "",
            "followers":      d.get("followers", 0),
            "public_repos":   d.get("public_repos", 0),
        }
    except Exception:
        return {}


# ── 2. 官网爬取 ────────────────────────────────────────────────────────────────

def scrape_website(url: str) -> dict:
    """Best-effort HTTP scrape of official website for pricing / job hints."""
    if not url or not url.startswith("http"):
        return {}
    result: dict = {"website_reachable": False}
    try:
        with httpx.Client(
            timeout=_TIMEOUT,
            headers=_HEADERS_BROWSER,
            follow_redirects=True,
        ) as client:
            r = client.get(url)
        if r.status_code >= 400:
            return result
        result["website_reachable"] = True
        text = r.text[:80_000]   # limit parsing to 80k chars

        # ── pricing hints ──────────────────────────────────────────────────
        price_matches = re.findall(
            r"\$\s*(\d{1,5}(?:\.\d{1,2})?)\s*(?:/\s*(?:mo|month|yr|year|user|seat))?",
            text, re.I,
        )
        if price_matches:
            nums = sorted({float(p) for p in price_matches})
            lo = nums[0]
            result["pricing_hint"] = (
                f"从 ${lo:.0f}/月起" if lo < 500 else f"企业定制（最低 ${lo:.0f}）"
            )

        # ── job / open roles hint ──────────────────────────────────────────
        job_match = re.search(
            r"(\d{1,4})\s*(?:open\s*)?(?:position|role|job|opening|vacancies?)",
            text, re.I,
        )
        if job_match:
            result["open_roles"] = f"{job_match.group(1)} 个在招岗位"
        elif re.search(r"we.re hiring|join our team|careers|open roles", text, re.I):
            result["open_roles"] = "招聘中（具体数量见官网）"

        # ── try to detect pricing page ─────────────────────────────────────
        if re.search(r'href=["\'][^"\']*pric', text, re.I):
            pricing_urls = re.findall(r'href=["\']([^"\']*pric[^"\']*)["\']', text, re.I)
            if pricing_urls:
                # Try to fetch the pricing page as well
                pricing_url = pricing_urls[0]
                if not pricing_url.startswith("http"):
                    pricing_url = urljoin(url, pricing_url)
                try:
                    with httpx.Client(
                        timeout=_TIMEOUT,
                        headers=_HEADERS_BROWSER,
                        follow_redirects=True,
                    ) as client2:
                        r2 = client2.get(pricing_url)
                    ptext = r2.text[:40_000]
                    p2 = re.findall(
                        r"\$\s*(\d{1,5}(?:\.\d{1,2})?)\s*(?:/\s*(?:mo|month|yr|year|user|seat))?",
                        ptext, re.I,
                    )
                    if p2:
                        nums2 = sorted({float(x) for x in p2})
                        result["pricing_hint"] = f"从 ${nums2[0]:.0f}/月起（pricing 页）"
                except Exception:
                    pass

    except Exception:
        pass
    return result


# ── 3. DuckDuckGo HTML 搜索（新闻 / 融资） ────────────────────────────────────

def search_company_news(company_name: str, extra: str = "funding OR investment OR revenue") -> list[dict]:
    """
    Lightweight news search via DuckDuckGo HTML endpoint.
    Returns list of {title, url, snippet} dicts.
    """
    query = quote_plus(f"{company_name} {extra}")
    url = f"https://html.duckduckgo.com/html/?q={query}&t=h_&ia=web"
    results: list[dict] = []
    try:
        with httpx.Client(timeout=12, headers=_HEADERS_BROWSER) as client:
            r = client.get(url)
        if r.status_code >= 400:
            return results
        text = r.text
        # Extract result blocks
        blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            text, re.DOTALL,
        )
        for href, raw_title, raw_snip in blocks[:8]:
            title   = re.sub(r"<[^>]+>", "", raw_title).strip()
            snippet = re.sub(r"<[^>]+>", "", raw_snip).strip()
            # DuckDuckGo wraps real URLs in redirect; extract from uddg= param
            real_url = href
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                from urllib.parse import unquote
                real_url = unquote(m.group(1))
            if title:
                results.append({"title": title, "url": real_url, "snippet": snippet})
    except Exception:
        pass
    return results


def _parse_funding_from_news(news_items: list[dict]) -> list[dict]:
    """
    Try to extract structured funding round data from news snippets.
    Very heuristic — best-effort only.
    """
    rounds = []
    round_kw = r"\b(seed|pre[- ]?seed|series [a-e]|angel|ipo|spac|growth equity)\b"
    amount_kw = r"\$(\d+(?:\.\d+)?)\s*(m|b|million|billion)\b"
    for item in news_items:
        text = (item.get("title", "") + " " + item.get("snippet", "")).lower()
        rm = re.search(round_kw, text, re.I)
        am = re.search(amount_kw, text, re.I)
        if rm:
            amount = ""
            if am:
                val, unit = am.group(1), am.group(2).lower()
                amount = f"${val}{'B' if unit in ('b','billion') else 'M'}"
            rounds.append({
                "round":     rm.group(1).title(),
                "amount":    amount or "—",
                "date":      "",
                "investors": "",
                "source":    item.get("url", ""),
            })
    return rounds[:4]


# ── 4. 主入口 ──────────────────────────────────────────────────────────────────

def research_org(
    org_login: str,
    github_token: Optional[str] = None,
    existing_expl: Optional[dict] = None,
) -> dict:
    """
    Orchestrate all data sources for an Organization.
    Returns a dict of fields to merge into biz.explanations.

    Only fetches data that isn't already in existing_expl.
    """
    expl = dict(existing_expl or {})

    # ── GitHub org info ───────────────────────────────────────────────────
    github_data = fetch_org_github_info(org_login, token=github_token)
    ci = dict(expl.get("company_info") or {})

    if github_data:
        if not ci.get("website") and github_data.get("website"):
            ci["website"] = github_data["website"]
        if ci.get("github_verified") is None and "github_verified" in github_data:
            ci["github_verified"] = github_data["github_verified"]
        if not ci.get("description") and github_data.get("description"):
            ci["description"] = github_data["description"]
        if not ci.get("location") and github_data.get("location"):
            ci["location"] = github_data["location"]
        if not ci.get("twitter") and github_data.get("twitter"):
            ci["twitter"] = f"@{github_data['twitter']}"
        if not ci.get("followers"):
            ci["followers"] = github_data.get("followers", 0)

    # ── website scraping ──────────────────────────────────────────────────
    website_url = ci.get("website") or ""
    if website_url and not ci.get("pricing_hint") and not expl.get("revenue_info", {}).get("pricing"):
        site_data = scrape_website(website_url)
        if site_data.get("pricing_hint"):
            rev = dict(expl.get("revenue_info") or {})
            if not rev.get("pricing"):
                rev["pricing"] = site_data["pricing_hint"]
            expl["revenue_info"] = rev
        if site_data.get("open_roles") and not ci.get("open_roles"):
            ci["open_roles"] = site_data["open_roles"]

    expl["company_info"] = ci

    # ── news search for funding / strategic updates ────────────────────────
    if not expl.get("funding_rounds"):
        news = search_company_news(org_login, extra="funding investment raised series")
        parsed = _parse_funding_from_news(news)
        if parsed:
            expl["funding_rounds"] = parsed
        # Store raw news for strategic updates section
        if news and not expl.get("strategic_updates"):
            snippets = [
                f"**[{n['title']}]({n['url']})** — {n['snippet']}"
                for n in news[:4] if n.get("title")
            ]
            if snippets:
                expl["strategic_updates"] = "\n\n".join(snippets)

    # ── recent product news ───────────────────────────────────────────────
    if not expl.get("commercial_landscape"):
        prod_news = search_company_news(org_login, extra="product launch customer enterprise")
        if prod_news:
            snippets = [
                f"- [{n['title']}]({n['url']})" for n in prod_news[:4] if n.get("title")
            ]
            if snippets:
                expl["commercial_landscape"] = (
                    "近期新闻（自动抓取，仅供参考）：\n" + "\n".join(snippets)
                )

    return expl
