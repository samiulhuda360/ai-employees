import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

MODULE_PATH = Path(__file__).resolve().parents[1] / "youtube_signal_collector.py"
spec = importlib.util.spec_from_file_location("youtube_signal_collector", MODULE_PATH)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def channel_page_html():
    initial_data = {
        "contents": [
            {
                "lockupViewModel": {
                    "contentId": "abcdefghijk",
                    "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                    "metadata": {
                        "lockupMetadataViewModel": {
                            "title": {"content": "Fallback video"},
                            "metadata": {
                                "contentMetadataViewModel": {
                                    "metadataRows": [
                                        {
                                            "metadataParts": [
                                                {"text": {"content": "1.2K views"}},
                                                {"text": {"content": "13 hours ago"}},
                                            ]
                                        }
                                    ]
                                }
                            },
                        }
                    },
                }
            }
        ]
    }
    return ("<html><script>var ytInitialData = " + json.dumps(initial_data) + ";</script></html>").encode()


class FeedFallbackTests(unittest.TestCase):
    def test_rss_http_error_falls_back_to_channel_videos_page(self):
        channel = {
            "id": "UC1234567890123456789012",
            "name": "Example Channel",
            "handle": "ExampleHandle",
        }
        feed_url = collector.FEED_URL.format(channel_id=channel["id"])
        page_url = collector.CHANNEL_VIDEOS_URL.format(handle=channel["handle"])

        def fake_fetch(url, timeout=20):
            if url == feed_url:
                raise HTTPError(url, 404, "Not Found", None, None)
            if url == page_url:
                return channel_page_html()
            raise AssertionError(f"unexpected URL: {url}")

        fixed_now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        with patch.object(collector, "fetch", side_effect=fake_fetch):
            with patch.object(collector, "utc_now", return_value=fixed_now):
                result = collector.read_feed(channel, 6)

        self.assertIsNone(result["error"])
        self.assertEqual(result["source"], "channel_page")
        self.assertEqual(result["feed_title"], "Example Channel")
        self.assertEqual(result["videos"][0]["video_id"], "abcdefghijk")
        self.assertEqual(result["videos"][0]["title"], "Fallback video")
        self.assertEqual(result["videos"][0]["views"], 1200)
        self.assertEqual(result["videos"][0]["published"], "2026-09-23T23:00:00+00:00")


class CompactMetadataTests(unittest.TestCase):
    def test_compact_channel_page_metadata_uses_accessibility_labels(self):
        initial_data = {
            "lockupViewModel": {
                "contentId": "DdCEmlAydcw",
                "metadata": {
                    "lockupMetadataViewModel": {
                        "title": {"content": "Compact video"},
                        "metadata": {
                            "contentMetadataViewModel": {
                                "metadataRows": [
                                    {
                                        "metadataParts": [
                                            {"text": {"content": "78K"}, "accessibilityLabel": "78 thousand views"},
                                            {"text": {"content": "12h ago"}, "accessibilityLabel": "12 hours ago"},
                                        ]
                                    }
                                ]
                            }
                        },
                    }
                },
            }
        }
        body = ("<script>var ytInitialData = " + json.dumps(initial_data) + ";</script>").encode()
        now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

        videos = collector.parse_channel_page(body, {"name": "AI Engineer"}, 6, now)

        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["views"], 78000)
        self.assertEqual(videos[0]["published"], "2026-09-24T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
