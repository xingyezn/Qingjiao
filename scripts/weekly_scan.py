#!/usr/bin/env python3
"""Scan registered official announcement pages and collect review candidates."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCES_FILE = ROOT / "data" / "sources.json"
PROJECTS_FILE = ROOT / "data" / "projects.json"
INBOX_FILE = ROOT / "data" / "inbox.json"
RELEVANT_TERMS = (
    "项目申报", "课题申报", "申报通知", "申报公告", "申报指南", "项目指南",
    "自然科学基金", "基础研究基金", "社科基金", "哲学社会科学", "教育科学",
    "教育科研", "人文社会科学", "博士后", "博新计划", "青年创新人才",
    "科研平台和项目", "联合基金", "专项申报",
)
YEAR_PATTERN = re.compile(r"(?:2025|2026|2027|2028)")
INTENT_TERMS = ("申报", "申请", "指南", "项目", "课题", "资助", "博士后")
SOURCE_WORKERS = 12


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            values = dict(attrs)
            self.current_href = values.get("href")
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href is not None:
            text = re.sub(r"\s+", " ", " ".join(self.current_text)).strip()
            if text:
                self.items.append((self.current_href, text))
            self.current_href = None
            self.current_text = []


def normalized_url(url: str) -> str:
    url, _fragment = urldefrag(url.strip())
    return url.rstrip("/")


def allowed_domain(url: str, source_url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    source_host = (urlparse(source_url).hostname or "").lower()
    return bool(host and source_host and (host == source_host or host.endswith("." + source_host)))


def fetch_page(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "QingjiaoWeeklyNoticeScan/1.0 (+https://github.com/xingyezn/Qingjiao)"})
    with urlopen(request, timeout=15) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        raw = response.read(5_000_000)
        return raw.decode(content_type, errors="replace").encode("utf-8")


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def scan_source(source: dict) -> tuple[int, list[dict], tuple[str, str] | None]:
    listing_url = source["url"]
    try:
        content = fetch_page(listing_url)
        parser = AnchorParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        candidates = []
        for href, title in parser.items:
            title = html.unescape(title)
            source_terms = tuple(source.get("keywords", [])) or RELEVANT_TERMS
            if len(title) < 8 or not any(term in title for term in source_terms):
                continue
            if not any(term in title for term in INTENT_TERMS) or not YEAR_PATTERN.search(title):
                continue
            target = normalized_url(urljoin(listing_url, href))
            if urlparse(target).scheme != "https" or not allowed_domain(target, listing_url):
                continue
            if target == normalized_url(listing_url):
                continue
            candidates.append({
                "region": source.get("region", "待分类"),
                "sourceHost": source.get("host", ""),
                "sourceId": source.get("id", ""),
                "listingUrl": listing_url,
                "title": title[:300],
                "noticeUrl": target,
                "discoveredAt": datetime.now(timezone.utc).date().isoformat(),
                "reviewStatus": "待人工核验",
                "reviewNotes": "确认主办单位、项目类别、发布日期、申报条件和截止日期后，再转入 projects.json。",
            })
        return 1, candidates, None
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        return 0, [], (source.get("host", source.get("id", listing_url)), str(error))


def main() -> int:
    source_data = read_json(SOURCES_FILE, {"sources": []})
    projects_data = read_json(PROJECTS_FILE, {"projects": []})
    inbox = read_json(INBOX_FILE, {"candidates": []})
    known_urls = {
        normalized_url(item.get("sourceUrl", ""))
        for item in projects_data.get("projects", [])
        if item.get("sourceUrl")
    }
    known_urls.update(normalized_url(item.get("noticeUrl", "")) for item in inbox.get("candidates", []))
    prior_candidates = inbox.get("candidates", [])
    new_candidates: list[dict] = []
    errors: list[tuple[str, str]] = []
    checked = 0

    sources = source_data.get("sources", [])
    with ThreadPoolExecutor(max_workers=min(SOURCE_WORKERS, max(1, len(sources)))) as executor:
        for success_count, candidates, error in executor.map(scan_source, sources):
            checked += success_count
            if error:
                errors.append(error)
            for candidate in candidates:
                target = normalized_url(candidate["noticeUrl"])
                if target in known_urls:
                    continue
                digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:16]
                known_urls.add(target)
                candidate["id"] = digest
                new_candidates.append(candidate)

    if checked == 0:
        print("No official source page could be fetched; failing the scan.", file=sys.stderr)
        return 1

    all_candidates = prior_candidates + new_candidates
    all_candidates.sort(key=lambda item: (item.get("discoveredAt", ""), item.get("region", ""), item.get("title", "")), reverse=True)
    INBOX_FILE.write_text(json.dumps({
        "schemaVersion": 1,
        "description": "自动扫描得到的候选公告。未经人工核验不得直接展示为有效申报信息。核实并迁入 projects.json 后，请从本文件删除对应条目。",
        "candidates": all_candidates,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## 青椒之梯每周公告扫描\n\n")
            summary.write(f"- 成功读取来源：{checked}/{len(source_data.get('sources', []))}\n")
            summary.write(f"- 新发现候选：{len(new_candidates)} 条\n")
            summary.write(f"- 待核验队列总数：{len(all_candidates)} 条\n")
            if errors:
                summary.write("\n### 本次读取失败的来源\n")
                for host, error in errors:
                    summary.write(f"- {host}: {error[:240]}\n")
            if new_candidates:
                summary.write("\n### 新发现线索\n")
                for item in new_candidates:
                    summary.write(f"- [{item['title']}]({item['noticeUrl']}) — {item['region']} / {item['sourceHost']}\n")
    print(f"Checked {checked} source pages; found {len(new_candidates)} new candidates; {len(errors)} errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
