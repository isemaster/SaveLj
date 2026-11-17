# LiveJournal Backup Tool

A Python script to create a full local backup of a LiveJournal blog. The script asks for the target username, validates if the journal exists, and downloads all posts, images, and comments into a local folder.

## Features

*   **Interactive Input:** Upon launch, it requests the LiveJournal username.
*   **Validation:** Checks if the journal exists before starting. If the journal is not found (404), the script terminates with the message "нет такого журнал" (no such journal).
*   **Offline Content:** Downloads posts as `.html` files for offline reading.
*   **Image Archiving:** Finds images within posts, downloads them locally, and updates the links in the HTML to point to the local files.
*   **Author Details:** Keeps the post author's name and userpic beside the title, and saves post and comment userpics locally.
*   **Comments Backup:** Saves comments with each post, including commenter details and locally archived userpics.
*   **Year-by-Year Processing:** Sorts posts by date (oldest first), downloads a whole year before scanning the next.
*   **Archive Index:** Generates an `index.html` with posts grouped by year and month.
*   **Organized Structure:** Stores the journal archive and shared assets under the configured root folder.

## Requirements

*   Python 3.x
*   `requests`
*   `beautifulsoup4`

## Installation

1.  Clone the repository or download the script.
2.  Install the required dependencies:
    ```bash
    pip install requests beautifulsoup4
    ```

## Usage

1.  Run the script:
    ```bash
    python SaveLj.py
    ```
2.  Optionally, set the output root in `config.txt` next to `SaveLj.py`:
    ```text
    ROOT_DIR = D:\path\to\backup
    ```
    If `config.txt` is missing or has no `ROOT_DIR` setting, output goes to the `output` folder next to `SaveLj.py`. Relative paths in the config are resolved from that folder.
3.  To access locked posts, create `cookies.txt` next to `SaveLj.py` and add one cookie per line using this format:
    ```text
    name: "values"
    ```
    The file is ignored by Git so your session cookies are not committed. If it is missing, the script asks whether to continue with access to public posts only; an empty file has the same effect.
4.  Enter the LiveJournal username when prompted (e.g., `mi3ch`).
5.  The script will verify the URL. If valid, it will create the output folders and start downloading.

## Output Structure

```text
ROOT_DIR/                   # Defaults to ./output when ROOT_DIR is not configured
├── css/
│   └── lj.css              # Styles shared by posts and the archive index
├── lj_icons/
│   └── ...                 # Downloaded LiveJournal service icons and missing.svg
├── userpics/
│   └── username/
│       └── ...             # Shared userpics used by post and comment authors
└── journal_username/
    ├── index.html          # Archive index grouped by year and month
    ├── 12345.html          # One post per file; comments are included in the page
    ├── images/
    │   └── ...             # Images used in posts and comments
    └── debug/              # Optional raw response files when run with --debug
```

`ROOT_DIR` is the path configured in `config.txt`. The `css`, `lj_icons`, and
`userpics` folders are shared between journal archives; each journal's posts,
index, and other images are kept in its own folder. The `debug` folder is
created only when the script is run with `--debug`.

------------------------------------

# Скрипт для бекапа LiveJournal (ЖЖ)

Python-скрипт для создания полной локальной копии блога LiveJournal. Скрипт запрашивает имя пользователя, проверяет существование журнала и скачивает все посты, изображения и комментарии в локальную папку.

## Возможности

*   **Интерактивный ввод:** При запуске скрипт спрашивает название журнала (username).
*   **Проверка существования:** Перед началом работы проверяет доступность журнала. Если журнал не найден (ошибка 404), скрипт завершает работу с сообщением «нет такого журнал».
*   **Офлайн чтение:** Сохраняет посты в виде `.html` файлов.
*   **Сохранение изображений:** Находит изображения в постах, скачивает их в локальную папку и обновляет ссылки в HTML, чтобы они работали без интернета.
*   **Данные автора:** Сохраняет имя автора и userpic рядом с заголовком записи; userpic авторов комментариев также сохраняются локально.
*   **Скачивание комментариев:** Сохраняет комментарии внутри файла записи вместе с данными авторов.
*   **Обработка по годам:** Сортирует записи по дате (сначала старые), скачивает весь год перед сканированием следующего.
*   **Индекс архива:** Создает `index.html` со списком записей, сгруппированных по годам и месяцам.
*   **Структура папок:** Сохраняет архив журнала и общие ресурсы в корневой папке из конфигурации.

## Требования

*   Python 3.x
*   `requests`
*   `beautifulsoup4`

## Установка

1.  Скачайте скрипт.
2.  Установите необходимые библиотеки:
    ```bash
    pip install requests beautifulsoup4
    ```

## Использование

1.  Запустите скрипт:
    ```bash
    python SaveLj.py
    ```
2.  При необходимости укажите корневую папку для сохранения в `config.txt` рядом с `SaveLj.py`:
    ```text
    ROOT_DIR = D:\path\to\backup
    ```
    Если файла `config.txt` нет или в нем не задан `ROOT_DIR`, результат сохраняется в папку `output` рядом с `SaveLj.py`. Относительные пути в конфигурации отсчитываются от этой папки.
3.  Для доступа к закрытым записям создайте рядом с `SaveLj.py` файл `cookies.txt` и добавьте по одной cookie в строку в формате:
    ```text
    name: "values"
    ```
    Файл добавлен в список игнорируемых Git, поэтому cookie сессии не попадут в коммит. Если файла нет, скрипт спросит, продолжать ли работу только с открытыми постами; пустой файл дает тот же результат.
4.  Введите имя пользователя ЖЖ, когда появится запрос (например: `tema` или `varlamov`).
5.  Скрипт проверит URL. Если журнал существует, он создаст папки для сохранения и начнет скачивание.

## Структура выходных данных

```text
ROOT_DIR/                   # По умолчанию ./output, если ROOT_DIR не задан
├── css/
│   └── lj.css              # Общие стили записей и индекса архива
├── lj_icons/
│   └── ...                 # Служебные иконки LiveJournal и missing.svg
├── userpics/
│   └── имя_пользователя/
│       └── ...             # Общие userpic авторов записей и комментариев
└── имя_журнала/
    ├── index.html          # Индекс архива по годам и месяцам
    ├── 12345.html          # Одна запись в файле, комментарии включены в страницу
    ├── images/
    │   └── ...             # Изображения записей и комментариев
    └── debug/              # Сырые ответы; создается только с параметром --debug
```

`ROOT_DIR` — путь из `config.txt`. Каталоги `css`, `lj_icons` и `userpics`
общие для всех архивов; записи, индекс и остальные изображения каждого журнала
хранятся в его отдельной папке. Каталог `debug` появляется только при запуске
с параметром `--debug`.

## Acknowledgements

Thanks to [@isemaster](https://github.com/isemaster) for the initial version of this script.

## Благодарности

Спасибо [@isemaster](https://github.com/isemaster) за первоначальную версию скрипта.
