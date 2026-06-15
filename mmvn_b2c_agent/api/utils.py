import requests
import pandas
from io import StringIO

FEED_URLS = [
    "https://online.mmvietnam.com/media/feed/mm-food-service-hung-phu-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-an-phu-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-bien-hoa-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-binh-duong-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-binh-phu-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-buon-ma-thuot-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-ha-long-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-ha-dong-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-hiep-phu-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-hoang-mai-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-hong-bang-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-hung-loi-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-long-xuyen-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-nha-trang-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-quy-nhon-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-rach-gia-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-thang-long-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-vinh-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-vung-tau-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-mega-market-da-nang-vi.csv",
    "https://online.mmvietnam.com/media/feed/mm-supermarket-thanh-xuan-vi.csv",
]


def load_feed(url: str):
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad status codes
        data = response.content.decode('utf-8')
        df = pandas.read_csv(StringIO(data))
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"Error loading feed from {url}: {e}")
        return None


def get_all_categories() -> dict:
    result = {}
    for i, url in enumerate(FEED_URLS):
        df = load_feed(url)
        print(f"Loaded {i + 1}")
        if df is None:
            continue

        store_code = str(df['store_code'].mode()[0])
        result[store_code] = df[['main_category', 'main_category_uid']].dropna().drop_duplicates().rename(
            mapper={'main_category': 'name', 'main_category_uid': 'id'}, axis=1).to_dict(orient='records')

    return result


def get_category_map() -> dict:
    print(f"Loading all categories from feeds...")
    frames = [load_feed(f) for f in FEED_URLS]
    full_df = pandas.concat(frames, ignore_index=True)
    categories_df = full_df[['main_category', 'main_category_uid']].dropna().value_counts()
    category_map = {row['main_category_uid']: row['main_category'] for _, row in categories_df.reset_index().iterrows()}
    return category_map


if __name__ == '__main__':
    import json
    print(json.dumps(get_category_map(), indent=4, ensure_ascii=False))
