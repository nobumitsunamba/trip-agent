"""
search_train.py
Yahoo!乗換案内をスクレイピングして新幹線候補を検索するモジュール
"""

import re
import urllib.parse
from datetime import datetime

import requests
from bs4 import BeautifulSoup

YAHOO_TRANSIT_BASE = "https://transit.yahoo.co.jp/search/result"
YAHOO_TRANSIT_TOP = "https://transit.yahoo.co.jp/"

# 東北・北海道・上越・北陸方面の主要駅キーワード（えきねっと対象路線判定用）
EKINET_KEYWORDS = [
    "仙台", "盛岡", "新青森", "青森", "函館", "新函館北斗", "札幌",
    "新潟", "長野", "金沢", "富山", "福井", "敦賀",
    "山形", "秋田", "福島", "郡山", "那須塩原", "宇都宮",
    "大宮", "高崎", "軽井沢",
]

# 乗り換え案内・所要時間で混入しやすい短い「XX分」を誤検知しないための下限（分）
# 新幹線+在来線で最短でも30分程度はかかる
_MIN_DURATION_MINUTES = 20
# 新幹線料金の最低ライン（これ以下は誤検知と判断）
_MIN_FARE_YEN = 500

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def build_yahoo_url(from_station: str, to_station: str, arrival_time: str, trip_date: str) -> str:
    """Yahoo!乗換案内の検索URLを生成する。"""
    dt = datetime.strptime(trip_date, "%Y-%m-%d")
    hh, mm = arrival_time.split(":")
    m1 = mm[0] if len(mm) >= 1 else "0"
    m2 = mm[1] if len(mm) >= 2 else "0"

    params = {
        "from": from_station,
        "to": to_station,
        "y": dt.strftime("%Y"),
        "m": dt.strftime("%m"),
        "d": dt.strftime("%d"),
        "hh": hh,
        "m1": m1,
        "m2": m2,
        "type": "4",   # 4 = 着時刻指定
        "ticket": "ic",
        "expkind": "1",
        "shin": "1",   # 新幹線を含む
        "ex": "1",
        "hb": "1",
        "lb": "1",
        "sr": "1",
    }
    return YAHOO_TRANSIT_BASE + "?" + urllib.parse.urlencode(params, encoding="utf-8")


def determine_booking_site(from_station: str, to_station: str, route_text: str = "") -> dict:
    """
    出発地・目的地・経路テキストからどの予約サイトを使うか判定する。
    """
    combined = f"{from_station} {to_station} {route_text}"
    for kw in EKINET_KEYWORDS:
        if kw in combined:
            return {
                "name": "JR東日本 えきねっと",
                "url": "https://www.eki-net.com/top/index.html",
            }
    return {
        "name": "JR東海 EX予約",
        "url": "https://expy.jp/",
    }


def _duration_to_minutes(text: str) -> int:
    """「X時間Y分」「X時間」「Y分」形式の文字列を分数に変換する。"""
    text = re.sub(r"\s+", "", text)
    m = re.match(r"(\d+)時間(\d+)分", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"(\d+)時間$", text)
    if m:
        return int(m.group(1)) * 60
    m = re.match(r"(\d+)分$", text)
    if m:
        return int(m.group(1))
    return 0


def _minutes_to_str(minutes: int) -> str:
    """分数を「X時間Y分」形式に変換する。"""
    if minutes <= 0:
        return ""
    h, m = divmod(minutes, 60)
    if h > 0 and m > 0:
        return f"{h}時間{m}分"
    if h > 0:
        return f"{h}時間"
    return f"{m}分"


def _parse_single_route(item, from_station: str, to_station: str) -> dict | None:
    """
    単一の経路要素をパースして辞書を返す。

    ポイント:
    - 料金は「XX,XXX円」形式で最大値を採用（小さい数字の誤検知を除外）
    - 所要時間は最長マッチを採用（乗り換え時間など短い値の誤検知を除外）
    - 時刻は全テキストから先頭・末尾を出発・到着とみなす
    """
    raw_text = item.get_text()

    # ---- 出発・到着時刻 ----
    # HH:MM 形式（00:00〜23:59）を全て抽出
    # ※ \b は日本語文字との境界では機能しないため使わない（例: "07:00発" の「発」）
    all_times = re.findall(r"(\d{1,2}:\d{2})", raw_text)
    valid_times = [
        t for t in all_times
        if 0 <= int(t.split(":")[0]) <= 23 and 0 <= int(t.split(":")[1]) <= 59
    ]
    dep_time = valid_times[0] if len(valid_times) >= 1 else ""
    arr_time = valid_times[-1] if len(valid_times) >= 2 else (valid_times[0] if valid_times else "")

    # ---- 所要時間 ----
    # 全マッチを分数に変換し、最大値（= 総所要時間）を採用
    # → 乗り換え時間の「5分」「10分」より総所要時間の「2時間15分」が勝つ
    dur_matches = re.findall(r"\d+時間\d+分|\d+時間|\d+分", raw_text)
    best_minutes = 0
    for dm in dur_matches:
        mins = _duration_to_minutes(dm)
        if mins > best_minutes:
            best_minutes = mins
    duration = _minutes_to_str(best_minutes) if best_minutes >= _MIN_DURATION_MINUTES else ""

    # ---- 料金 ----
    # 「XX,XXX円」形式を全て抽出し最大値を採用
    # → 「2円」のような誤検知は _MIN_FARE_YEN で除外
    fare = ""
    fare_max = 0
    # パターン1: 「14,380円」「1,500円」など
    for m in re.finditer(r"([\d,]+)円", raw_text):
        raw_num = m.group(1).replace(",", "")
        if raw_num.isdigit():
            n = int(raw_num)
            if n >= _MIN_FARE_YEN and n > fare_max:
                fare_max = n
                # 元の表記（カンマ区切り）を使う
                fare = f"¥{m.group(1)}円"
    # パターン2: 「¥14,380」など（"円"なし）
    if not fare:
        for m in re.finditer(r"¥\s*([\d,]+)", raw_text):
            raw_num = m.group(1).replace(",", "")
            if raw_num.isdigit():
                n = int(raw_num)
                if n >= _MIN_FARE_YEN and n > fare_max:
                    fare_max = n
                    fare = f"¥{m.group(1)}円"

    # ---- 路線名 ----
    train_elems = item.select(
        ".transport, .trainName, [class*='transport'], [class*='train'], [class*='line']"
    )
    train_names = []
    seen_names: set[str] = set()
    for el in train_elems:
        txt = el.get_text(strip=True)
        if txt and txt not in seen_names:
            seen_names.add(txt)
            train_names.append(txt)
    route_summary = "、".join(train_names[:4]) if train_names else f"{from_station}→{to_station}"

    # ---- 予約サイト判定 ----
    booking = determine_booking_site(from_station, to_station, raw_text)

    # 時刻・所要時間・料金のいずれも取れなかった場合は無効とみなす
    if not dep_time and not arr_time and not duration and not fare:
        return None

    return {
        "dep_time": dep_time or "—",
        "arr_time": arr_time or "—",
        "duration": duration or "—",
        "fare": fare or "—",
        "route_summary": route_summary,
        "booking_name": booking["name"],
        "booking_url": booking["url"],
    }


def parse_routes(soup: BeautifulSoup, from_station: str, to_station: str) -> list[dict]:
    """BeautifulSoupオブジェクトから経路候補を最大3件パースして返す。"""

    # Yahoo!乗換案内の経路一覧コンテナを探す
    route_items = soup.select("li.routeWrap")
    if not route_items:
        route_items = soup.select("section.routeWrap")
    if not route_items:
        # フォールバック: routeという語を含む div（広めだが最終手段）
        route_items = soup.select("div[class*='route']")

    results = []
    seen_keys: set[tuple] = set()   # 重複除外用: (dep_time, arr_time) のセット

    for item in route_items:
        try:
            route_info = _parse_single_route(item, from_station, to_station)
            if not route_info:
                continue

            # (出発時刻, 到着時刻) が同じ候補は重複として除外
            # 時刻がどちらも取れない場合は (料金, 所要時間) を補助キーとして使う
            dep = route_info["dep_time"]
            arr = route_info["arr_time"]
            if dep == "—" and arr == "—":
                key = (route_info["fare"], route_info["duration"])
            else:
                key = (dep, arr)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            results.append(route_info)
            if len(results) >= 3:
                break
        except Exception:
            continue

    return results


def _make_session() -> requests.Session:
    """
    Cookie付きセッションを作成する。
    Yahoo!乗換案内のトップページを先に取得してセッションCookieを得る。
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        resp = session.get(YAHOO_TRANSIT_TOP, timeout=10)
        resp.raise_for_status()
    except Exception:
        pass
    return session


def search_trains(
    from_station: str,
    to_station: str,
    arrival_time: str,
    trip_date: str,
) -> tuple[list[dict], str]:
    """
    Yahoo!乗換案内をスクレイピングして新幹線候補を最大3件返す。

    Returns:
        (routes, yahoo_url)
        routes: 経路候補のリスト（最大3件）
        yahoo_url: Yahoo!乗換案内の検索結果URL
    """
    yahoo_url = build_yahoo_url(from_station, to_station, arrival_time, trip_date)
    routes = []

    try:
        session = _make_session()
        session.headers["Referer"] = YAHOO_TRANSIT_TOP
        resp = session.get(yahoo_url, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        routes = parse_routes(soup, from_station, to_station)
    except requests.HTTPError as e:
        print(f"[search_train] HTTP エラー ({e.response.status_code}): {e}")
    except requests.RequestException as e:
        print(f"[search_train] 通信エラー: {e}")
    except Exception as e:
        print(f"[search_train] パースエラー: {e}")

    # パース失敗時のフォールバック
    if not routes:
        booking = determine_booking_site(from_station, to_station)
        routes = [
            {
                "dep_time": "—",
                "arr_time": "—",
                "duration": "—",
                "fare": "—",
                "route_summary": (
                    f"{from_station} → {to_station}\n"
                    "（下の「Yahoo!乗換案内で確認」ボタンで時刻・料金をご確認ください）"
                ),
                "booking_name": booking["name"],
                "booking_url": booking["url"],
            }
        ]

    return routes, yahoo_url
