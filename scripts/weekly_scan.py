#!/usr/bin/env python3
"""Automatically review and publish high-confidence official project notices."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import codecs
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
EXCLUDED_TITLE_TERMS = ("征求意见", "指南建议", "建议征集", "拟立项", "立项名单", "评审结果", "推荐结果", "项目公示", "申报培训")
SOURCE_WORKERS = 4
MAX_UNIVERSITY_NOTICES_PER_SOURCE = 8
MAX_PAGE_BYTES = 5_000_000
MAX_NOTICE_BYTES = 8_000_000

CATEGORY_TERMS = {
    "natural-science": ("自然科学", "科技计划", "基础研究", "科学基金", "科研项目", "重点研发计划", "科技重大专项"),
    "social-science": ("社会科学", "哲学社会科学", "社科规划", "社科基金", "人文社会科学"),
    "education": ("教育科学", "教育科研", "教育研究", "教育规划"),
    "postdoctoral": ("博士后", "博新计划", "博士后基金"),
}
NATIONAL_TERMS = (
    "国家自然科学基金", "国家自然基金", "国家社会科学基金", "国家社科基金",
    "全国教育科学规划", "中国博士后科学基金", "博士后创新人才支持计划",
    "国家重点研发计划", "国家科技重大专项", "教育部人文社会科学", "博新计划",
)


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_href: str | None = None
        self.current_title = ""
        self.current_text: list[str] = []
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            values = dict(attrs)
            self.current_href = values.get("href")
            self.current_title = values.get("title") or ""
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href is not None:
            text = re.sub(r"\s+", " ", " ".join(self.current_text)).strip()
            if len(self.current_title) > len(text):
                text = self.current_title.strip()
            if text:
                self.items.append((self.current_href, text))
            self.current_href = None
            self.current_title = ""
            self.current_text = []


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self.ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


def normalized_url(url: str) -> str:
    url, _fragment = urldefrag(url.strip())
    return url.rstrip("/")


def clean_title(title: str) -> str:
    title = html.unescape(re.sub(r"\s+", " ", title)).strip()
    return re.sub(r"^\d{1,2}\s+20\d{2}[-/]\d{1,2}\s+", "", title)


def allowed_domain(url: str, source: dict) -> bool:
    host = (urlparse(url).hostname or "").lower()
    source_host = (urlparse(source["url"]).hostname or "").lower()
    if source.get("sourceType") == "university":
        domain = source.get("officialDomain", "").lower()
        return bool(domain.endswith(".edu.cn") and (source_host == domain or source_host.endswith("." + domain)) and (host == domain or host.endswith("." + domain)))
    return bool(host and source_host and (host == source_host or host.endswith("." + source_host)))


def decode_page(raw: bytes, header_charset: str | None) -> str:
    meta = re.search(br"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", raw[:4096], re.I)
    candidates = [header_charset, meta.group(1).decode("ascii", errors="ignore") if meta else None, "utf-8", "gb18030"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            codecs.lookup(encoding)
            return raw.decode(encoding)
        except (LookupError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_page(url: str, max_bytes: int = MAX_PAGE_BYTES) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": "QingjiaoWeeklyNoticeScan/2.0 (+https://github.com/xingyezn/Qingjiao)"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(max_bytes)
        return decode_page(raw, response.headers.get_content_charset()), response.geturl()


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def category_for_source(source: dict) -> str | None:
    if source.get("sourceType") == "university":
        return None
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


def category_for_notice(source: dict, title: str) -> str | None:
    if source.get("sourceType") == "university":
        for category in ("postdoctoral", "education", "social-science", "natural-science"):
            if category_matches(title, category):
                return category
        return None
    return category_for_source(source)


def region_for_notice(source: dict, title: str) -> str | None:
    if source.get("sourceType") != "university":
        return source.get("region")
    if any(term in title for term in NATIONAL_TERMS):
        return "国家级"
    region = source.get("region", "")
    return region if region and region in title else None


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
    category = category_for_notice(source, title)
    if not category or len(title) < 8 or not category_matches(title, category):
        return None
    if any(term in title for term in EXCLUDED_TITLE_TERMS):
        return None
    region = region_for_notice(source, title)
    if not region:
        return None
    if not any(term in title for term in INTENT_TERMS):
        return None
    years = [int(value) for value in YEAR_PATTERN.findall(title)]
    if not years or not any(year in calendar_years() for year in years):
        return None
    if urlparse(notice_url).scheme != "https" or not allowed_domain(notice_url, source):
        return None

    try:
        page, final_url = fetch_page(notice_url, MAX_NOTICE_BYTES)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None
    if urlparse(final_url).scheme != "https" or not allowed_domain(final_url, source):
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
    currently_open = source.get("sourceType") != "university" and explicitly_open and deadline is not None and deadline >= today and (start_at is None or start_at <= today)
    year = next((value for value in years if value in calendar_years()), max(years))
    return {
        "category": category,
        "region": region,
        "year": year,
        "startAt": to_iso(start_at) if source.get("sourceType") != "university" else None,
        "deadline": to_iso(deadline) if source.get("sourceType") != "university" else None,
        "status": "open" if currently_open else "announced",
        "statusText": "申报中（自动核验官方通知）" if currently_open else "官方通知已发布（自动收录）",
    }


def scan_source(source: dict) -> tuple[int, list[dict], tuple[str, str] | None]:
    listing_url = source["url"]
    try:
        page, final_listing_url = fetch_page(listing_url)
        if source.get("sourceType") == "university" and not allowed_domain(final_listing_url, source):
            raise ValueError("University listing redirected outside its official domain")
        parser = AnchorParser()
        parser.feed(page)
        records = []
        if source.get("sourceType") != "university" and not category_for_source(source):
            return 1, [], None
        attempted = 0
        for href, raw_title in parser.items:
            title = clean_title(raw_title)
            if not any(term in title for term in (source.get("keywords") or INTENT_TERMS)):
                continue
            if any(term in title for term in EXCLUDED_TITLE_TERMS):
                continue
            notice_url = normalized_url(urljoin(listing_url, href))
            if notice_url == normalized_url(listing_url):
                continue
            if urlparse(notice_url).scheme != "https" or not allowed_domain(notice_url, source):
                continue
            if not category_for_notice(source, title) or not region_for_notice(source, title):
                continue
            if not any(int(value) in calendar_years() for value in YEAR_PATTERN.findall(title)):
                continue
            if source.get("sourceType") == "university" and attempted >= MAX_UNIVERSITY_NOTICES_PER_SOURCE:
                break
            attempted += 1
            reviewed = review_notice(source, listing_url, title, notice_url)
            if not reviewed:
                continue
            records.append({
                **reviewed,
                "level": level_for_region(reviewed["region"]),
                "host": "以高校通知原文为准" if source.get("sourceType") == "university" else source.get("host", ""),
                "name": title[:300],
                "summary": "由高校科研管理部门转发的国家级或省级项目通知；校内截止时间可能早于主管部门时间，请查看原文。" if source.get("sourceType") == "university" else "由系统依据官方来源域名、公告年度、项目类别及申报关键词自动审核收录。资格条件和附件以原始通知为准。",
                "publishedAt": None,
                "sourceUrl": notice_url,
                "sourceType": source.get("sourceType", "government"),
                "sourceInstitution": source.get("host", ""),
                "sourceCheckedAt": date.today().isoformat(),
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
    missing_sources = [
        item.get("id", "未命名") for item in projects
        if item.get("recordType") != "official-source-index"
        and (urlparse(item.get("sourceUrl") or "").scheme != "https" or not urlparse(item.get("sourceUrl") or "").hostname)
    ]
    if missing_sources:
        print(f"Project source URLs must be valid HTTPS links: {', '.join(missing_sources)}", file=sys.stderr)
        return 1
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
            summary.write(f"- 其中高校来源：{sum(item.get('sourceType') == 'university' for item in new_projects)} 条\n")
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
