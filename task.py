import os
import sqlite3
import time
from email.utils import parsedate_to_datetime

import requests

from bs4 import BeautifulSoup


API_URL = 'https://api.github.com/orgs/google/repos'
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'Github_google_repositories.db'
)
PER_PAGE = 100
MAX_RETRIES = 5


def fetch_repositories():
    """Fetch every repository page, following GitHub's pagination links."""
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'google-repository-collector',
    }
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'

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
    url = API_URL
    params = {'per_page': PER_PAGE, 'type': 'all'}

    with requests.Session() as session:
        while url:
            # Be gentle to GitHub: pause before each page request.
            time.sleep(1)
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = session.get(
                        url, params=params, headers=headers, timeout=30
                    )
                except requests.RequestException as exc:
                    if attempt == MAX_RETRIES:
                        raise RuntimeError(
                            f'GitHub APIへの接続に失敗しました: {url}: {exc}'
                        ) from exc
                    wait = min(60, 2 ** attempt)
                    print(f'接続エラー。{wait}秒後に再試行します ({attempt}/{MAX_RETRIES})')
                    time.sleep(wait)
                    continue

                if response.status_code == 200:
                    break

                # Rate limiting: wait until reset when GitHub supplies it.
                if response.status_code == 403 and response.headers.get(
                    'X-RateLimit-Remaining'
                ) == '0':
                    reset_at = response.headers.get('X-RateLimit-Reset')
                    wait = max(1, int(reset_at) - int(time.time()) + 1) if reset_at else 60
                elif response.status_code in (429, 500, 502, 503, 504):
                    wait = min(60, 2 ** attempt)
                else:
                    response.raise_for_status()

                if attempt == MAX_RETRIES:
                    response.raise_for_status()
                print(f'HTTP {response.status_code}。{wait}秒後に再試行します ({attempt}/{MAX_RETRIES})')
                time.sleep(wait)
            else:
                raise RuntimeError(f'GitHub APIから取得できませんでした: {url}')

            page_data = response.json()
            if not isinstance(page_data, list):
                raise RuntimeError(f'APIの応答形式が想定外です: {url}')
            if not page_data:
                break

            for item in page_data:
                name = item.get('name')
                if not name:
                    continue
                repositories[name] = {
                    'name': name,
                    'language': item.get('language') or '',
                    'stars': str(item.get('stargazers_count', 0)),
                }

            print(f'取得済み: {len(repositories)} リポジトリ')
            next_link = response.links.get('next', {}).get('url')
            url = next_link
            params = None  # The next URL already includes its query parameters.

    return repositories


def save_repositories(repositories):
    # Replace the table only after every page has been fetched successfully.
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS repositories (
                name TEXT PRIMARY KEY,
                language TEXT,
                stars TEXT
            )
        ''')
        conn.execute('DELETE FROM repositories')
        conn.executemany(
            '''
            INSERT INTO repositories (name, language, stars)
            VALUES (?, ?, ?)
            ''',
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