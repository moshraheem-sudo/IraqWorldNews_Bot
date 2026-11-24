"""Unit tests for Iraq & World News Bot."""

import unittest
from pathlib import Path

from bot.config import SourceConfig, load_app_config
from bot.parser import NewsItem, derive_title, parse_rss_feed, parse_telegram_web, parse_x_list
from bot.publisher import normalize_title_for_dedup
from bot.storage import StorageManager
from bot.telegram_client import format_news_post


class TestBotComponents(unittest.TestCase):
    def test_derive_title(self):
        text = "بغداد - واع\nأعلنت وزارة الداخلية اليوم..."
        title = derive_title(text)
        self.assertEqual(title, "بغداد - واع")

    def test_format_news_post(self):
        item = NewsItem(
            id="tg:test:1",
            source_name="وكالة الأنباء العراقية (واع)",
            source_username="inainaiq",
            category="أخبار العراق",
            hashtag="العراق",
            title="خبر عاجل من بغداد",
            content="تفاصيل الخبر الهام من بغداد...",
            original_url="https://t.me/inainaiq/1",
            image_url=None,
            published_at="2026-11-23T20:00:00Z",
        )
        post = format_news_post(item)
        self.assertIn("وكالة الأنباء العراقية (واع)", post)
        self.assertIn("خبر عاجل من بغداد", post)
        self.assertIn("#العراق", post)
        self.assertIn("https://t.me/inainaiq/1", post)

    def test_storage_deduplication(self, tmp_path=None):
        test_file = Path("test_sent.json")
        try:
            storage = StorageManager(test_file)
            self.assertFalse(storage.is_sent("tg:inainaiq:100"))
            storage.mark_sent("tg:inainaiq:100")
            self.assertTrue(storage.is_sent("tg:inainaiq:100"))
            storage.save()

            # Reload and check persistence
            reloaded = StorageManager(test_file)
            self.assertTrue(reloaded.is_sent("tg:inainaiq:100"))
            self.assertEqual(reloaded.data["total_sent"], 1)
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_title_deduplication(self):
        t1 = "عاجل: وزارة التربية تعلن موعد الامتحانات النهائية!"
        t2 = "وزارة التربية تعلن موعد الامتحانات النهائية لجميع المراحل"
        t3 = "عاجل | زلزال بقوة 5 درجات يضرب جنوب تركيا"
        k1 = normalize_title_for_dedup(t1)
        k2 = normalize_title_for_dedup(t2)
        k3 = normalize_title_for_dedup(t3)
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_rss_parser(self):
        sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test RSS</title>
                <item>
                    <title>عاجل: خبر سياسي واقتصادي</title>
                    <link>https://example.com/news/123</link>
                    <description>تفاصيل التقرير المنشور هنا</description>
                    <guid>https://example.com/news/123</guid>
                    <pubDate>Mon, 23 Nov 2026 18:00:00 +0000</pubDate>
                </item>
            </channel>
        </rss>
        """
        source = SourceConfig(
            id="test_rss",
            source_type="rss",
            name="موقع تجريبي",
            username="test_rss",
            url="https://example.com/rss",
            category="أخبار عربية وعالمية",
            hashtag="عالمي",
        )
        items = parse_rss_feed(sample_rss, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "عاجل: خبر سياسي واقتصادي")
        self.assertEqual(items[0].original_url, "https://example.com/news/123")

    def test_x_list_parser(self):
        sample_x_html = '''<html><body>
        <script id="__NEXT_DATA__" type="application/json">
        {
            "props": {
                "pageProps": {
                    "timeline": {
                        "entries": [
                            {
                                "content": {
                                    "tweet": {
                                        "id_str": "1234567890123456789",
                                        "text": "عاجل: تصريح هام لوزير الخارجية حول الأوضاع الإقليمية",
                                        "user": {"screen_name": "AlArabiya", "name": "العربية عاجل"},
                                        "created_at": "Tue Nov 24 16:00:00 +0000 2026",
                                        "photos": [{"url": "https://pbs.twimg.com/media/example.jpg"}]
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }
        </script></body></html>'''
        source = SourceConfig(
            id="x_test",
            source_type="x_list",
            name="منصة X",
            username="test_list",
            url="https://x.com/i/lists/123456",
            category="أخبار عربية وعالمية",
        )
        items = parse_x_list(sample_x_html, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "x:1234567890123456789")
        self.assertEqual(items[0].image_url, "https://pbs.twimg.com/media/example.jpg")
        self.assertIn("تصريح هام", items[0].content)


if __name__ == "__main__":
    unittest.main()
