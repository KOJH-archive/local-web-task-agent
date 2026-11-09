from __future__ import annotations

from typing import Any

import feedparser
import requests


class ReadOnlyFetcher:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def get(self, url: str, max_chars: int = 50_000) -> dict[str, Any]:
        response = requests.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": "WebTaskAgent/0.1 (+local user agent)"},
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        text = response.text[:max_chars]
        return {
            "url": response.url,
            "status": response.status_code,
            "content_type": content_type,
            "text": text,
            "truncated": len(response.text) > max_chars,
        }

    def rss(self, url: str, limit: int = 30) -> dict[str, Any]:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        return {
            "url": url,
            "title": parsed.feed.get("title", ""),
            "entries": [
                {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "summary": entry.get("summary", "")[:2000],
                }
                for entry in parsed.entries[:limit]
            ],
        }
