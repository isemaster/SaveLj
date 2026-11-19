#!/usr/bin/env python3
"""SaveLj v2 -- LiveJournal backup: posts + full comments + all images.

Layout (ROOT_DIR from config.txt, default ./output):
  ROOT/css/lj.css
  ROOT/lj_icons/            LJ service icons (stat.livejournal.*), missing.svg placeholder
  ROOT/userpics/<user>/     <part1>_<part2>.<ext>
  ROOT/<journal>/index.html year/month index (<details>)
  ROOT/<journal>/<id>.html  one file per post, comments included
  ROOT/<journal>/images/    all other images of the posts and comments

Flags: --force (re-download existing posts)  --index-only (rebuild index.html)
       --mobile (force the mobile comment reader)  --debug (dump raw JSON/HTML to <journal>/debug/)
"""

import calendar
import hashlib
import html
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse
from wakepy import keep

import requests
from bs4 import BeautifulSoup

# ================= SETTINGS =================
HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookies.txt")
CFG_FILE = os.path.join(HERE, "config.txt")
IMAGES_FOLDER, USERPICS_FOLDER, ICONS_FOLDER = "images", "userpics", "lj_icons"
PLACEHOLDER_NAME = "missing.svg"

FORCE = "--force" in sys.argv
INDEX_ONLY = "--index-only" in sys.argv
FORCE_MOBILE = "--mobile" in sys.argv
DEBUG = "--debug" in sys.argv

NAV_START, NAV_END = "<!-- lj-nav -->", "<!-- /lj-nav -->"
NAV_RE = re.compile(re.escape(NAV_START) + r".*?" + re.escape(NAV_END), re.S)

UNTITLED = "* * *"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
DELAYS = {
    "between_pages": (1, 3),
    "between_posts": (60, 120),
    "between_comments": (1, 4),
    "between_days": (0.5, 2),
    "between_years": (30, 60),
    "before_retry": (5, 10),
}
EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
}
IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp")
SERVICE_HOST_RE = re.compile(r"^(l-)?stat\.livejournal\.(net|com)$")
USERPIC_HOST_RE = re.compile(r"^(l-)?userpic\.livejournal\.com$")
JUNK_TAGS = [
    "script",
    "style",
    "noscript",
    "form",
    "input",
    "button",
    "textarea",
    "select",
]
JUNK_CLASS_TOKENS = {
    "socials",
    "social",
    "share",
    "sharing",
    "like",
    "likes",
    "repost",
    "featposts",
    "slot",
    "qrcode",
}
RUS_MONTHS = {
    "января": "01",
    "февраля": "02",
    "марта": "03",
    "апреля": "04",
    "мая": "05",
    "июня": "06",
    "июля": "07",
    "августа": "08",
    "сентября": "09",
    "октября": "10",
    "ноября": "11",
    "декабря": "12",
    "янв": "01",
    "фев": "02",
    "мар": "03",
    "апр": "04",
    "май": "05",
    "июн": "06",
    "июл": "07",
    "авг": "08",
    "сен": "09",
    "окт": "10",
    "ноя": "11",
    "дек": "12",
}
MONTH_NAMES = [
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]
# ============================================


def load_root_folder():
    default = os.path.join(HERE, "output")
    try:
        with open(CFG_FILE, encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.partition("=")
                if not sep or key.strip() != "ROOT_DIR":
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1].strip()
                if not value:
                    continue
                value = os.path.expandvars(os.path.expanduser(value))
                if not os.path.isabs(value):
                    value = os.path.join(
                        os.path.dirname(os.path.abspath(CFG_FILE)), value
                    )
                return os.path.normpath(value)
    except FileNotFoundError:
        pass
    return default


ROOT_FOLDER = load_root_folder()

LOGGER = logging.getLogger("SaveLj")


def load_logging_settings():
    log_filename = "SaveLj.log"
    log_level_name = "ERROR"
    try:
        with open(CFG_FILE, encoding="utf-8") as config_file:
            for line in config_file:
                key, separator, value = line.partition("=")
                if not separator:
                    continue
                key = key.strip()
                value = value.strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in ("'", '"')
                ):
                    value = value[1:-1].strip()
                if key == "LOG_FILE" and value:
                    log_filename = os.path.expandvars(
                        os.path.expanduser(value)
                    )
                elif key == "LOG_LEVEL" and value:
                    log_level_name = value.upper()
    except FileNotFoundError:
        pass

    log_level = logging.getLevelName(log_level_name)
    if not isinstance(log_level, int):
        raise ValueError(f"Invalid LOG_LEVEL in {CFG_FILE}: {log_level_name}")

    if not os.path.isabs(log_filename):
        log_filename = os.path.join(ROOT_FOLDER, log_filename)
    return os.path.normpath(log_filename), log_level


def configure_logging():
    log_filename, log_level = load_logging_settings()
    log_directory = os.path.dirname(log_filename)
    if log_directory:
        os.makedirs(log_directory, exist_ok=True)

    for handler in LOGGER.handlers[:]:
        LOGGER.removeHandler(handler)
        handler.close()
    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    LOGGER.setLevel(log_level)
    LOGGER.addHandler(file_handler)
    LOGGER.propagate = False


# ---------------------------------------------------------------- utilities
def load_cookies():
    cookies = {}
    with open(COOKIE_FILE, encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, raw = line.partition(":")
            if not sep or not name.strip():
                raise ValueError(f'строка {n}: ожидается имя: "значение"')
            try:
                value = json.loads(raw.strip())
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"строка {n}: значение cookie должно быть в кавычках"
                ) from e
            if not isinstance(value, str):
                raise ValueError(f"строка {n}: значение cookie должно быть строкой")
            cookies[name.strip()] = value
    return cookies


def load_cookies_or_confirm():
    try:
        return load_cookies()
    except FileNotFoundError:
        print("Без файла cookies.txt будут доступны только открытые посты. Продолжить?")
        if input(" > ").strip().lower() not in ("y", "yes", "д", "да"):
            print("⛔ Отмена.")
            sys.exit()
        return {}
    except (OSError, ValueError) as e:
        LOGGER.error("Failed to load cookies from %s: %s", COOKIE_FILE, e)
        print(f"❌ Ошибка загрузки cookies из {COOKIE_FILE}: {e}")
        sys.exit(1)


def random_delay(rng):
    time.sleep(random.uniform(*rng))


def clean_filename(text):
    return re.sub(r'[\\/*?:"<>|]', "", text).strip()[:100]


def safe_name(text):
    return re.sub(r'[\\/*?:"<>|\s]', "_", text or "unknown")


def fetch(session, url, retries=4, timeout=30, **kw):
    """GET with retries for network errors, 429 and 5xx. Returns Response or None."""
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=timeout, **kw)
        except requests.RequestException as e:
            LOGGER.error(
                "Request failed on attempt %d/%d for %s: %s",
                attempt + 1,
                retries + 1,
                url,
                e,
            )
            if attempt == retries:
                print(f"      ❌ {url}: {e.__class__.__name__}")
                return None
            random_delay(DELAYS["before_retry"])
            continue
        if r.status_code == 429 or r.status_code >= 500:
            LOGGER.error(
                "Request returned HTTP %s on attempt %d/%d for %s",
                r.status_code,
                attempt + 1,
                retries + 1,
                url,
            )
            if attempt == retries:
                return r
            print(f"      🚫 {r.status_code} — пауза...")
            random_delay((60, 120) if r.status_code == 429 else DELAYS["before_retry"])
            continue
        if r.status_code != 200:
            LOGGER.error("Request returned HTTP %s for %s", r.status_code, url)
        return r
    return None


def parse_russian_date_from_text(text):
    if not text:
        return None
    text = text.lower()
    m = re.search(r"(\d{1,2})\s+([а-я]+)[,\s]+(\d{4})", text)
    if m:
        day, mon, year = m.groups()
        if RUS_MONTHS.get(mon):
            return f"{year}_{RUS_MONTHS[mon]}_{day.zfill(2)}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return "_".join(m.groups())
    return None


def get_post_date(soup):
    meta = soup.find("meta", property="article:published_time")
    if meta and meta.get("content"):
        return meta["content"].split("T")[0].replace("-", "_")
    for tag in soup.find_all("time"):
        if tag.get("datetime"):
            return tag["datetime"].split("T")[0].replace("-", "_")
        parsed = parse_russian_date_from_text(tag.get_text())
        if parsed:
            return parsed
    for c in soup.find_all(class_=re.compile(r"(date|time|header|meta|subject)")):
        parsed = parse_russian_date_from_text(c.get_text())
        if parsed:
            return parsed
    return parse_russian_date_from_text(soup.get_text()[:10000]) or "0000_00_00"


# ---------------------------------------------------------------- cleaning
def strip_junk(root):
    """Remove scripts, forms, like/share widgets, ad slots."""
    for t in root.find_all(JUNK_TAGS):
        t.decompose()
    for t in root.find_all(class_=True):
        if getattr(t, "_decomposed", False):
            continue
        classes = t.get("class") or []
        tokens = set()
        for c in classes:
            tokens.update(x for x in re.split(r"[-_]+", c.lower()) if x)
            if "category-panel" in c.lower():
                tokens.add("slot")
        if tokens & JUNK_CLASS_TOKENS:
            t.decompose()
    for t in root.find_all(True):
        for attr in [a for a in (t.attrs or {}) if a.lower().startswith("on")]:
            del t.attrs[attr]


# ---------------------------------------------------------------- assets
PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50" viewBox="0 0 50 50">'
    '<rect x="1" y="1" width="48" height="48" fill="#eee" stroke="#aaa" stroke-width="2" '
    'stroke-dasharray="4 3"/><path d="M10 38l10-12 7 8 5-6 8 10z" fill="#bbb"/>'
    '<circle cx="18" cy="16" r="4" fill="#bbb"/></svg>'
)


def sniff_ext(content, ctype, url):
    if ctype in EXT_BY_TYPE:
        return EXT_BY_TYPE[ctype]
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG"):
        return ".png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if b"<svg" in content[:500].lower():
        return ".svg"
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext in IMG_EXTS:
        return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


class Assets:
    def __init__(self, session, root, journal):
        self.session, self.root, self.journal = session, root, journal
        self.cache = {}
        self.page_dir = os.path.join(root, journal)

    @staticmethod
    def classify(url):
        host = urlparse(url).netloc.lower()
        if SERVICE_HOST_RE.match(host):
            return "service"
        if USERPIC_HOST_RE.match(host):
            return "userpic"
        return "image"

    def _target(self, url, kind, owner, post_id):
        """-> (directory, base name without extension)"""
        parts = [p for p in urlparse(url).path.split("/") if p]
        if kind == "service":
            if parts and parts[0] == "img":
                parts = parts[1:]
            stem = "_".join(parts) or "icon"
            stem, ext = os.path.splitext(stem)
            return os.path.join(self.root, ICONS_FOLDER), stem, ext
        if kind == "userpic":
            parts[-1:] = [os.path.splitext(parts[-1])[0]] if parts else ["unknown"]
            return (
                os.path.join(self.root, USERPICS_FOLDER, safe_name(owner)),
                "_".join(parts[-2:]),
                "",
            )
        digest = hashlib.md5(url.encode()).hexdigest()[:8]
        return os.path.join(self.page_dir, IMAGES_FOLDER), f"{post_id}_{digest}", ""

    def localize(self, url, owner=None, post_id="x", stem=None):
        """Download url once; returns absolute local path or None."""
        key = (url, owner if self.classify(url) == "userpic" else None)
        if key in self.cache:
            return self.cache[key]
        kind = self.classify(url)
        folder, base, svc_ext = self._target(url, kind, owner or "_unknown", post_id)
        if stem and kind == "image":
            base = stem
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if os.path.splitext(f)[0] == base:
                    self.cache[key] = os.path.join(folder, f)
                    return self.cache[key]
        r = fetch(self.session, url, retries=2, timeout=20)
        result = None
        if r is not None and r.status_code == 200 and r.content:
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype.startswith("text/"):
                LOGGER.error(
                    "Image request returned non-image content type %s: %s",
                    ctype,
                    url,
                )
            else:
                ext = svc_ext or sniff_ext(r.content, ctype, url)
                os.makedirs(folder, exist_ok=True)
                result = os.path.join(folder, base + ext)
                with open(result, "wb") as fh:
                    fh.write(r.content)
        elif r is not None and r.status_code == 200:
            LOGGER.error("Image request returned an empty response: %s", url)
        elif r is not None:
            print(f"      ⚠️ картинка недоступна ({r.status_code}): {url}")
        self.cache[key] = result
        return result

    def placeholder(self):
        folder = os.path.join(self.root, ICONS_FOLDER)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, PLACEHOLDER_NAME)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(PLACEHOLDER_SVG)
        return path

    def rel(self, abs_path):
        return os.path.relpath(abs_path, self.page_dir).replace(os.sep, "/")

    def big_image_for(self, img, abs_url, base_url, post_id):
        """Если картинку оборачивает <a href=big.jpg>: качаем как <имя маленькой>_big.<ext>."""
        parent = img.parent
        if parent is None or parent.name != "a" or not parent.get("href"):
            return None
        href = urljoin(base_url, parent["href"].strip())
        if (
            href == abs_url
            or self.classify(abs_url) != "image"
            or self.classify(href) != "image"
            or os.path.splitext(urlparse(href).path)[1].lower() not in IMG_EXTS
        ):
            return None
        small_stem = self._target(abs_url, "image", None, post_id)[1]
        big = self.localize(href, None, post_id, stem=small_stem + "_big")
        if big:
            parent["href"] = self.rel(big)
        return big

    def rewrite_images(self, tag, base_url, post_id, owner=None):
        """Localize every <img> inside tag; unavailable ones become a 50x50 placeholder."""
        n_ok = n_bad = 0
        for img in tag.find_all("img"):
            src = (
                img.get("src") or img.get("data-src") or img.get("data-original") or ""
            )
            if not src or src.startswith("data:"):
                continue
            abs_url = urljoin(base_url, src.strip())
            alt = (
                img.get("alt")
                or img.get("title")
                or os.path.basename(urlparse(abs_url).path)
            )
            for attr in ("srcset", "data-src", "data-original", "loading", "sizes"):
                img.attrs.pop(attr, None)
            path = self.localize(abs_url, owner, post_id)
            big = self.big_image_for(img, abs_url, base_url, post_id)
            path = path or big
            if path:
                img["src"] = self.rel(path)
                n_ok += 1
            else:
                img["src"] = self.rel(self.placeholder())
                img["width"], img["height"] = "50", "50"
                img["alt"], img["title"] = alt, abs_url
                img["class"] = (img.get("class") or []) + ["img-missing"]
                img.attrs.pop("style", None)
                n_bad += 1
        return n_ok, n_bad


# ---------------------------------------------------------------- scanning
def extract_post_urls_from_page(soup, username):
    urls = set()
    for t in soup.select("div.j-w-featposts"):
        t.decompose()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if f"{username}.livejournal.com" in href and re.search(r"/\d+\.html$", href):
            urls.add(href)
    return urls


def get_day_urls(soup, username, year, start_date, end_date):
    days = {}
    base = f"https://{username}.livejournal.com"
    for link in soup.find_all("a", href=True):
        m = re.search(rf"/{year}/(\d{{2}})/(\d{{2}})/?$", link["href"])
        if not m:
            continue
        try:
            d = date(int(year), int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if start_date <= d.isoformat() <= end_date:
            days[d.isoformat()] = urljoin(base, link["href"])
    return days


def scan_year(session, username, year, start_date, end_date):
    print(f"   🔎 Сканирую {year} год...")
    urls, dates = set(), {}
    year_url = f"https://{username}.livejournal.com/{year}/"
    r = fetch(session, year_url)
    if r is None or r.status_code != 200:
        LOGGER.error(
            "Archive page unavailable for %s (%s): %s",
            username,
            year,
            getattr(r, "status_code", "no response"),
        )
        print(
            f"      ❌ Страница архива недоступна: {getattr(r, 'status_code', 'нет ответа')}"
        )
        return []
    m = re.search(r'"has_remote"\s*:\s*(\d)', r.text)
    if m and m.group(1) == "0" and session.cookies:
        LOGGER.error("LiveJournal did not accept the configured cookies for %s", username)
        print("   ⚠️ ЖЖ считает запрос анонимным: куки не приняты")
    soup = BeautifulSoup(r.text, "html.parser")
    days = get_day_urls(soup, username, year, start_date, end_date)
    if days:
        for day, day_url in sorted(days.items()):
            rd = fetch(session, day_url)
            if rd is not None and rd.status_code == 200:
                found = extract_post_urls_from_page(
                    BeautifulSoup(rd.text, "html.parser"), username
                )
                urls |= found
                for u in found:
                    dates[u] = day
            random_delay(DELAYS["between_days"])
    else:
        if start_date <= f"{year}-01-01" and f"{year}-12-31" <= end_date:
            urls |= extract_post_urls_from_page(soup, username)
        for month in range(1, 13):
            rm = fetch(
                session, f"https://{username}.livejournal.com/{year}/{month:02d}/"
            )
            if rm is not None and rm.status_code == 200:
                msoup = BeautifulSoup(rm.text, "html.parser")
                found = extract_post_urls_from_page(msoup, username)
                mdays = get_day_urls(msoup, username, year, start_date, end_date)
                for day, day_url in sorted(mdays.items()):
                    rd = fetch(session, day_url)
                    if rd is not None and rd.status_code == 200:
                        posts = extract_post_urls_from_page(
                            BeautifulSoup(rd.text, "html.parser"), username
                        )
                        urls |= posts
                        for u in posts:
                            dates[u] = day
                last = calendar.monthrange(int(year), month)[1]
                if (
                    not mdays
                    and start_date <= f"{year}-{month:02d}-01"
                    and f"{year}-{month:02d}-{last:02d}" <= end_date
                ):
                    urls |= found
            random_delay(DELAYS["between_pages"])
    for u in urls - set(dates):
        rp = fetch(session, u)
        if rp is None or rp.status_code != 200:
            continue
        rp.encoding = rp.encoding if rp.encoding == "utf-8" else rp.apparent_encoding
        d = get_post_date(BeautifulSoup(rp.text, "html.parser"))
        if d != "0000_00_00":
            dates[u] = d.replace("_", "-")
        random_delay(DELAYS["between_pages"])
    dated = sorted(
        ((u, dates[u]) for u in urls if u in dates), key=lambda x: (x[1], x[0])
    )
    if len(dated) != len(urls):
        LOGGER.error(
            "Skipped %d posts without a date while scanning %s for %s",
            len(urls) - len(dated),
            year,
            username,
        )
        print(f"      ⚠️ Пропущено постов без даты: {len(urls) - len(dated)}")
    print(f"      ✅ Найдено постов в {year} году: {len(dated)}")
    return dated


# ---------------------------------------------------------------- comments
def extract_json_assignment(text, name):
    """Find `name = {...}` in page scripts and decode the JSON object."""
    for m in re.finditer(re.escape(name) + r"\s*=\s*", text):
        start = m.end()
        if start < len(text) and text[start] == "{":
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[start:])
                return obj
            except ValueError:
                continue
    return None


def norm_comment(c, journal):
    cid = str(c.get("dtalkid") or c.get("thread") or "")
    ts = c.get("ctime_ts")
    when = c.get("ctime") or ""
    if ts:
        try:
            when = datetime.fromtimestamp(int(ts), timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
        except (ValueError, OSError, OverflowError):
            pass
    name = c.get("uname") or c.get("username") or c.get("dname") or ""
    return {
        "id": cid,
        "parent": str(c.get("parent") or 0),
        "level": int(c.get("level") or 0),
        "user": name,
        "userpic": c.get("userpic") or "",
        "date": when,
        "subject": c.get("subject") or "",
        "html": c.get("article") or "",
        "deleted": bool(c.get("deleted")),
        "screened": c.get("leafclass") == "screened",
        "loaded": c.get("loaded", 1),
    }


def order_comments(items):
    """-> [(comment, depth)] in thread order. Uses parent links when present, else level."""
    by_id = {c["id"]: c for c in items if c["id"]}
    if (
        by_id
        and all(c["id"] in by_id for c in items)
        and any(c["parent"] != "0" for c in items)
    ):
        kids = defaultdict(list)
        for c in items:
            kids[c["parent"] if c["parent"] in by_id else "0"].append(c)
        out, stack = (
            [],
            [
                (c, 0)
                for c in sorted(kids["0"], key=lambda c: int(c["id"]), reverse=True)
            ],
        )
        while stack:
            c, d = stack.pop()
            out.append((c, d))
            stack.extend(
                (k, d + 1)
                for k in sorted(kids[c["id"]], key=lambda c: int(c["id"]), reverse=True)
            )
        return out
    return [(c, c["level"]) for c in items]


def collect_comments_desktop(
    session, journal, post_id, post_url, first_html, soup, debug_dir=None
):
    page_obj = extract_json_assignment(first_html, "Site.page")
    if not page_obj or "comments" not in page_obj:
        return None
    store, tried = {}, set()

    def add(raw_list):
        for raw in raw_list:
            if "more" in raw and not raw.get("dtalkid"):
                if raw.get("parent"):
                    pending.add(str(raw["parent"]))
                continue
            c = norm_comment(raw, journal)
            if not c["id"]:
                continue
            old = store.get(c["id"])
            if old is None or (not old["loaded"] and c["loaded"]) or c["html"]:
                store[c["id"]] = c
            if (
                not c["loaded"]
                and not c["deleted"]
                and not c["screened"]
                and c["id"] not in tried
            ):
                pending.add(c["id"])

    pending = set()
    add(page_obj["comments"])
    pages = {1}
    for a in soup.find_all("a", href=True):
        if f"/{post_id}.html" in a["href"]:
            q = parse_qs(urlparse(a["href"]).query)
            if "page" in q and q["page"][0].isdigit():
                pages.add(int(q["page"][0]))
    for pg in sorted(pages - {1}):
        random_delay(DELAYS["between_comments"])
        r = fetch(session, f"{post_url}?noscroll#comments#page={pg}")
        if r is None or r.status_code != 200:
            LOGGER.error(
                "Could not load comment page %s for post %s in %s",
                pg,
                post_id,
                journal,
            )
            print(f"      ⚠️ Страница комментариев {pg} недоступна")
            continue
        obj = extract_json_assignment(r.text, "Site.page")
        if obj and "comments" in obj:
            add(obj["comments"])
    jr = journal.replace("-", "_")
    for _ in range(50):
        todo = sorted(pending - tried)
        if not todo:
            break
        for tid in todo:
            tried.add(tid)
            pending.discard(tid)
            rpc = (
                f"https://{journal}.livejournal.com/{jr}/__rpc_get_thread?journal={jr}"
                f"&itemid={post_id}&flat=&skip=&media=&thread={tid}&expand_all=1&_={int(time.time() * 1000)}"
            )
            random_delay(DELAYS["between_comments"])
            r = fetch(session, rpc)
            if r is None or r.status_code != 200:
                LOGGER.error(
                    "Could not expand comment thread %s for post %s in %s",
                    tid,
                    post_id,
                    journal,
                )
                print(f"      ⚠️ Не удалось развернуть ветку {tid}")
                continue
            try:
                data = r.json()
            except ValueError as error:
                LOGGER.error(
                    "Invalid JSON expanding comment thread %s for post %s in %s: %s",
                    tid,
                    post_id,
                    journal,
                    error,
                )
                continue
            if debug_dir:
                with open(
                    os.path.join(debug_dir, f"{post_id}_rpc_{tid}.json"),
                    "w",
                    encoding="utf-8",
                ) as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=1)
            add(data.get("comments", []))
    expected = page_obj.get("replycount")
    if expected is not None and str(expected).isdigit() and int(expected) != len(store):
        LOGGER.error(
            "Incomplete comments for post %s in %s: got %d of %s",
            post_id,
            journal,
            len(store),
            expected,
        )
        print(f"      ⚠️ Комментариев получено {len(store)} из {expected}")
    if debug_dir:
        with open(
            os.path.join(debug_dir, f"{post_id}_Site.page.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(page_obj, fh, ensure_ascii=False, indent=1)
    return order_comments(list(store.values()))


def collect_comments_mobile(session, journal, post_id):
    url = f"https://m.livejournal.com/read/user/{journal}/{post_id}/comments"
    out, seen, n = [], set(), 0
    while url and url not in seen:
        seen.add(url)
        r = fetch(session, url)
        if r is None or r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        for node in soup.select(".b-comment"):
            n += 1
            depth = len(
                [
                    p
                    for p in node.parents
                    if p.name and "b-sub-comment" in (p.get("class") or [])
                ]
            )
            a = node.select_one(".lj-user a")
            crop = node.select_one(".item-userpic .b-userpic-crop")
            m = (
                re.search(r"url\(\s*['\"]?([^'\")]+)", crop.get("style", ""))
                if crop
                else None
            )
            text = node.select_one(".item-text")
            meta = node.select_one(".item-meta")
            out.append(
                (
                    {
                        "id": f"m{n}",
                        "parent": "0",
                        "level": depth,
                        "user": a.get_text(strip=True) if a else "",
                        "userpic": urljoin(url, m.group(1)) if m else "",
                        "date": meta.get_text(strip=True) if meta else "",
                        "subject": "",
                        "html": text.decode_contents() if text else "",
                        "deleted": False,
                        "screened": False,
                        "loaded": 1,
                    },
                    depth,
                )
            )
        nxt = soup.select_one(".paging-next a[href]")
        url = urljoin(url, nxt["href"]) if nxt else None
        if url:
            random_delay(DELAYS["between_comments"])
    return out


def render_comments(ordered, journal, assets, post_id):
    if not ordered:
        return "<p class='no-comments'>Комментариев нет.</p>"
    parts, failed = [], 0
    for c, depth in ordered:
        user = c["user"] or "(Anonymous)"
        uname = c["user"]
        if c["deleted"]:
            body = "<p class='comment-deleted'>[комментарий удалён]</p>"
        elif c["screened"] and not c["html"]:
            body = "<p class='comment-deleted'>[скрытый комментарий]</p>"
        else:
            frag = BeautifulSoup(f"<div>{c['html']}</div>", "html.parser").div
            strip_junk(frag)
            ok, bad = assets.rewrite_images(
                frag, f"https://{journal}.livejournal.com/", post_id
            )
            body = frag.decode_contents()
        pic = ""
        if c["userpic"] and uname:
            local = assets.localize(
                urljoin("https://www.livejournal.com/", c["userpic"]), uname, post_id
            )
            src = assets.rel(local or assets.placeholder())
            pic = (
                f'<img class="comment-userpic" src="{html.escape(src)}" width="50" height="50" '
                f'alt="{html.escape(uname)}">'
            )
        author = (
            f'<a class="comment-author" href="https://{html.escape(uname)}.livejournal.com/">{html.escape(user)}</a>'
            if uname
            else '<span class="comment-author">(Anonymous)</span>'
        )
        subj = (
            f'<span class="comment-subject">{html.escape(c["subject"])}</span>'
            if c["subject"]
            else ""
        )
        parts.append(
            f'<div class="comment" id="c{html.escape(c["id"])}" style="margin-left:{min(depth, 12) * 22}px">'
            f'<div class="comment-head">{pic}<div class="comment-meta">{author} '
            f'<span class="comment-date">{html.escape(c["date"])}</span>{subj}</div></div>'
            f'<div class="comment-body">{body}</div></div>'
        )
    return "\n".join(parts)


# ---------------------------------------------------------------- posts
def find_author(html_text, soup):
    """-> dict(username, display, userpic): JSON страницы, затем DOM."""
    res = {"username": "", "display": "", "userpic": ""}
    for m in re.finditer(r"(Site\.[\w.]+)\s*=\s*(?=\{)", html_text):
        try:
            obj, _ = json.JSONDecoder().raw_decode(html_text[m.end() :])
        except ValueError:
            continue
        if isinstance(obj, dict) and "poster_userpic_url" in obj:
            res.update(
                username=obj.get("poster") or obj.get("journal") or "",
                display=obj.get("poster_display_name") or "",
                userpic=obj.get("poster_userpic_url") or "",
            )
            break
    if not res["userpic"]:
        sels = [
            "dt.b-singlepost-author-userpic img",
            "dt.aentry-head__userpic img",
            "[class*=author-userpic] img",
            "[class*=userpic] img",
            ".p-author img",
            ".h-card img",
            ".author img",
            ".vcard img",
        ]
        for sel in sels:
            img = soup.select_one(sel)
            if img and (img.get("src") or img.get("data-src")):
                res["userpic"] = img.get("src") or img.get("data-src")
                holder = img.find_parent(attrs={"lj:user": True})
                res["username"] = res["username"] or (
                    holder.get("lj:user") if holder else ""
                )
                break
    return res


def parse_author(soup, raw_text, journal, post_url, assets, post_id):
    info = find_author(raw_text, soup)
    for sel in ("dl.aentry-head__userpanel", "dl.b-singlepost-author", "dl.p-author"):
        for t in soup.select(sel):
            t.decompose()
    owner = info["username"] or journal
    name = info["display"] or owner
    img_html = ""
    if info["userpic"]:
        wrap = soup.new_tag("div")
        wrap.append(soup.new_tag("img", src=info["userpic"], alt=owner))
        assets.rewrite_images(wrap, post_url, post_id, owner=owner)
        img_html = str(wrap.img)
    if not (img_html or name):
        return ""
    name_html = (
        f'<span class="post-author-name"><a href="index.html">{html.escape(name)}</a></span>'
        if name
        else ""
    )
    return f'<div class="post-author">{img_html}{name_html}</div>'


def clean_title(s):
    text = BeautifulSoup(s or "", "html.parser").get_text(" ", strip=True)
    return " ".join(text.split()) or UNTITLED


def get_post_title(raw_text, soup):
    # 1) JSON страницы: Site.entry = {"title": ...}. Пустая строка означает «без заголовка».
    for m in re.finditer(r"Site\.entry\s*=\s*(?=\{)", raw_text):
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw_text[m.end() :])
        except ValueError:
            continue
        if isinstance(obj, dict) and "title" in obj:
            return clean_title(obj["title"])
    # 2) Заголовочные теги разных оформлений
    for sel in (
        "h1.aentry-post__title",
        "h1.b-singlepost-title",
        ".b-singlepost-title",
        ".entry-title",
        "h3.entry-header",
        "h1",
    ):
        t = soup.select_one(sel)
        if t:
            return clean_title(t.get_text())
    return UNTITLED


def process_posts(session, journal, posts, assets):
    out_dir = os.path.join(ROOT_FOLDER, journal)
    os.makedirs(out_dir, exist_ok=True)
    debug_dir = None
    if DEBUG:
        debug_dir = os.path.join(out_dir, "debug")
        os.makedirs(debug_dir, exist_ok=True)
    print(f"\n📂 Папка сохранения: {out_dir}")
    for i, (url, archive_date) in enumerate(posts, 1):
        m = re.search(r"/(\d+)\.html", url)
        if not m:
            LOGGER.error("Skipping post URL without a numeric ID: %s", url)
            continue
        post_id = m.group(1)
        date_str = archive_date.replace("-", "_")
        print(f"\n[{i}/{len(posts)}] 📝 Пост #{post_id} ({url})")
        target = os.path.join(out_dir, f"{post_id}.html")
        if os.path.exists(target) and not FORCE:
            print("   ✅ Файл уже существует, пропускаем.")
            continue
        try:
            r = fetch(session, f"{url}?noscroll#comments", timeout=60)
            if r is None or r.status_code != 200:
                print(
                    f"   ❌ Пост недоступен: {getattr(r, 'status_code', 'нет ответа')}"
                )
                continue
            if r.encoding != "utf-8":
                r.encoding = r.apparent_encoding
            raw_text = r.text
            if debug_dir:
                with open(
                    os.path.join(debug_dir, f"{post_id}_post.html"),
                    "w",
                    encoding="utf-8",
                ) as fh:
                    fh.write(raw_text)
            soup = BeautifulSoup(raw_text, "html.parser")
            title = get_post_title(raw_text, soup)

            ordered = None
            if not FORCE_MOBILE:
                print("   💬 Комментарии (JSON страницы + развёртывание веток)...")
                ordered = collect_comments_desktop(
                    session, journal, post_id, url, raw_text, soup, debug_dir
                )
            if ordered is None:
                print("   💬 Комментарии (мобильная версия, все страницы)...")
                ordered = collect_comments_mobile(session, journal, post_id)
            print(f"      получено: {len(ordered)}")

            for t in soup.select(".category-panel"):
                t.decompose()
            author_markup = parse_author(soup, raw_text, journal, url, assets, post_id)
            content = (
                soup.find(class_="entry-content")
                or soup.find(class_="b-singlepost-body")
                or soup.find("article")
                or soup.find("div", {"id": "entry-text"})
                or soup.find("body")
            )
            for t in content.select("h1.aentry-post__title"):
                t.decompose()
            strip_junk(content)
            ok, bad = assets.rewrite_images(content, url, post_id)
            if ok or bad:
                print(f"   🖼️ Картинок: {ok} сохранено, {bad} заменено заглушкой")
            comments_html = render_comments(ordered, journal, assets, post_id)
            t_esc = html.escape(title)
            final = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{t_esc}</title>
    <meta name="lj-post-id" content="{post_id}">
    <meta name="lj-date" content="{archive_date}">
    <meta name="lj-comments" content="{len(ordered)}">
    <link rel="stylesheet" href="../css/lj.css" type="text/css">
</head>
<body>
    <div class="container">
        <div class="nav"><a href="index.html">&larr; {html.escape(journal)}</a></div>
        <div class="post-heading">
            {author_markup}
            <h1 class="post-title">{t_esc}</h1>
        </div>
        <div class="meta">
            <a href="{html.escape(url)}" target="_blank">Оригинал</a> | ID: {post_id} | Дата: {date_str}
        </div>
        <div class="content">
            {content}
        </div>
        <div class="comments-section">
            <div class="comments-header">Комментарии ({len(ordered)})</div>
            {comments_html}
        </div>
    </div>
</body>
</html>"""
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(final)
            print(f"   ✅ Сохранено: {post_id}.html")
        except Exception as e:  # noqa: BLE001
            LOGGER.exception("Failed to process post %s for %s", post_id, journal)
            print(f"   ❌ Ошибка с постом: {e}")
        random_delay(DELAYS["between_posts"])


# ---------------------------------------------------------------- index
def read_post_meta(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read(20000)
    pid = os.path.splitext(os.path.basename(path))[0]
    d = re.search(r'name="lj-date" content="(\d{4})-(\d{2})-(\d{2})"', text)
    if not d:
        d = re.search(r"Дата:\s*(\d{4})_(\d{2})_(\d{2})", text)
    t = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    c = re.search(r'name="lj-comments" content="(\d+)"', text)
    return {
        "id": pid,
        "date": "-".join(d.groups()) if d else "",
        "title": html.unescape(t.group(1).strip()) if t else pid,
        "comments": int(c.group(1)) if c else None,
    }


def nav_html(prev, nxt):
    def link(p, cls, label):
        if not p:
            return f'<span class="{cls} disabled">{label}</span>'
        return (
            f'<a class="{cls}" rel="{cls}" href="{p["id"]}.html" '
            f'title="{html.escape(p["title"], quote=True)}">{label}</a>'
        )

    return (
        NAV_START
        + '<div class="post-nav">'
        + link(prev, "prev", "&larr; Предыдущая")
        + '<a class="up" href="index.html">Оглавление</a>'
        + link(nxt, "next", "Следующая &rarr;")
        + "</div>"
        + NAV_END
    )


def update_navigation(out_dir, posts):
    dated = sorted(
        (p for p in posts if p["date"]), key=lambda p: (p["date"], int(p["id"]))
    )
    changed = 0
    for i, p in enumerate(dated):
        path = os.path.join(out_dir, f"{p['id']}.html")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        block = nav_html(
            dated[i - 1] if i else None, dated[i + 1] if i + 1 < len(dated) else None
        )
        if NAV_RE.search(text):
            new = NAV_RE.sub(lambda m: block, text)
        else:
            new = text.replace(
                '<div class="container">', '<div class="container">\n' + block, 1
            )
            new = re.sub(
                r"(\s*</div>\s*</body>)",
                lambda m: "\n" + block + m.group(1),
                new,
                count=1,
            )
        if new != text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            changed += 1
    return changed


def build_index(journal):
    out_dir = os.path.join(ROOT_FOLDER, journal)
    if not os.path.isdir(out_dir):
        LOGGER.error("Cannot build archive index; journal folder is missing: %s", out_dir)
        print(f"Папка {out_dir} не найдена.")
        return
    posts = [
        read_post_meta(os.path.join(out_dir, f))
        for f in os.listdir(out_dir)
        if f.endswith(".html") and f != "index.html" and f[:-5].isdigit()
    ]
    update_navigation(out_dir, posts)
    tree = defaultdict(lambda: defaultdict(list))
    for p in posts:
        y, mo = (p["date"][:4], p["date"][5:7]) if p["date"] else ("0000", "00")
        tree[y][mo].append(p)
    body = []
    for y in sorted(tree, reverse=True):
        cnt = sum(len(v) for v in tree[y].values())
        label = y if y != "0000" else "Без даты"
        months = []
        for mo in sorted(tree[y], reverse=True):
            items = sorted(tree[y][mo], key=lambda p: (p["date"], int(p["id"])))
            mname = MONTH_NAMES[int(mo)] if mo != "00" else "—"
            lis = "".join(
                f'<li><span class="d">{html.escape(p["date"][8:10] or "")}</span> '
                f'<a href="{p["id"]}.html">{html.escape(p["title"])}</a>'
                + (
                    f' <span class="c">[{p["comments"]}]</span>'
                    if p["comments"]
                    else ""
                )
                + "</li>"
                for p in items
            )
            months.append(
                f'<details class="month"><summary>{mname} <span class="n">({len(items)})</span></summary>'
                f"<ul>{lis}</ul></details>"
            )
        body.append(
            f'<details class="year"><summary>{label} <span class="n">({cnt})</span></summary>'
            + "".join(months)
            + "</details>"
        )
    page = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{html.escape(journal)} — архив ЖЖ</title>
    <link rel="stylesheet" href="../css/lj.css" type="text/css">
</head>
<body class="index-page">
    <div class="container">
        <h1><a href="https://{html.escape(journal)}.livejournal.com/">{html.escape(journal)}</a> — архив</h1>
        <p class="index-info">Постов: {len(posts)}. Обновлено: {datetime.now().strftime("%Y-%m-%d %H:%M")}.
        <a href="#" onclick="document.querySelectorAll('details').forEach(d=>d.open=true);return false">Развернуть всё</a> |
        <a href="#" onclick="document.querySelectorAll('details').forEach(d=>d.open=false);return false">Свернуть всё</a></p>
        {"".join(body)}
    </div>
</body>
</html>"""
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"📑 index.html: {len(posts)} постов")


# ---------------------------------------------------------------- css
def ensure_css():
    css_dir = os.path.join(ROOT_FOLDER, "css")
    css_path = os.path.join(css_dir, "lj.css")
    os.makedirs(css_dir, exist_ok=True)
    if not os.path.exists(css_path):
        src = os.path.join(HERE, "css", "lj.css")
        if os.path.exists(src):
            shutil.copy(src, css_path)
        else:
            LOGGER.error("CSS stylesheet not found at %s; archive styling is unavailable", src)
            print(f"⚠️ Файл стилей не найден: {src}")
            print("   ❌ CSS не создан, внешний вид может быть нарушен.")
            print(
                "   ❌ Пожалуйста, скачайте lj.css из репозитория и поместите в папку css/."
            )


# ---------------------------------------------------------------- main
def ask_date(prompt, default_month, last_day):
    print(prompt)
    parts = input(" > ").strip().split("-")
    y = int(parts[0])
    mo = int(parts[1]) if len(parts) > 1 else default_month
    if not 1 <= mo <= 12:
        raise ValueError("month")
    d = (
        int(parts[2])
        if len(parts) > 2
        else (1 if not last_day else calendar.monthrange(y, mo)[1])
    )
    return date(y, mo, d)


def main():
    if sys.platform == "win32":
        os.system("chcp 65001 > nul")
    print("=" * 60 + "\n📚 СКАЧИВАНИЕ LIVEJOURNAL\n" + "=" * 60)
    print("\n📝 Введите название журнала (username):")
    journal = input(" > ").strip()
    if not journal:
        LOGGER.error("Journal username was empty")
        sys.exit("❌ Пустое имя.")
    ensure_css()
    if INDEX_ONLY:
        build_index(journal)
        return
    session = requests.Session()
    session.headers.update(HEADERS)
    for k, v in load_cookies_or_confirm().items():
        session.cookies.set(k, v, domain=".livejournal.com")
    try:
        start = ask_date(
            "\n📅 НАЧАЛЬНАЯ ДАТА (YYYY, YYYY-MM или YYYY-MM-DD):", 1, False
        )
        end = ask_date("📅 КОНЕЧНАЯ ДАТА (YYYY, YYYY-MM или YYYY-MM-DD):", 12, True)
    except ValueError:
        LOGGER.error("Invalid date range entered")
        sys.exit("❌ Введите корректную дату.")
    if start > end:
        start, end = end, start
    assets = Assets(session, ROOT_FOLDER, journal)
    total = 0
    try:
        for year in range(start.year, end.year + 1):
            posts = scan_year(
                session, journal, str(year), start.isoformat(), end.isoformat()
            )
            total += len(posts)
            if posts:
                print(f"\n📆 === {year} ({len(posts)} постов) ===")
                process_posts(session, journal, posts, assets)
                build_index(journal)
            if year < end.year:
                random_delay(DELAYS["between_years"])
    finally:
        build_index(journal)
    print(
        f"\n📊 ИТОГО НАЙДЕНО: {total}\n🎉 ГОТОВО. Папка: {os.path.join(ROOT_FOLDER, journal)}"
    )


if __name__ == "__main__":
    try:
        configure_logging()
        with keep.running() as mode:
            if not mode.active:
                LOGGER.error("Could not prevent the system from sleeping")
                print("⚠️ Не удалось запретить сон системы.")
            main()
    except Exception:
        LOGGER.exception("SaveLj terminated unexpectedly")
        raise
    input("\nНажмите Enter, чтобы выйти...")
