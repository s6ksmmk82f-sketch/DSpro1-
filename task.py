import os
import re
import sqlite3
import time

import requests
from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup

BASE_URL = 'https://github.com/orgs/google/repositories'
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'Github_google_repositories.db'
)
MAX_PAGES = 100

language_names = {
    'Assembly', 'Astro', 'Bazel', 'C', 'C#', 'C++', 'Clojure',
    'CoffeeScript', 'Crystal', 'CSS', 'Dart', 'Dockerfile', 'Elixir',
    'Elm', 'Erlang', 'F#', 'Fortran', 'Go', 'Groovy', 'Haskell',
    'HTML', 'Java', 'JavaScript', 'Julia', 'Jupyter Notebook',
    'Kotlin', 'Lua', 'Makefile', 'Markdown', 'MATLAB', 'Nix',
    'Objective-C', 'OCaml', 'Pascal', 'Perl', 'PHP', 'PowerShell',
    'Python', 'R', 'Ruby', 'Rust', 'Scala', 'Scheme', 'Shell',
    'Solidity', 'SQL', 'Swift', 'TypeScript', 'Vim Script', 'Vue',
    'WebAssembly', 'Wolfram Language'
}

REPO_HREF_RE = re.compile(r'(?:https://github\.com)?/google/([^/]+)/?$')
STAR_HREF_RE = re.compile(r'/google/[^/]+/stargazers/?$')
STAR_TEXT_RE = re.compile(r'[\d,.]+[kKmM]?')


def fetch_soup(url, retries=4):
    for attempt in range(1, retries + 1):
        time.sleep(1 if attempt == 1 else min(30, 2 ** attempt))

        try:
            res = requests.get(
                url,
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=10
            )
        except requests.RequestException as e:
            print(f"アクセスに失敗しました：{url} ({e})")
            continue

        print(f"取得中：{url}")
        print(f"ステータスコード：{res.status_code}")
        print(f"ステータスメッセージ：{res.reason}")

        if res.status_code == 429:
            print(f"レート制限のため再試行します（{attempt}/{retries}）")
            continue

        if res.status_code != 200:
            return None

        res.encoding = res.apparent_encoding

        try:
            return BeautifulSoup(res.text, 'html.parser')
        except ParserRejectedMarkup:
            print(f"解析できないため、スキップ：{url}")
            return None

    return None


def extract_stars(card, star_link=None):
    if star_link is None:
        star_link = card.find('a', href=STAR_HREF_RE)

    if star_link is not None:
        star_text = star_link.get_text(' ', strip=True)
        star_match = STAR_TEXT_RE.search(star_text)
        if star_match:
            return star_match.group(0)

        labelled = ' '.join(filter(None, [
            star_link.get('aria-label'),
            star_link.get('title')
        ]))
        star_match = STAR_TEXT_RE.search(labelled)
        if star_match:
            return star_match.group(0)

    for text in card.stripped_strings:
        star_match = re.search(
            r'([\d,.]+[kKmM]?)\s+stars?\b',
            text,
            re.I
        )
        if star_match:
            return star_match.group(1)

    return ''


def extract_language(card):
    language_tag = card.select_one('[itemprop="programmingLanguage"]')
    if language_tag:
        language = language_tag.get_text(strip=True)
        if language:
            return language

    language_tag = card.select_one('[class*="PrimaryLanguageName"]')
    if language_tag:
        language = language_tag.get_text(strip=True)
        if language:
            return language

    for element in card.find_all(['span', 'a']):
        element_text = element.get_text(strip=True)
        if element_text in language_names:
            return element_text

    return ''


def parse_repositories(html_soup):
    page_repos = {}

    items = html_soup.select(
        'ul[data-listview-component="items-list"] > li'
    )

    if not items:
        items = []
        seen = set()
        for repo_link in html_soup.find_all('a', href=REPO_HREF_RE):
            card = repo_link.find_parent('li') or repo_link.parent
            card_id = id(card)
            if card_id not in seen:
                seen.add(card_id)
                items.append(card)

    for card in items:
        repo_link = card.find('a', href=REPO_HREF_RE)
        if repo_link is None:
            continue

        href_match = REPO_HREF_RE.search(repo_link.get('href', ''))
        if href_match is None:
            continue

        repo_name = href_match.group(1)
        page_repos[repo_name] = {
            'name': repo_name,
            'language': extract_language(card),
            'stars': extract_stars(card)
        }

    if page_repos:
        return page_repos

    # カード構造が取れない場合はスターリンクから復元する
    for star_link in html_soup.find_all('a', href=STAR_HREF_RE):
        star_path = star_link.get('href', '')
        path_match = re.search(r'/google/([^/]+)/stargazers', star_path)
        if path_match is None:
            continue

        repo_name = path_match.group(1)
        page_repos[repo_name] = {
            'name': repo_name,
            'language': '',
            'stars': extract_stars(star_link.parent or star_link, star_link)
        }

    return page_repos


def main():
    repositories = {}

    for page in range(1, MAX_PAGES + 1):
        current_url = (
            BASE_URL if page == 1 else f'{BASE_URL}?page={page}'
        )
        html_soup = fetch_soup(current_url)
        if html_soup is None:
            continue

        page_repos = parse_repositories(html_soup)
        if not page_repos:
            break

        repositories.update(page_repos)

        for repo in page_repos.values():
            if not repo['language']:
                print(f"言語を取得できませんでした：{repo['name']}")
            if not repo['stars']:
                print(f"スター数を取得できませんでした：{repo['name']}")

    if not repositories:
        print('リポジトリを取得できなかったため、データベースは更新しません')
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DROP TABLE IF EXISTS repositories')
        conn.execute('''
            CREATE TABLE repositories (
                name TEXT PRIMARY KEY,
                language TEXT,
                stars TEXT
            )
        ''')

        for repo in repositories.values():
            conn.execute(
                '''
                INSERT INTO repositories (name, language, stars)
                VALUES (?, ?, ?)
                ''',
                (repo['name'], repo['language'], repo['stars'])
            )

        rows = conn.execute('''
            SELECT name, language, stars
            FROM repositories
            ORDER BY name
        ''').fetchall()

        print(f"取得したリポジトリ数：{len(rows)}")

        for name, language, stars in rows:
            print(
                f"リポジトリ名：{name} "
                f"主要言語：{language} "
                f"スターの数：{stars}"
            )


if __name__ == '__main__':
    main()
