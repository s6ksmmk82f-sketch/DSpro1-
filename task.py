import time
import requests
import sqlite3
from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup
from urllib.parse import urljoin, urlparse, urldefrag

url = 'https://github.com/orgs/google/repositories'
domain = urlparse(url).netloc

# URLリスト作成とか色々作る
url_list = [url]
visited_url_set = set()
repositories = {}

# 上限設定、わかんなかったらこれ→「sqlite3 database.db "PRAGMA table_info(Github_google_repositories.db);" | wc -l」
max_pages = 1000

# 何回も同じページいかないように
while url_list and len(visited_url_set) < max_pages:
    current_url = urldefrag(url_list.pop(0))[0]

    if current_url in visited_url_set:
        continue

    visited_url_set.add(current_url)

    #　☑️1秒まつ
    time.sleep(1)

    try:
        res = requests.get(current_url, timeout=10)
    except requests.RequestException as e:
        print(f"アクセスに失敗しました：{current_url} ({e})")
        continue

    print(f"ステータスコード：{res.status_code}")
    print(f"ステータスメッセージ：{res.reason}")

    if res.status_code != 200:
        continue

    res.encoding = res.apparent_encoding

    try:
        html_soup = BeautifulSoup(res.text, 'html.parser')
    except ParserRejectedMarkup:
        print(f"解析できないため、スキップ：{current_url}")
        continue

    path_parts = urlparse(current_url).path.strip('/').split('/')

    # リポジトリ詳細ページから情報を取得
    if len(path_parts) == 2 and path_parts[0] == 'google':
        repo_name = path_parts[1]

        language_tag = html_soup.select_one(
            '[itemprop="programmingLanguage"]'
        )
        language = language_tag.get_text(strip=True) if language_tag else ''

        stars = ''
        star_link = html_soup.select_one(
            f'a[href="/google/{repo_name}/stargazers"]'
        )
        if star_link:
            stars = star_link.get_text(strip=True)

        repositories[repo_name] = {
            'name': repo_name,
            'language': language,
            'stars': stars
        }

    # リポジトリ詳細ページと一覧の次ページをたどる
    for tag in html_soup.find_all('a', href=True):
        next_url = urljoin(current_url, tag.get('href'))
        next_url = urldefrag(next_url)[0]
        parsed_url = urlparse(next_url)
        next_path_parts = parsed_url.path.strip('/').split('/')

        is_repository = (
            parsed_url.netloc == domain
            and len(next_path_parts) == 2
            and next_path_parts[0] == 'google'
        )

        is_repository_list_page = (
            parsed_url.netloc == domain
            and parsed_url.path == '/orgs/google/repositories'
        )

        if (
            parsed_url.scheme in ('http', 'https')
            and (is_repository or is_repository_list_page)
            and next_url not in visited_url_set
            and next_url not in url_list
        ):
            url_list.append(next_url)

# SQLiteデータベースに保存し、SELECT文で表示
with sqlite3.connect('Github_google_repositories.db') as conn:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS repositories (
            name TEXT PRIMARY KEY,
            language TEXT,
            stars TEXT
        )
    ''')

    for repo in repositories.values():
        conn.execute('''
            INSERT OR REPLACE INTO repositories (name, language, stars)
            VALUES (?, ?, ?)
        ''', (repo['name'], repo['language'], repo['stars']))

    rows = conn.execute('''
        SELECT name, language, stars
        FROM repositories
        ORDER BY name
    ''').fetchall()

    # リポジトリ名，主要な言語，スターの数
    for name, language, stars in rows:
        print(f"リポジトリ名：{name} 主要言語：{language} スターの数：{stars}")