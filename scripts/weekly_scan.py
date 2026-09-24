#!/usr/bin/env python3
"""Automatically review and publish high-confidence official project notices."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCES_FILE = ROOT / "data" / "sources.json"
PROJECTS_FILE = ROOT / "data" / "projects.json"
YEAR_PATTERN = re.compile(r"20\d{2}")
INTENT_TERMS = ("申报", "申请", "指南", "项目", "课题", "资助", "博士后")
SOURCE_WORKERS = 4
MAX_PAGE_BYTES = 5_000_000
MAX_NOTICE_BYTES = 8_000_000

CATEGORY_TERMS = {
    "natural-science": ("自然科学", "科技计划", "基础研究", "科学基金", "科研项目"),
    "social-science": ("社会科学", "哲学社会科学", "社科规划", "社科基金", "人文社会科学"),
    "education": ("教育科学", "教育科研", "教育研究", "教育规划"),
    "postdoctoral": ("博士后", "博新计划", "博士后基金"),
}


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.current_href = dict(attrs).get("href")
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


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalized_url(url: str) -> str:
    url, _fragment = urldefrag(url.strip())
    return url.rstrip("/")


def allowed_domain(url: str, source_url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    source_host = (urlparse(source_url).hostname or "").lower()
    return bool(host and source_host and (host == source_host or host.endswith("." + source_host)))


def fetch_page(url: str, max_bytes: int = MAX_PAGE_BYTES) -> str:
    request = Request(url, headers={"User-Agent": "QingjiaoWeeklyNoticeScan/2.0 (+https://github.com/xingyezn/Qingjiao)"})
    with urlopen(request, timeout=15) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(max_bytes)
        return raw.decode(charset, errors="replace")


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def category_for_source(source: dict) -> str | None:
    source_id = source.get("id", "")
    if source_id in {"nsfc", "shanghai-science", "beijing-science", "guangdong-science"}:
        return "natural-science"
    if source_id == "nssfc" or source_id == "moe" or source_id.endswith("-social") or source_id.endswith("-social-science"):
        return "social-science"
    if source_id == "education-national" or source_id.endswith("-education"):
        return "education"
    if source_id == "postdoc-national" or source_id.endswith("-postdoc") or source_id.endswith("-postdoctoral"):
        return "postdoctoral"
    return None


def level_for_region(region: str) -> str:
    return "国家级" if region == "国家级" else "省部级"


def category_matches(title: str, category: str) -> bool:
    return any(term in title for term in CATEGORY_TERMS[category])


def calendar_years() -> set[int]:
    year = date.today().year
    return {year, year + 1}


def extract_date(text: str, context_pattern: str) -> date | None:
    match = re.search(context_pattern + r"[^\d]{0,30}(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def to_iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def review_notice(source: dict, listing_url: str, title: str, notice_url: str) -> dict | None:
    category = category_for_source(source)
    if not category or len(title) < 8 or not category_matches(title, category):
        return None
    if not any(term in title for term in INTENT_TERMS):
        return None
    years = [int(value) for value in YEAR_PATTERN.findall(title)]
    if not years or not any(year in calendar_years() for year in years):
        return None
    if urlparse(notice_url).scheme != "https" or not allowed_domain(notice_url, listing_url):
        return None

    try:
        page = fetch_page(notice_url, MAX_NOTICE_BYTES)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None

    parser = TextParser()
    parser.feed(page)
    body = html.unescape(re.sub(r"\s+", " ", " ".join(parser.parts)))
    if len(body.strip()) < 80 or not any(term in body for term in INTENT_TERMS):
        return None

    today = date.today()
    start_at = extract_date(body, r"(?:申报|申请|受理)(?:时间|期限|日期)?(?:自|从|为|：|:)?")
    deadline = extract_date(body, r"(?:截止|截至|申报至|受理至|提交至)(?:时间|日期)?(?:为|：|:)?")
    # A notice is listed as currently open only when its official text states
    # an application window and provides an unexpired deadline.
    explicitly_open = any(term in body for term in ("正在申报", "申报中", "开放申报", "开始申报"))
    currently_open = explicitly_open and deadline is not None and deadline >= today and (start_at is None or start_at <= today)
    year = next((value for value in years if value in calendar_years()), max(years))
    return {
        "category": category,
        "year": year,
        "startAt": to_iso(start_at),
        "deadline": to_iso(deadline),
        "status": "open" if currently_open else "announced",
        "statusText": "申报中（自动核验官方通知）" if currently_open else "官方通知已发布（自动收录）",
    }


def scan_source(source: dict) -> tuple[int, list[dict], tuple[str, str] | None]:
    listing_url = source["url"]
    try:
        page = fetch_page(listing_url)
        parser = AnchorParser()
        parser.feed(page)
        records = []
        category = category_for_source(source)
        if not category:
            return 1, [], None
        for href, raw_title in parser.items:
            title = html.unescape(raw_title)
            if not any(term in title for term in source.get("keywords", [])):
                continue
            notice_url = normalized_url(urljoin(listing_url, href))
            if notice_url == normalized_url(listing_url):
                continue
            reviewed = review_notice(source, listing_url, title, notice_url)
            if not reviewed:
                continue
            records.append({
                **reviewed,
                "region": source.get("region", "待分类"),
                "category": reviewed["category"],
                "level": level_for_region(source.get("region", "")),
                "host": source.get("host", ""),
                "name": title[:300],
                "summary": "由系统依据官方来源域名、公告年度、项目类别及申报关键词自动审核收录。资格条件和附件以原始通知为准。",
                "publishedAt": None,
                "sourceUrl": notice_url,
                "applicationUrl": None,
                "checkedAt": date.today().isoformat(),
                "automaticReview": {
                    "decision": "approved",
                    "rulesVersion": 1,
                    "reviewedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            })
        return 1, records, None
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        return 0, [], (source.get("host", source.get("id", listing_url)), str(error))


def main() -> int:
    source_data = read_json(SOURCES_FILE, {"sources": []})
    project_data = read_json(PROJECTS_FILE, {"projects": []})
    projects = project_data.get("projects", [])
    known_urls = {normalized_url(item.get("sourceUrl", "")) for item in projects if item.get("sourceUrl")}
    new_projects: list[dict] = []
    errors: list[tuple[str, str]] = []
    checked = 0
    sources = source_data.get("sources", [])
    with ThreadPoolExecutor(max_workers=min(SOURCE_WORKERS, max(1, len(sources)))) as executor:
        for success_count, records, error in executor.map(scan_source, sources):
            checked += success_count
            if error:
                errors.append(error)
            for record in records:
                target = normalized_url(record["sourceUrl"])
                if target in known_urls:
                    continue
                digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:16]
                record["id"] = f"auto-{digest}"
                known_urls.add(target)
                new_projects.append(record)

    if checked == 0:
        print("No official source page could be fetched; failing the scan.", file=sys.stderr)
        return 1

    if new_projects:
        projects.extend(new_projects)
        projects.sort(key=lambda item: (item.get("status") != "open", item.get("deadline") or "9999", item.get("region", ""), item.get("name", "")))
        project_data["projects"] = projects
        project_data["lastVerified"] = date.today().isoformat()
        PROJECTS_FILE.write_text(json.dumps(project_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## 青椒之梯每周自动审核扫描\n\n")
            summary.write(f"- 成功读取来源：{checked}/{len(sources)}\n")
            summary.write(f"- 自动通过并收录：{len(new_projects)} 条\n")
            summary.write(f"- 自动拒绝/忽略低置信度线索，不进入人工队列\n")
            if errors:
                summary.write(f"- 读取失败：{len(errors)} 个来源\n")
            if new_projects:
                summary.write("\n### 自动收录公告\n")
                for item in new_projects:
                    summary.write(f"- [{item['name']}]({item['sourceUrl']}) — {item['region']} / {item['category']} / {item['statusText']}\n")
    print(f"Checked {checked} source pages; automatically published {len(new_projects)} notices; {len(errors)} errors.")
    for host, error in errors:
        print(f"Source fetch failed [{host}]: {error[:240]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
