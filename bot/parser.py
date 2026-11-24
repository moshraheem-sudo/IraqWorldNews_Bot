"""Parsers for public Telegram web previews and RSS feeds."""

from __future__ import annotations

import html
import json
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from bot.config import SourceConfig

BG_IMAGE_RE = re.compile(r"background-image\s*:\s*url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


@dataclass
class NewsItem:
    id: str
    source_name: str
    source_username: str
    category: str
    hashtag: str
    title: str
    content: str
    original_url: str
    image_url: Optional[str]
    published_at: str
    video_url: Optional[str] = None


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in raw.replace("\r\n", "\n").split("\n")]
    joined = "\n".join(lines).strip()
    return MULTI_NEWLINE_RE.sub("\n\n", joined)


def derive_title(content: str, max_len: int = 120) -> str:
    if not content:
        return ""
    for line in content.splitlines():
        candidate = line.strip()
        if candidate:
            if len(candidate) <= max_len:
                return candidate
            clipped = candidate[:max_len].rsplit(" ", 1)[0].strip()
            return (clipped if clipped else candidate[:max_len].strip()) + "…"
    return ""


def parse_telegram_web(html_content: str, source: SourceConfig) -> List[NewsItem]:
    """Parse public t.me/s/<channel> HTML."""
    soup = BeautifulSoup(html_content, "lxml")
    items: List[NewsItem] = []

    message_nodes = soup.select(".tgme_widget_message[data-post]")
    for node in message_nodes:
        data_post = str(node.get("data-post") or "").strip()
        if "/" not in data_post:
            continue

        parts = data_post.split("/", 1)
        channel_user = parts[0].strip()
        msg_id_str = parts[1].strip()
        if not msg_id_str.isdigit():
            continue

        post_id = f"tg:{channel_user.lower()}:{msg_id_str}"
        original_url = f"https://t.me/{channel_user}/{msg_id_str}"

        # Extract text
        text_node = node.select_one(".tgme_widget_message_text")
        raw_text = text_node.get_text("\n", strip=True) if text_node else ""
        text = clean_text(raw_text)

        # Extract image if present
        image_url = None
        photo_node = node.select_one(".tgme_widget_message_photo_wrap")
        if photo_node:
            style = photo_node.get("style", "")
            match = BG_IMAGE_RE.search(style)
            if match:
                img_src = match.group(1).strip()
                if img_src.startswith("//"):
                    img_src = "https:" + img_src
                image_url = img_src

        # Date
        time_node = node.select_one("time[datetime]")
        published_at = (
            time_node.get("datetime")
            if time_node and time_node.get("datetime")
            else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )

        title = derive_title(text)

        # If post has neither text nor image, skip
        if not text and not image_url:
            continue

        items.append(
            NewsItem(
                id=post_id,
                source_name=source.name,
                source_username=channel_user,
                category=source.category,
                hashtag=source.hashtag,
                title=title,
                content=text,
                original_url=original_url,
                image_url=image_url,
                published_at=published_at,
            )
        )

    # Return newest last or keep order
    return items[-source.max_posts :]


def parse_rss_feed(xml_content: str, source: SourceConfig) -> List[NewsItem]:
    """Parse RSS or Atom XML feed."""
    items: List[NewsItem] = []
    try:
        root = ET.fromstring(xml_content.encode("utf-8"))
    except Exception as e:
        # Fallback to BeautifulSoup if XML is slightly malformed
        try:
            soup = BeautifulSoup(xml_content, "xml")
            entries = soup.find_all(["item", "entry"])
            for entry in entries[: source.max_posts]:
                title = clean_text(entry.find("title").get_text(strip=True) if entry.find("title") else "")
                link_node = entry.find("link")
                link = ""
                if link_node:
                    link = link_node.get("href") or link_node.get_text(strip=True)
                desc_node = entry.find(["description", "summary", "content"])
                desc_raw = desc_node.get_text(strip=True) if desc_node else ""
                desc_clean = clean_text(BeautifulSoup(desc_raw, "html.parser").get_text("\n", strip=True))

                guid_node = entry.find(["guid", "id"])
                guid = guid_node.get_text(strip=True) if guid_node else (link or title)
                stable_hash = f"{zlib.crc32(guid.encode('utf-8')) & 0x7FFFFFFF:08x}"
                post_id = f"rss:{source.id}:{stable_hash}"

                if not title and not desc_clean:
                    continue

                items.append(
                    NewsItem(
                        id=post_id,
                        source_name=source.name,
                        source_username=source.username,
                        category=source.category,
                        hashtag=source.hashtag,
                        title=title or derive_title(desc_clean),
                        content=desc_clean or title,
                        original_url=link,
                        image_url=None,
                        published_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    )
                )
            return items
        except Exception:
            return []

    # Standard XML parsing
    channel_items = root.findall(".//item")
    if not channel_items:
        channel_items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    for item in channel_items[: source.max_posts]:
        title_el = item.find("title")
        if title_el is None:
            title_el = item.find("{http://www.w3.org/2005/Atom}title")
        title = clean_text(title_el.text or "") if title_el is not None else ""

        link_el = item.find("link")
        if link_el is None:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
        link = ""
        if link_el is not None:
            link = link_el.attrib.get("href") or (link_el.text or "").strip()

        desc_el = item.find("description")
        if desc_el is None:
            desc_el = item.find("{http://www.w3.org/2005/Atom}summary")
        if desc_el is None:
            desc_el = item.find("{http://www.w3.org/2005/Atom}content")
        raw_desc = (desc_el.text or "") if desc_el is not None else ""
        clean_desc = clean_text(BeautifulSoup(raw_desc, "html.parser").get_text("\n", strip=True))

        # Check for media/enclosure image
        image_url = None
        enclosure = item.find("enclosure")
        if enclosure is not None and "image" in enclosure.attrib.get("type", ""):
            image_url = enclosure.attrib.get("url")
        if not image_url:
            media_content = item.find("{http://search.yahoo.com/mrss/}content")
            if media_content is not None and media_content.attrib.get("url"):
                image_url = media_content.attrib.get("url")

        guid_el = item.find("guid")
        if guid_el is None:
            guid_el = item.find("{http://www.w3.org/2005/Atom}id")
        guid = (guid_el.text or "") if guid_el is not None else (link or title)

        stable_hash = f"{zlib.crc32(guid.encode('utf-8')) & 0x7FFFFFFF:08x}"
        post_id = f"rss:{source.id}:{stable_hash}"

        pubdate_el = item.find("pubDate")
        if pubdate_el is None:
            pubdate_el = item.find("{http://www.w3.org/2005/Atom}published")
        pubdate_str = pubdate_el.text if pubdate_el is not None else None
        try:
            pub_dt = parsedate_to_datetime(pubdate_str) if pubdate_str else datetime.now(timezone.utc)
            published_at = pub_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            published_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        if not title and not clean_desc:
            continue

        items.append(
            NewsItem(
                id=post_id,
                source_name=source.name,
                source_username=source.username,
                category=source.category,
                hashtag=source.hashtag,
                title=title or derive_title(clean_desc),
                content=clean_desc or title,
                original_url=link,
                image_url=image_url,
                published_at=published_at,
            )
        )

    return items


def parse_x_list(html_content: str, source: SourceConfig, filter_username: Optional[str] = None) -> List[NewsItem]:
    """Parse public X / Twitter List or Account HTML embed syndication."""
    match = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_content, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except Exception:
        return []

    entries = (
        data.get("props", {})
        .get("pageProps", {})
        .get("timeline", {})
        .get("entries", [])
    )
    items: List[NewsItem] = []
    
    # Target username normalization for filtering
    target_users = set()
    if filter_username:
        f_low = filter_username.lower().strip().lstrip("@")
        target_users.add(f_low)
        if f_low in ("aljazeera_now", "ajabreaking", "ajarabic"):
            target_users.update(["aljazeera_now", "ajabreaking", "ajarabic"])
        elif f_low in ("skynewsarabia_b", "skynewsarabia"):
            target_users.update(["skynewsarabia_b", "skynewsarabia"])
        elif f_low in ("alhadath", "alhadath_ymn"):
            target_users.update(["alhadath", "alhadath_ymn"])

    for entry in entries:
        tweet = entry.get("content", {}).get("tweet")
        if not isinstance(tweet, dict):
            continue
        tweet_id = str(tweet.get("id_str") or "").strip()
        if not tweet_id.isdigit():
            continue

        user = tweet.get("user") or {}
        screen_name = str(user.get("screen_name") or "X").strip()
        user_name = str(user.get("name") or "منصة X").strip()

        # If filtering by specific account
        if target_users and screen_name.lower() not in target_users:
            continue

        raw_text = tweet.get("text") or ""
        clean_tweet_text = clean_text(raw_text)

        # Media extraction (Photos & High Quality MP4 Videos)
        image_url = None
        video_url = None

        ext_media = (tweet.get("extended_entities") or {}).get("media") or (tweet.get("entities") or {}).get("media") or []
        for m in ext_media:
            short_media_url = str(m.get("url") or "")
            if short_media_url:
                raw_text = raw_text.replace(short_media_url, "").strip()

            m_type = str(m.get("type") or "photo")
            thumb_url = str(m.get("media_url_https") or m.get("media_url") or "").strip()

            if m_type == "photo":
                if not image_url and thumb_url:
                    image_url = thumb_url
            elif m_type in ("video", "animated_gif"):
                variants = (m.get("video_info") or {}).get("variants") or []
                mp4s = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
                mp4s.sort(key=lambda x: int(x.get("bitrate") or 0), reverse=True)
                if mp4s and not video_url:
                    video_url = str(mp4s[0]["url"])
                if thumb_url and not image_url:
                    image_url = thumb_url

        if not image_url:
            photos = tweet.get("photos", [])
            if photos and isinstance(photos, list):
                first_photo = photos[0]
                if isinstance(first_photo, dict) and first_photo.get("url"):
                    image_url = first_photo.get("url")

        clean_tweet_text = clean_text(raw_text)

        # Skip retweets without text or empty tweets
        if not clean_tweet_text and not image_url and not video_url:
            continue

        created_at_raw = tweet.get("created_at")
        published_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if created_at_raw:
            try:
                dt = datetime.strptime(created_at_raw.strip(), "%a %b %d %H:%M:%S %z %Y")
                published_at = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            except Exception:
                pass

        title = derive_title(clean_tweet_text)
        original_url = f"https://x.com/{screen_name}/status/{tweet_id}"
        post_id = f"x:{tweet_id}"

        # Determine hashtag
        screen_lower = screen_name.lower()
        if source.source_type == "x_account" and source.hashtag:
            hashtag = source.hashtag
        elif "skynews" in screen_lower:
            hashtag = "سكاي_نيوز"
        elif "hadath" in screen_lower:
            hashtag = "الحدث"
        elif "jazeera" in screen_lower or screen_lower.startswith("aja"):
            hashtag = "الجزيرة_عاجل"
        elif "arabiya" in screen_lower:
            hashtag = "العربية"
        else:
            hashtag = "منصة_X"

        source_display_name = source.name if source.source_type == "x_account" else f"{user_name} (@{screen_name})"

        items.append(
            NewsItem(
                id=post_id,
                source_name=source_display_name,
                source_username=screen_name,
                category=source.category,
                hashtag=hashtag,
                title=title,
                content=clean_tweet_text,
                original_url=original_url,
                image_url=image_url,
                published_at=published_at,
                video_url=video_url,
            )
        )
    return items[-source.max_posts:]
