import os
import re
import sqlite3
import time
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup


BASE_URL = 'https://github.com/orgs/google/repositories'
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'Github_google_repositories.db'
)
MAX_RETRIES = 5

REPO_HREF_RE = re.compile(r'(?:https://github\.com)?/google/([^/?#]+)/?$')
STAR_HREF_RE = re.compile(r'/google/[^/]+/stargazers/?$')
STAR_TEXT_RE = re.compile(r'[\d,.]+[kKmM]?')
LANGUAGE_NAMES = {
    'Assembly', 'Astro', 'Bazel', 'C', 'C#', 'C++', 'Clojure',
    'CoffeeScript', 'Crystal', 'CSS', 'Dart', 'Dockerfile', 'Elixir',
    'Elm', 'Erlang', 'F#', 'Fortran', 'Go', 'Groovy', 'Haskell',
    'HTML', 'Java', 'JavaScript', 'Julia', 'Jupyter Notebook', 'Kotlin',
    'Lua', 'Makefile', 'Markdown', 'MATLAB', 'Nix', 'Objective-C',
    'OCaml', 'Pascal', 'Perl', 'PHP', 'PowerShell', 'Python', 'R',
    'Ruby', 'Rust', 'Scala', 'Scheme', 'Shell', 'Solidity', 'SQL',
    'Swift', 'TypeScript', 'Vim Script', 'Vue', 'WebAssembly',
    'Wolfram Language'
}


def fetch_soup(url):
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; repository-list-reader)'}
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        # Keep a one-second interval before every page request, including retries.
        time.sleep(1)
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                response.encoding = response.apparent_encoding
                return BeautifulSoup(response.text, 'html.parser')

            last_error = RuntimeError(
                f'HTTP {response.status_code} {response.reason}: {url}'
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:
                break
            if response.status_code == 429:
                # Honor GitHub's requested wait when supplied. Otherwise back
                # off much longer than the normal one-second page interval.
                retry_after = response.headers.get('Retry-After')
                wait = None
                if retry_after:
                    try:
                        wait = max(1, int(retry_after))
                    except ValueError:
                        try:
                            wait = max(
                                1,
                                int(parsedate_to_datetime(retry_after).timestamp() - time.time())
                            )
                        except (TypeError, ValueError, OverflowError):
                            wait = None
                reset_at = response.headers.get('X-RateLimit-Reset')
                if wait is None and response.headers.get('X-RateLimit-Remaining') == '0' and reset_at:
                    try:
                        wait = max(1, int(reset_at) - int(time.time()) + 1)
                    except ValueError:
                        wait = None
                if wait is None:
                    wait = min(900, 60 * (2 ** (attempt - 1)))
            else:
                wait = min(60, 2 ** attempt)
        except requests.RequestException as exc:
            last_error = exc
            wait = min(60, 2 ** attempt)

        if attempt < MAX_RETRIES:
            delay = min(60, 2 ** attempt)
            print(f'取得に失敗しました。{delay}秒後に再試行します ({attempt}/{MAX_RETRIES})')
            time.sleep(delay)
            print(f'取得に失敗しました。{wait}秒後に再試行します ({attempt}/{MAX_RETRIES})')
            time.sleep(wait)

    raise RuntimeError(f'ページを取得できませんでした: {url} ({last_error})')


def extract_stars(card):
    star_link = card.find('a', href=STAR_HREF_RE)
    if star_link:
        text = star_link.get_text(' ', strip=True)
        match = STAR_TEXT_RE.search(text)
        if match:
            return match.group(0)

        labelled = ' '.join(filter(None, [
            star_link.get('aria-label'), star_link.get('title')
        ]))
        match = STAR_TEXT_RE.search(labelled)
        if match:
            return match.group(0)

    for text in card.stripped_strings:
        match = re.search(r'([\d,.]+[kKmM]?)\s+stars?\b', text, re.I)
        if match:
            return match.group(1)
    return ''


def extract_language(card):
    tag = card.select_one('[itemprop="programmingLanguage"]')
    if tag and tag.get_text(strip=True):
        return tag.get_text(strip=True)

    tag = card.select_one('[class*="PrimaryLanguageName"]')
    if tag and tag.get_text(strip=True):
        return tag.get_text(strip=True)

    for element in card.find_all(['span', 'a']):
        value = element.get_text(strip=True)
        if value in LANGUAGE_NAMES:
            return value
    return ''


def parse_repositories(soup):
    repositories = {}
    cards = soup.select('ul[data-listview-component="items-list"] > li')

    # Fallback for GitHub markup variations.
    if not cards:
        cards = []
        seen_cards = set()
        for link in soup.find_all('a', href=REPO_HREF_RE):
            card = link.find_parent('li') or link.parent
            if id(card) not in seen_cards:
                seen_cards.add(id(card))
                cards.append(card)

    for card in cards:
        link = card.find('a', href=REPO_HREF_RE)
        if link is None:
            continue
        match = REPO_HREF_RE.search(link.get('href', ''))
        if match is None:
            continue
        name = match.group(1)
        repositories[name] = {
            'name': name,
            'language': extract_language(card),
            'stars': extract_stars(card),
        }

    return repositories


def scrape_all_repositories():
    repositories = {}
    page = 1

    while True:
        url = BASE_URL if page == 1 else f'{BASE_URL}?page={page}'
        print(f'取得中: {url}')
        soup = fetch_soup(url)
        page_repositories = parse_repositories(soup)

        # GitHub's repository list ends with a page containing no repository cards.
        if not page_repositories:
            if page == 1:
                raise RuntimeError('1ページ目からリポジトリを取得できませんでした')
            break

        repositories.update(page_repositories)
        print(f'{len(page_repositories)}件取得（累計 {len(repositories)}件）')
        page += 1

    return repositories


def save_and_display(repositories):
    # Replace saved data only after scraping reaches the end successfully.
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute('''
            CREATE TABLE IF NOT EXISTS repositories (
                name TEXT PRIMARY KEY,
                language TEXT,
                stars TEXT
            )
        ''')
        connection.execute('DELETE FROM repositories')
        connection.executemany(
            'INSERT INTO repositories (name, language, stars) VALUES (?, ?, ?)',
            [
                (repo['name'], repo['language'], repo['stars'])
                for repo in repositories.values()
            ],
        )

        rows = connection.execute('''
            SELECT name, language, stars
            FROM repositories
            ORDER BY name
        ''').fetchall()

    print(f'取得・保存したリポジトリ数: {len(rows)}')
    for name, language, stars in rows:
        print(f'リポジトリ名: {name} | 主要な言語: {language} | スターの数: {stars}')
    print(f'DB保存先: {DB_PATH}')


def main():
    try:
        repositories = scrape_all_repositories()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f'全ページの取得が完了しなかったため、DBは更新しません: {exc}')
        return

    if not repositories:
        print('リポジトリを取得できなかったため、DBは更新しません')
        return

    save_and_display(repositories)


if __name__ == '__main__':
    main()