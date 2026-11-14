import os
import sqlite3
import time

import requests


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


def main():
    try:
        repositories = fetch_repositories()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f'取得が完了しなかったため、データベースは更新しません: {exc}')
        return

    if not repositories:
        print('リポジトリを取得できなかったため、データベースは更新しません')
        return

    save_repositories(repositories)
    print(f'取得・保存したリポジトリ数: {len(repositories)}')
    print(f'保存先: {DB_PATH}')


if __name__ == '__main__':
    main()
