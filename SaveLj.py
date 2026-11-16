import requests
from bs4 import BeautifulSoup
import calendar
import json
import os
import time
import re
import sys
import random
from datetime import date
from urllib.parse import urljoin, urlparse

# ================= НАСТРОЙКИ ПО УМОЛЧАНИЮ =================
USERNAME = ""
YEAR = ""
BASE_URL = ""
OUTPUT_FOLDER = ""
IMAGES_FOLDER = "images"
COMMENTS_FOLDER = "comments_raw"

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Настройки задержек
DELAYS = {
    "between_pages": (1, 3),  # между страницами журнала (уменьшил для скорости поиска)
    "between_posts": (60, 120),  # между скачиванием постов (сек)
    "between_comments": (1, 4),  # между загрузкой комментариев (сек)
    "between_days": (0.5, 2),  # между днями при сканировании
    "before_retry": (5, 10),  # перед повторной попыткой
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

# ==========================================================


def load_cookies():
    cookies = {}
    with open(COOKIE_FILE, encoding="utf-8") as cookie_file:
        for line_number, line in enumerate(cookie_file, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            name, separator, raw_value = line.partition(":")
            if not separator or not name.strip():
                raise ValueError(f'строка {line_number}: ожидается имя: "значение"')

            try:
                value = json.loads(raw_value.strip())
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"строка {line_number}: значение cookie должно быть в кавычках"
                ) from error

            if not isinstance(value, str):
                raise ValueError(
                    f"строка {line_number}: значение cookie должно быть строкой"
                )
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
    except (OSError, ValueError) as error:
        print(f"❌ Ошибка загрузки cookies из {COOKIE_FILE}: {error}")
        sys.exit(1)


def random_delay(delay_range):
    """Случайная задержка в заданном диапазоне"""
    min_delay, max_delay = delay_range
    sleep_time = random.uniform(min_delay, max_delay)
    # Можно закомментировать print, чтобы не спамить в консоль при поиске
    # print(f"⏳ Пауза {sleep_time:.1f}с...")
    time.sleep(sleep_time)


def clean_filename(text):
    return re.sub(r'[\\/*?:"<>|]', "", text).strip()[:100]


def parse_russian_date_from_text(text):
    if not text:
        return None
    text = text.lower()
    match = re.search(r"(\d{1,2})\s+([а-я]+)[,\s]+(\d{4})", text)
    if match:
        day, month_name, year = match.groups()
        month_num = RUS_MONTHS.get(month_name)
        if month_num:
            return f"{year}_{month_num}_{day.zfill(2)}"
    match_iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match_iso:
        return f"{match_iso.group(1)}_{match_iso.group(2)}_{match_iso.group(3)}"
    return None


def get_post_date(soup):
    meta_date = soup.find("meta", property="article:published_time")
    if meta_date and meta_date.get("content"):
        return meta_date.get("content").split("T")[0].replace("-", "_")

    time_tags = soup.find_all("time")
    for tag in time_tags:
        dt = tag.get("datetime")
        if dt:
            return dt.split("T")[0].replace("-", "_")
        parsed = parse_russian_date_from_text(tag.get_text())
        if parsed:
            return parsed

    potential_containers = soup.find_all(
        class_=re.compile(r"(date|time|header|meta|subject)")
    )
    for container in potential_containers:
        parsed = parse_russian_date_from_text(container.get_text())
        if parsed:
            return parsed

    full_text = soup.get_text()[:10000]
    parsed = parse_russian_date_from_text(full_text)
    if parsed:
        return parsed

    return "0000_00_00"


def download_image(session, img_url, filename_prefix, folder_path, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            parsed = urlparse(img_url)
            ext = os.path.splitext(parsed.path)[1].lower()
            if not ext or len(ext) > 5:
                ext = ".jpg"
            local_filename = f"{filename_prefix}{ext}"
            local_filepath = os.path.join(folder_path, local_filename)
            if os.path.exists(local_filepath):
                return local_filename

            img_data = session.get(img_url, timeout=15).content
            with open(local_filepath, "wb") as handler:
                handler.write(img_data)
            return local_filename
        except Exception as e:
            if attempt < max_retries:
                # print(f"⚠️ Ошибка загрузки изображения (попытка {attempt+1}): {e}")
                random_delay(DELAYS["before_retry"])
            else:
                return None


def extract_post_urls_from_page(soup, username):
    """Извлекает ссылки на посты со страницы"""
    urls = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Ищем ссылки формата username.livejournal.com/NNNNN.html
        if f"{username}.livejournal.com" in href and re.search(r"/\d+\.html$", href):
            urls.add(href)
    return urls


def get_day_urls_from_year_page(soup, username, year, start_date, end_date):
    """Извлекает ссылки на дни в заданном диапазоне дат"""
    day_urls = {}
    base = f"https://{username}.livejournal.com"

    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Ищем ссылки формата /YYYY/MM/DD/ или полный URL
        match = re.search(rf"/{year}/(\d{{2}})/(\d{{2}})/?$", href)
        if match:
            try:
                day = date(int(year), int(match.group(1)), int(match.group(2)))
            except ValueError:
                continue
            day_string = day.isoformat()
            if start_date <= day_string <= end_date:
                day_urls[day_string] = urljoin(base, href)

    return [day_url for _, day_url in sorted(day_urls.items())]


def get_archive_day_from_url(day_url):
    match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/?$", urlparse(day_url).path)
    return "-".join(match.groups()) if match else ""


def get_all_post_urls_by_year(session, start_date, end_date):
    """Сбор ссылок и дат на посты за указанный год (без скачивания контента)"""
    print(f"   🔎 Сканирую {YEAR} год...")
    unique_urls = set()
    post_dates = {}

    year_url = f"https://{USERNAME}.livejournal.com/{YEAR}/"

    try:
        r = session.get(year_url, timeout=200)

        if r.status_code == 429:
            print("      🚫 Rate limit! Ожидание...")
            random_delay((60, 120))
            r = session.get(year_url, timeout=20)

        if r.status_code != 200:
            print(f"      ❌ Страница архива недоступна: {r.status_code}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        # СПОСОБ 1: Ищем ссылки на дни с записями (календарный вид)
        day_urls = get_day_urls_from_year_page(
            soup, USERNAME, YEAR, start_date, end_date
        )

        if day_urls:
            # print(f"      📆 Найдено {len(day_urls)} дней с записями")

            for i, day_url in enumerate(sorted(day_urls)):
                try:
                    r = session.get(day_url, timeout=20)
                    if r.status_code == 200:
                        day_soup = BeautifulSoup(r.text, "html.parser")
                        found_urls = extract_post_urls_from_page(day_soup, USERNAME)
                        unique_urls.update(found_urls)
                        day_date = get_archive_day_from_url(day_url)
                        for post_url in found_urls:
                            post_dates[post_url] = day_date
                    random_delay(DELAYS["between_days"])
                except Exception:  # noqa: B110, S110, BLE001
                    pass

        else:
            # СПОСОБ 2: Посты отображаются прямо на странице года (с пагинацией)
            year_start = f"{YEAR}-01-01"
            year_end = f"{YEAR}-12-31"
            if start_date <= year_start and year_end <= end_date:
                unique_urls.update(extract_post_urls_from_page(soup, USERNAME))

            # Проверяем пагинацию - сканируем по месяцам (упрощенно)
            for month in range(1, 13):
                month_url = f"https://{USERNAME}.livejournal.com/{YEAR}/{month:02d}/"
                try:
                    r = session.get(month_url, timeout=15)
                    if r.status_code == 200:
                        month_soup = BeautifulSoup(r.text, "html.parser")
                        found = extract_post_urls_from_page(month_soup, USERNAME)

                        # Также ищем дни внутри месяца
                        month_day_urls = get_day_urls_from_year_page(
                            month_soup,
                            USERNAME,
                            YEAR,
                            start_date,
                            end_date,
                        )

                        for d_url in month_day_urls:
                            r_d = session.get(d_url, timeout=15)
                            if r_d.status_code == 200:
                                d_soup = BeautifulSoup(r_d.text, "html.parser")
                                day_posts = extract_post_urls_from_page(
                                    d_soup, USERNAME
                                )
                                unique_urls.update(day_posts)
                                day_date = get_archive_day_from_url(d_url)
                                for post_url in day_posts:
                                    post_dates[post_url] = day_date
                                time.sleep(0.5)

                        month_start = f"{YEAR}-{month:02d}-01"
                        month_end = (
                            f"{YEAR}-{month:02d}-"
                            f"{calendar.monthrange(int(YEAR), month)[1]:02d}"
                        )
                        if (
                            not month_day_urls
                            and start_date <= month_start
                            and month_end <= end_date
                        ):
                            unique_urls.update(found)

                except Exception:  # noqa: B110, S110, BLE001
                    pass  # noruff
                random_delay(DELAYS["between_pages"])

    except Exception as e:  # noqa: BLE001
        print(f"      ❌ Ошибка сканирования года: {e}")

    print(f"      ✅ Найдено постов в {YEAR} году: {len(unique_urls)}")
    return sorted(
        unique_urls,
        key=lambda url: (post_dates.get(url) or "9999-99-99", url),
    )


def get_mobile_comments(session, username, post_id, raw_save_path):
    mobile_url = f"https://m.livejournal.com/read/user/{username}/{post_id}/comments"
    print(f"   💬 Загружаю комментарии...")

    try:
        r = session.get(mobile_url, timeout=25)

        with open(raw_save_path, "w", encoding="utf-8") as f:
            f.write(r.text)

        if r.status_code == 429:
            return "<p>Комментарии временно недоступны (rate limit).</p>"
        if r.status_code != 200:
            return f"<p>Ошибка доступа к комментариям (код {r.status_code}).</p>"

        soup = BeautifulSoup(r.text, "html.parser")

        comments_container = (
            soup.find(class_="b-tree")
            or soup.find(class_="comments-body")
            or soup.find("section", class_="comments")
            or soup.find("div", id="comments")
            or soup.find(class_="b-comments")
        )

        if not comments_container:
            main_wrapper = soup.find("main") or soup.find("div", class_="app-widget")
            if main_wrapper:
                comments_container = main_wrapper
            else:
                return "<p>Комментариев нет или не удалось извлечь.</p>"

        for junk in comments_container.find_all(
            ["form", "input", "textarea", "button", "script", "style"]
        ):
            junk.decompose()
        for a in comments_container.find_all("a"):
            txt = a.get_text().lower()
            if (
                "ответить" in txt
                or "reply" in txt
                or "expand" in txt
                or "развернуть" in txt
            ):
                a.decompose()
        for img in comments_container.find_all("img"):
            src = img.get("src")
            if src:
                img["src"] = urljoin(mobile_url, src)

        return str(comments_container)

    except Exception as e:  # noqa: BLE001
        return f"<p>Ошибка при обработке комментариев: {e}</p>"


def process_posts(session, urls, current_output_folder):
    """Скачивание списка постов в указанную папку"""
    if not os.path.exists(current_output_folder):
        os.makedirs(current_output_folder)
    images_dir = os.path.join(current_output_folder, IMAGES_FOLDER)
    if not os.path.exists(images_dir):
        os.makedirs(images_dir)
    comments_raw_dir = os.path.join(current_output_folder, COMMENTS_FOLDER)
    if not os.path.exists(comments_raw_dir):
        os.makedirs(comments_raw_dir)

    print(f"\n📂 Папка сохранения: {current_output_folder}")

    for i, url in enumerate(urls):
        try:
            post_id_match = re.search(r"/(\d+)\.html", url)
            if not post_id_match:
                continue
            post_id = post_id_match.group(1)

            print(f"\n[{i + 1}/{len(urls)}] 📝 Пост #{post_id} ({url})")

            # Проверка, скачан ли уже файл
            # (нужно знать дату заранее, но мы не знаем, поэтому проверим после загрузки или примерно)
            # Для упрощения грузим страницу.

            r = session.get(url, timeout=250)

            if r.status_code == 429:
                print("   🚫 Rate limit! Большая пауза...")
                random_delay((120, 180))
                # Можно добавить retry
                continue

            if r.status_code != 200:
                print(f"   ❌ Пост недоступен: {r.status_code}")
                continue

            if r.encoding != "utf-8":
                r.encoding = r.apparent_encoding
            soup = BeautifulSoup(r.text, "html.parser")
            for category_panel in soup.select(".category-panel"):
                category_panel.decompose()
            date_str = get_post_date(soup)

            title_tag = (
                soup.find("h1")
                or soup.find("h3", class_="entry-header")
                or soup.find("title")
            )
            title_text = title_tag.get_text(strip=True) if title_tag else "No Title"
            safe_title = clean_filename(title_text)

            html_filename = f"{date_str}_{post_id}_{safe_title}.html"
            html_filepath = os.path.join(current_output_folder, html_filename)

            if os.path.exists(html_filepath):
                print("   ✅ Файл уже существует, пропускаем.")
                continue

            content = (
                soup.find(class_="entry-content")
                or soup.find(class_="b-singlepost-body")
                or soup.find("article")
                or soup.find("div", {"id": "entry-text"})
            )
            if not content:
                content = soup.find("body")

            # Картинки
            images = content.find_all("img")
            if images:
                print(f"   🖼️ Обработка {len(images)} картинок...")
                img_counter = 1
                for img in images:
                    src = img.get("src")
                    if not src:
                        continue
                    abs_url = urljoin(url, src)
                    img_prefix = f"{date_str}_{post_id}_{img_counter}"
                    local_img_name = download_image(
                        session, abs_url, img_prefix, images_dir
                    )
                    if local_img_name:
                        img["src"] = f"{IMAGES_FOLDER}/{local_img_name}"
                        # Убираем жесткие размеры
                        for attr in ["width", "height", "style", "srcset"]:
                            if img.has_attr(attr):
                                del img[attr]
                    img_counter += 1

            # Комментарии
            raw_comment_filename = f"comments_{post_id}.html"
            raw_comment_path = os.path.join(comments_raw_dir, raw_comment_filename)
            random_delay(DELAYS["between_comments"])
            comments_html = get_mobile_comments(
                session, USERNAME, post_id, raw_comment_path
            )

            # Сохранение HTML
            final_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <title>{title_text}</title>
    <style>
        body {{ font-family: 'Verdana', sans-serif; max-width: 800px; margin: auto; padding: 20px; background: #f4f4f4; }}
        .container {{ background: #fff; padding: 30px; border-radius: 5px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
        img {{ max-width: 100%; height: auto; display: block; margin: 15px 0; border-radius: 3px; }}
        h1 {{ color: #333; }}
        .meta {{ color: #777; font-size: 0.9em; margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 10px; }}
        a {{ color: #0066cc; text-decoration: none; }}
        .comments-section {{ margin-top: 50px; border-top: 2px solid #eee; padding-top: 20px; }}
        .comments-header {{ font-size: 1.5em; font-weight: bold; margin-bottom: 20px; }}
        /* Стиль для комментариев с мобильной версии может требовать доработки CSS */
        .b-tree {{ padding-left: 0; }}
        .b-tree__item {{ list-style: none; margin-bottom: 20px; border-left: 3px solid #e0e0e0; padding-left: 15px; }}
        .b-tree__item .b-tree__item {{ margin-left: 15px; margin-top: 10px; border-left: 2px solid #ccc; }}
        .userbox {{ font-weight: bold; color: #444; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title_text}</h1>
        <div class="meta">
            <a href='{url}' target='_blank'>Оригинал</a> | ID: {post_id} | Дата: {date_str}
        </div>
        <div class="content">
            {str(content)}
        </div>
        
        <div class="comments-section">
            <div class="comments-header">Комментарии</div>
            {comments_html}
        </div>
    </div>
</body>
</html>"""

            with open(html_filepath, "w", encoding="utf-8") as f:
                f.write(final_html)

            print(f"   ✅ Сохранено: {html_filename}")

        except Exception as e:
            print(f"   ❌ Ошибка с постом: {e}")

        random_delay(DELAYS["between_posts"])


def process_years(session, start_year, end_year, start_date, end_date):
    global YEAR
    total_posts_found = 0

    for year in range(start_year, end_year + 1):
        YEAR = str(year)
        urls = get_all_post_urls_by_year(session, start_date, end_date)
        total_posts_found += len(urls)

        if urls:
            print(f"\n📆 === ОБРАБОТКА {YEAR} ГОДА ({len(urls)} постов) ===")
            current_year_folder = os.path.join(USERNAME, YEAR)
            process_posts(
                session,
                urls,
                current_year_folder,
            )

        if year < end_year:
            time.sleep(100)

    return total_posts_found


# ================= ГЛАВНЫЙ БЛОК =================
if __name__ == "__main__":
    # Исправляем кодировку консоли для Windows, чтобы не было кракозябр
    if sys.platform == "win32":
        os.system("chcp 65001 > nul")

    print("=" * 60)
    print("📚 СКАЧИВАНИЕ LIVEJOURNAL (Диапазон лет)")
    print("=" * 60)

    # 1. Запрос имени пользователя
    # Выносим эмодзи в print, чтобы не ломать курсор
    print("\n📝 Введите название журнала (username):")
    user_input = input(" > ").strip()

    if not user_input:
        print("❌ Пустое имя.")
        sys.exit()
    USERNAME = user_input
    BASE_URL = f"https://{USERNAME}.livejournal.com"

    cookies = load_cookies_or_confirm()

    session = requests.Session()
    session.headers.update(HEADERS)
    for cookie_name, cookie_value in cookies.items():
        session.cookies.set(cookie_name, cookie_value, domain=".livejournal.com")

    # 2. Запрос диапазона лет
    try:
        print(
            "\n📅 Введите НАЧАЛЬНУЮ ДАТУ (год, год и месяц в формате YYYY-MM, или дату в формате YYYY-MM-DD,",
            "\n    например, 2003, 2011-01 или 2023-01-15):",
        )
        start_date_input = input(" > ").strip()
        start_date_parts = start_date_input.split("-")
        start_year_input = int(start_date_parts[0])
        if len(start_date_parts) > 1:
            start_month_input = int(start_date_parts[1])
            if start_month_input < 1 or start_month_input > 12:
                print("❌ Месяц должен быть от 1 до 12.")
                sys.exit()
        else:
            start_month_input = 1
        if len(start_date_parts) > 2:
            start_day_input = int(start_date_parts[2])
            if start_day_input < 1 or start_day_input > 31:
                print("❌ День должен быть от 1 до 31.")
                sys.exit()
        else:
            start_day_input = 1

        print(
            "📅 Введите КОНЕЧНУЮ ДАТУ (год, год и месяц в формате YYYY-MM, или дату в формате YYYY-MM-DD,",
            "\n    например, 2025, 2025-12 или 2025-12-31):",
        )
        end_date_input = input(" > ").strip()
        end_date_parts = end_date_input.split("-")
        end_year_input = int(end_date_parts[0])
        if len(end_date_parts) > 1:
            end_month_input = int(end_date_parts[1])
            if end_month_input < 1 or end_month_input > 12:
                print("❌ Месяц должен быть от 1 до 12.")
                sys.exit()
        else:
            end_month_input = 12
        if len(end_date_parts) > 2:
            end_day_input = int(end_date_parts[2])
            if end_day_input < 1 or end_day_input > 31:
                print("❌ День должен быть от 1 до 31.")
                sys.exit()
        else:
            # last day of the month calculated from the month and year
            end_day_input = calendar.monthrange(end_year_input, end_month_input)[1]

        start_date_value = date(start_year_input, start_month_input, start_day_input)
        end_date_value = date(end_year_input, end_month_input, end_day_input)
        if start_date_value > end_date_value:
            print("❌ Начальная дата позже конечной. Меняю местами...")
            start_date_value, end_date_value = end_date_value, start_date_value

        start_date_key = start_date_value.isoformat()
        end_date_key = end_date_value.isoformat()
        start_year_input = start_date_value.year
        end_year_input = end_date_value.year
    except ValueError:
        print("❌ Введите корректную дату в формате YYYY, YYYY-MM или YYYY-MM-DD.")
        sys.exit()

    print(f"\n🔍 Поиск постов в диапазоне {start_year_input} - {end_year_input}...")

    total_posts_found = process_years(
        session,
        start_year_input,
        end_year_input,
        start_date_key,
        end_date_key,
    )
    if total_posts_found == 0:
        print("❌ За указанный период постов не найдено.")
        sys.exit()

    print(f"📊 ИТОГО НАЙДЕНО: {total_posts_found} постов.")

    print("\n" + "=" * 60)
    print("🎉 ВСЕ ЗАДАЧИ ЗАВЕРШЕНЫ!")
    print(f"📁 Папка с архивом: {USERNAME}/")
    print("=" * 60)

    # Добавляем ожидание в конце, чтобы консоль не закрылась мгновенно
    input("\nНажмите Enter, чтобы выйти...")
