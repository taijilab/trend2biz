from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx
from bs4 import BeautifulSoup


@dataclass
class TrendingItemParsed:
    rank: int
    repo_full_name: str
    repo_url: str
    description: Optional[str]
    primary_language: Optional[str]
    stars_total_hint: Optional[int]
    forks_total_hint: Optional[int]
    stars_delta_window: Optional[int]


def _extract_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    normalized = text.replace(",", "")
    m = re.search(r"(\d+)", normalized)
    return int(m.group(1)) if m else None


def fetch_trending_html(since: str, language: str = "all", spoken: Optional[str] = None) -> str:
    lang_part = "" if language == "all" else f"/{language}"
    url = f"https://github.com/trending{lang_part}?since={since}"
    if spoken:
        url += f"&spoken_language_code={spoken}"
    resp = httpx.get(url, timeout=20.0, headers={"User-Agent": "Trend2Biz/0.1"})
    resp.raise_for_status()
    return resp.text


def parse_trending_html(html: str) -> list[TrendingItemParsed]:
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.Box-row")
    items: list[TrendingItemParsed] = []

    for idx, art in enumerate(articles, start=1):
        h2 = art.select_one("h2 a")
        if not h2:
            continue
        href = (h2.get("href") or "").strip()
        repo_full_name = href.strip("/").replace(" ", "")
        repo_url = f"https://github.com{href}"

        desc_el = art.select_one("p")
        desc = desc_el.get_text(" ", strip=True) if desc_el else None

        lang_el = art.select_one("span[itemprop='programmingLanguage']")
        lang = lang_el.get_text(strip=True) if lang_el else None

        star_link = art.select_one("a[href$='/stargazers']")
        fork_link = art.select_one("a[href$='/forks']")
        stars_total = _extract_int(star_link.get_text(strip=True) if star_link else None)
        forks_total = _extract_int(fork_link.get_text(strip=True) if fork_link else None)

        delta = None
        for span in art.select("span"):
            text = span.get_text(" ", strip=True)
            if "star" in text.lower() and re.search(r"\d", text):
                delta = _extract_int(text)
                if delta is not None:
                    break

        items.append(
            TrendingItemParsed(
                rank=idx,
                repo_full_name=repo_full_name,
                repo_url=repo_url,
                description=desc,
                primary_language=lang,
                stars_total_hint=stars_total,
                forks_total_hint=forks_total,
                stars_delta_window=delta,
            )
        )

    return items
