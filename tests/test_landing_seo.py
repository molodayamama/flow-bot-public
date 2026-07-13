"""Offline contracts for the public Photozhab search landing cluster."""
from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "deploy" / "photozhab"
PUBLIC_PAGES = (
    "index.html",
    "generaciya-kartinok.html",
    "generaciya-video.html",
    "nano-banana.html",
    "max-bot.html",
    "oferta.html",
    "privacy.html",
    "consent.html",
)
SEARCH_PAGES = PUBLIC_PAGES[:5]


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.h1_count = 0
        self.meta: dict[str, str] = {}
        self.links: list[dict[str, str]] = []
        self.hrefs: list[str] = []
        self.json_ld: list[str] = []
        self._json_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta":
            key = values.get("name") or values.get("property")
            if key:
                self.meta[key] = values.get("content", "")
        elif tag == "link":
            self.links.append(values)
        elif tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._json_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._json_parts is not None:
            self.json_ld.append("".join(self._json_parts))
            self._json_parts = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._json_parts is not None:
            self._json_parts.append(data)


def parse_page(name: str) -> HeadParser:
    parser = HeadParser()
    parser.feed((SITE / name).read_text(encoding="utf-8"))
    return parser


class LandingSeoTests(unittest.TestCase):
    def test_home_exposes_google_site_verification_in_head(self) -> None:
        page = parse_page("index.html")
        self.assertEqual(
            page.meta.get("google-site-verification"),
            "MpOJ7m9ATx7X2lWmJgJK8WfbfmsN-ajkSWTaETlAi6U",
        )

    def test_public_pages_have_unique_search_metadata(self) -> None:
        titles: set[str] = set()
        descriptions: set[str] = set()
        canonicals: set[str] = set()
        for name in PUBLIC_PAGES:
            page = parse_page(name)
            canonical = [
                item.get("href", "")
                for item in page.links
                if item.get("rel") == "canonical"
            ]
            self.assertGreaterEqual(len(page.title.strip()), 20, name)
            self.assertGreaterEqual(len(page.meta.get("description", "")), 70, name)
            self.assertIn("index", page.meta.get("robots", ""), name)
            self.assertEqual(page.h1_count, 1, name)
            self.assertEqual(len(canonical), 1, name)
            self.assertTrue(canonical[0].startswith("https://photozhab.ru/"), name)
            titles.add(page.title.strip())
            descriptions.add(page.meta["description"])
            canonicals.add(canonical[0])
        self.assertEqual(len(titles), len(PUBLIC_PAGES))
        self.assertEqual(len(descriptions), len(PUBLIC_PAGES))
        self.assertEqual(len(canonicals), len(PUBLIC_PAGES))

    def test_search_pages_cover_distinct_intents_and_social_cards(self) -> None:
        expected = {
            "index.html": ("Генерация картинок", "видео"),
            "generaciya-kartinok.html": ("Генерация картинок", "Nano Banana"),
            "generaciya-video.html": ("Генерация видео", "Veo"),
            "nano-banana.html": ("Nano Banana", "Pro"),
            "max-bot.html": ("MAX", "генерация картинок"),
        }
        for name, phrases in expected.items():
            source = (SITE / name).read_text(encoding="utf-8")
            page = parse_page(name)
            for phrase in phrases:
                self.assertIn(phrase.lower(), source.lower(), name)
            self.assertEqual(page.meta.get("og:site_name"), "Photozhab", name)
            self.assertTrue(page.meta.get("og:title"), name)
            self.assertTrue(page.meta.get("og:description"), name)
            self.assertTrue(page.meta.get("og:image", "").startswith("https://"), name)

    def test_home_json_ld_is_valid_and_truthful(self) -> None:
        page = parse_page("index.html")
        self.assertEqual(len(page.json_ld), 1)
        data = json.loads(page.json_ld[0])
        types = {item.get("@type") for item in data.get("@graph", [])}
        self.assertIn("WebSite", types)
        self.assertIn("SoftwareApplication", types)
        app = next(item for item in data["@graph"] if item.get("@type") == "SoftwareApplication")
        self.assertEqual(app["operatingSystem"], "Telegram, MAX")
        self.assertEqual(app["offers"]["price"], "0")

    def test_internal_links_resolve_to_versioned_static_files(self) -> None:
        for name in SEARCH_PAGES:
            page = parse_page(name)
            for href in page.hrefs:
                if not href.startswith("/") or href.startswith("/#"):
                    continue
                path = urlparse(href).path
                target = SITE / ("index.html" if path == "/" else path.lstrip("/"))
                self.assertTrue(target.is_file(), f"{name}: {href}")

    def test_robots_and_sitemap_match_public_files(self) -> None:
        robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("Disallow: /admin.html", robots)
        self.assertIn("Disallow: /api/", robots)
        self.assertIn("Sitemap: https://photozhab.ru/sitemap.xml", robots)
        root = ET.parse(SITE / "sitemap.xml").getroot()
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [node.text or "" for node in root.findall("sm:url/sm:loc", namespace)]
        self.assertEqual(len(locations), len(PUBLIC_PAGES))
        for location in locations:
            path = urlparse(location).path
            target = SITE / ("index.html" if path == "/" else path.lstrip("/"))
            self.assertTrue(target.is_file(), location)

    def test_admin_is_explicitly_noindex(self) -> None:
        admin = parse_page("admin.html")
        self.assertEqual(admin.meta.get("robots"), "noindex, nofollow, noarchive")


if __name__ == "__main__":
    unittest.main()
