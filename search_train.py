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

# 東北・北海道・上越・北陸方面の主要駅キーワード（えきねっと対象路線判定用）
EKINET_KEYWORDS = [
    "仙台", "盛岡", "新青森", "青森", "函館", "新函館北斗", "札幌",
    "新潟", "長野", "金沢", "富山", "福井", "敦賀",
    "山形", "秋田", "福島", "郡山", "那須塩原", "宇都宮",
    "大宮", "高崎", "軽井沢",
]

YAHOO_TRANSIT_TOP = "https://transit.yahoo.co.jp/"

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

    Returns:
        dict: {"name": サイト名, "url": URL}
    """
    combined = f"{from_station} {to_station} {route_text}"
    for kw in EKINET_KEYWORDS:
        if kw in combined:
            return {
                "name": "JR東日本 えきねっと",
                "url": "https://www.eki-net.com/top/index.html",
            }
    # デフォルトはJR東海EX予約（東海道・山陽新幹線）
    return {
        "name": "JR東海 EX予約",
        "url": "https://expy.jp/",
    }


def parse_duration(text: str) -> str:
    """'1時間30分' など所要時間テキストを正規化する。"""
    text = re.sub(r"\s+", "", text)
    # 「XX時間XX分」パターン
    m = re.search(r"(\d+)時間(\d+)分", text)
    if m:
        return f"{m.group(1)}時間{m.group(2)}分"
    m = re.search(r"(\d+)時間", text)
    if m:
        return f"{m.group(1)}時間"
    m = re.search(r"(\d+)分", text)
    if m:
        return f"{m.group(1)}分"
    return text


def parse_fare(text: str) -> str:
    """料金テキストから数字部分を抽出して整形する。"""
    text = re.sub(r"\s+", "", text)
    m = re.search(r"[\d,，]+", text)
    if m:
        digits = re.sub(r"[，,]", ",", m.group())
        return f"¥{digits}円"
    return text


def parse_routes(soup: BeautifulSoup, from_station: str, to_station: str) -> list[dict]:
    """BeautifulSoupオブジェクトから経路候補を最大3件パースして返す。"""
    results = []

    # Yahoo!乗換案内の経路一覧コンテナを探す
    route_items = soup.select("li.routeWrap")
    if not route_items:
        route_items = soup.select("section.routeWrap")
    if not route_items:
        # フォールバック: routeという語を含むdivを探す
        route_items = soup.select("div[class*='route']")

    for item in route_items[:3]:
        try:
            route_info = _parse_single_route(item, from_station, to_station)
            if route_info:
                results.append(route_info)
        except Exception:
            continue

    return results


def _parse_single_route(item, from_station: str, to_station: str) -> dict | None:
    """単一の経路要素をパースして辞書を返す。"""

    # ---- 出発・到着時刻 ----
    time_elems = item.select(".time, .timeWrap, [class*='time']")
    dep_time = arr_time = ""
    if time_elems:
        times_text = [t.get_text(strip=True) for t in time_elems if re.search(r"\d{1,2}:\d{2}", t.get_text())]
        time_matches = []
        for t in times_text:
            time_matches += re.findall(r"\d{1,2}:\d{2}", t)
        if len(time_matches) >= 2:
            dep_time = time_matches[0]
            arr_time = time_matches[-1]
        elif len(time_matches) == 1:
            arr_time = time_matches[0]

    # 時刻が取れなかった場合、テキスト全体から抽出
    if not dep_time or not arr_time:
        all_times = re.findall(r"\d{1,2}:\d{2}", item.get_text())
        if len(all_times) >= 2:
            dep_time = all_times[0]
            arr_time = all_times[-1]

    # ---- 所要時間 ----
    duration = ""
    dur_elem = item.select_one(".time, .totalTime, [class*='time']")
    raw_text = item.get_text()
    dur_m = re.search(r"(\d+時間\d+分|\d+時間|\d+分)", raw_text)
    if dur_m:
        duration = parse_duration(dur_m.group(1))

    # ---- 料金 ----
    fare = ""
    fare_elem = item.select_one(".fare, .price, [class*='fare'], [class*='price']")
    if fare_elem:
        fare = parse_fare(fare_elem.get_text())
    if not fare:
        fare_m = re.search(r"([\d,]+)\s*円", raw_text)
        if fare_m:
            fare = f"¥{fare_m.group(1)}円"

    # ---- 路線名（新幹線名など） ----
    train_names = []
    train_elems = item.select(".transport, .trainName, [class*='transport'], [class*='train']")
    for el in train_elems:
        txt = el.get_text(strip=True)
        if txt and "新幹線" in txt or "のぞみ" in txt or "ひかり" in txt or "はやぶさ" in txt:
            train_names.append(txt)
        elif txt:
            train_names.append(txt)

    route_summary = "、".join(train_names[:3]) if train_names else f"{from_station}→{to_station}"
    route_text = item.get_text()

    # ---- 予約サイト判定 ----
    booking = determine_booking_site(from_station, to_station, route_text)

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


def _make_session() -> requests.Session:
    """
    Cookie付きセッションを作成する。
    Yahoo!乗換案内のトップページを先に取得してセッションCookieを得る。
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        # トップページにアクセスしてCookieを取得
        resp = session.get(YAHOO_TRANSIT_TOP, timeout=10)
        resp.raise_for_status()
    except Exception:
        pass  # Cookie取得失敗でも続行
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
        # Refererヘッダーを追加してからリクエスト
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

    # パース失敗時のフォールバック: Yahoo!乗換案内URLは有効なので案内を表示
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
