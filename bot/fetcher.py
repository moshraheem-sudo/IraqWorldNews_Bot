"""HTTP fetching layer with retries and connection management."""

from __future__ import annotations

import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


def create_http_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=1,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }
    )
    return session


def fetch_url(session: requests.Session, url: str, timeout: int = 10) -> Optional[str]:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        logger.warning("Fetch failed for %s (Status: %d)", url, resp.status_code)
        return None
    except requests.RequestException as e:
        logger.error("Network error fetching %s: %s", url, e)
        return None
