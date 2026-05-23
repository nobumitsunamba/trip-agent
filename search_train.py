"""
search_train.py
Yahoo!乗換案内をスクレイピングして新幹線候補を検索するモジュール

返却する route dict の構造:
    {
      "dep_time":     "07:00",
      "arr_time":     "08:45",
      "duration":     "1時間45分",
      "fare":         "¥14,380円",
      "route_summary":"のぞみ123号",
      "segments": [
        {"type": "station", "name": "東京",    "time": "07:00"},
        {"type": "train",   "name": "のぞみ123号"},
        {"type": "station", "name": "名古屋",   "time": "08:45"},
      ],
      "booking_name": "JR東海 EX予約",
      "booking_url":  "https://expy.jp/",
    }
"""

import re
import urllib.parse
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag

YAHOO_TRANSIT_BASE = "https://transit.yahoo.co.jp/search/result"
YAHOO_TRANSIT_TOP  = "https://transit.yahoo.co.jp/"

# 東北・北海道・上越・北陸方面（えきねっと対象路線判定）
EKINET_KEYWORDS = [
    "仙台", "盛岡", "新青森", "青森", "函館", "新函館北斗", "札幌",
    "新潟", "長野", "金沢", "富山", "福井", "敦賀",
    "山形", "秋田", "福島", "郡山", "那須塩原", "宇都宮",
    "大宮", "高崎", "軽井沢",
]

# 乗り換え時間など短い値の誤検知を除外する最低所要時間（分）
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


# ============================================================
# URL / 判定ヘルパー
# ============================================================

def build_yahoo_url(from_station: str, to_station: str, arrival_time: str, trip_date: str) -> str:
    """Yahoo!乗換案内の検索URLを生成する（着時刻指定）。"""
    dt = datetime.strptime(trip_date, "%Y-%m-%d")
    hh, mm = arrival_time.split(":")
    params = {
        "from": from_station,
        "to": to_station,
        "y": dt.strftime("%Y"),
        "m": dt.strftime("%m"),
        "d": dt.strftime("%d"),
        "hh": hh,
        "m1": mm[0],
        "m2": mm[1] if len(mm) >= 2 else "0",
        "type": "4",    # 4 = 着時刻指定
        "ticket": "ic",
        "expkind": "1",
        "shin": "1",    # 新幹線含む
        "ex": "1",
        "hb": "1",
        "lb": "1",
        "sr": "1",
    }
    return YAHOO_TRANSIT_BASE + "?" + urllib.parse.urlencode(params, encoding="utf-8")


def determine_booking_site(from_station: str, to_station: str, route_text: str = "") -> dict:
    """出発地・目的地・経路テキストからどの予約サイトを使うか判定する。"""
    combined = f"{from_station} {to_station} {route_text}"
    for kw in EKINET_KEYWORDS:
        if kw in combined:
            return {"name": "JR東日本 えきねっと", "url": "https://www.eki-net.com/top/index.html"}
    return {"name": "JR東海 EX予約", "url": "https://expy.jp/"}


# ============================================================
# 所要時間ユーティリティ
# ============================================================

def _duration_to_minutes(text: str) -> int:
    """「X時間Y分」「X時間」「Y分」→ 分数に変換。"""
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
    """分数 → 「X時間Y分」形式に変換。"""
    if minutes <= 0:
        return ""
    h, m = divmod(minutes, 60)
    if h > 0 and m > 0:
        return f"{h}時間{m}分"
    if h > 0:
        return f"{h}時間"
    return f"{m}分"


# ============================================================
# セグメント抽出（乗り換え・各行程の詳細）
# ============================================================

def _clean_station_name(text: str) -> str:
    """駅名テキストから余分な文字を除去する。"""
    text = re.sub(r"[　\s]+", "", text)        # 全角・半角スペース除去
    text = re.sub(r"[発着乗り換え]$", "", text) # 語尾の「発」「着」「乗り換え」
    text = re.sub(r"\(.*?\)", "", text)        # ()内の注記
    text = re.sub(r"（.*?）", "", text)        # （）内の注記
    return text.strip()


def _is_valid_time(t: str) -> bool:
    """HH:MM 形式で 00:00〜23:59 の範囲か確認。"""
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)
    if not m:
        return False
    return 0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59


def _extract_first_time(text: str) -> str:
    """テキストから最初の HH:MM を抽出。"""
    m = re.search(r"(\d{1,2}:\d{2})", text)
    return m.group(1) if m and _is_valid_time(m.group(1)) else ""


def _extract_segments_from_structure(item: Tag) -> list[dict]:
    """
    CSS クラスベースのパースでセグメントを抽出する。

    Yahoo!乗換案内の典型的な HTML 構造:
      <ul class="route"> または <ul class="transportResult">
        <li class="station">  ← 駅
          <p class="time">07:00</p>
          <p class="stationName">東京</p>
        </li>
        <li class="transport"> ← 交通手段
          <p class="trainName">のぞみ123号</p>
          <p class="trainType">東海道新幹線</p>
        </li>
        ...
      </ul>
    """
    segments: list[dict] = []

    # 経路詳細 ul を探す（複数のクラス名バリアントに対応）
    route_ul = (
        item.select_one("ul.route")
        or item.select_one("ul.transportResult")
        or item.select_one(".routeDetail ul")
        or item.select_one("[class*='route'] > ul")
        or item.select_one("[class*='transport'] > ul")
    )
    if not route_ul:
        return segments

    for li in route_ul.find_all("li", recursive=False):
        cls = " ".join(li.get("class", []))

        if any(x in cls for x in ("station", "stop", "point")):
            # ---- 駅 ----
            time_el = (
                li.select_one("em.time")
                or li.select_one(".time")
                or li.select_one("[class*='time']")
            )
            name_el = (
                li.select_one(".stationName")
                or li.select_one("[class*='station']")
                or li.select_one(".name")
                or li.select_one("p:not([class*='time'])")
            )
            t = _extract_first_time(time_el.get_text() if time_el else "")
            if not t:
                t = _extract_first_time(li.get_text())
            n = _clean_station_name(name_el.get_text() if name_el else "")
            if n and not re.match(r"^\d+$", n):
                segments.append({"type": "station", "name": n, "time": t})

        elif any(x in cls for x in ("transport", "train", "line", "section")):
            # ---- 交通手段 ----
            name_el = (
                li.select_one(".trainName")
                or li.select_one("[class*='train']")
                or li.select_one(".lineName")
                or li.select_one("[class*='line']")
                or li.select_one("a")
                or li.select_one(".name")
            )
            type_el = li.select_one(".trainType, .lineType, [class*='type']")

            train_name = (name_el.get_text(strip=True) if name_el else "").strip()
            train_type = (type_el.get_text(strip=True) if type_el else "").strip()

            # 「東海道新幹線 のぞみ123号」のように結合
            if train_type and train_name and train_type not in train_name:
                display = f"{train_type} {train_name}"
            elif train_name:
                display = train_name
            else:
                display = li.get_text(strip=True)

            display = display[:50]  # 長すぎる場合は切り詰め
            if display:
                segments.append({"type": "train", "name": display})

    return segments


def _extract_segments_from_text(item: Tag) -> list[dict]:
    """
    テキストパターンベースのフォールバックパーサー。

    CSS 構造がパースできない場合に、テキスト行を走査して
    「HH:MM 駅名」形式と「〇〇線・〇〇号」形式を抽出する。
    """
    segments: list[dict] = []
    raw = item.get_text("\n")
    lines = [l.strip() for l in raw.split("\n") if l.strip()]

    seen_station_times: set[str] = set()

    for line in lines:
        # 「07:00 東京」「東京 07:00」形式
        m = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", line)
        if not m:
            m = re.match(r"^(.+?)\s+(\d{1,2}:\d{2})$", line)
            if m:
                m = type("M", (), {"group": lambda self, i: [None, m.group(2), m.group(1)][i]})()
        if m:
            t = m.group(1)
            n = _clean_station_name(m.group(2))
            if _is_valid_time(t) and n and t not in seen_station_times:
                seen_station_times.add(t)
                segments.append({"type": "station", "name": n, "time": t})
            continue

        # 「のぞみ123号」「東海道新幹線」「JR〇〇線」「〇〇新快速」など
        if (
            re.search(r"\d+号", line)
            or "新幹線" in line
            or re.search(r"[A-Z]{2}R?\s*\w+線", line)
            or re.search(r"[\w぀-鿿]+線", line)
            or "急行" in line
            or "快速" in line
            or "特急" in line
        ):
            clean = line[:50].strip()
            if clean and not re.match(r"^\d+$", clean):
                segments.append({"type": "train", "name": clean})

    return segments


def _extract_segments(item: Tag) -> list[dict]:
    """
    経路要素から各区間（駅・交通手段）を抽出する。

    Returns:
        [
          {"type": "station", "name": "東京",       "time": "07:00"},
          {"type": "train",   "name": "のぞみ123号"},
          {"type": "station", "name": "名古屋",      "time": "08:45"},
          ...
        ]
    """
    # アプローチ1: CSS クラス構造ベース
    segments = _extract_segments_from_structure(item)

    # アプローチ2: テキストパターンベース（構造パースが不十分な場合）
    if len(segments) < 2:
        segments = _extract_segments_from_text(item)

    # 重複除去: 連続する同じ要素は1つにまとめる
    deduped: list[dict] = []
    for seg in segments:
        if deduped and deduped[-1] == seg:
            continue
        deduped.append(seg)

    return deduped


def _build_route_summary(segments: list[dict]) -> str:
    """セグメントリストから路線サマリー文字列を生成する。例: のぞみ123号 / JR神戸線→のぞみ316号"""
    trains = [s["name"] for s in segments if s["type"] == "train"]
    return "→".join(trains[:4]) if trains else ""


# ============================================================
# 単一経路パーサー
# ============================================================

def _parse_single_route(item: Tag, from_station: str, to_station: str) -> dict | None:
    """
    単一の経路要素をパースして辞書を返す。

    ポイント:
    - 料金: 全「XX,XXX円」パターンの最大値を採用（小さい数字の誤検知を除外）
    - 所要時間: 全マッチの最大値を採用（乗り換え時間など短い値の誤検知を除外）
    - 時刻: \b を使わない（日本語文字との境界で機能しないため）
    """
    raw_text = item.get_text()

    # ---- 出発・到着時刻 ----
    all_times = [
        t for t in re.findall(r"(\d{1,2}:\d{2})", raw_text)
        if _is_valid_time(t)
    ]
    dep_time = all_times[0] if len(all_times) >= 1 else ""
    arr_time = all_times[-1] if len(all_times) >= 2 else (all_times[0] if all_times else "")

    # ---- 所要時間（最大値を採用）----
    best_minutes = max(
        (_duration_to_minutes(dm) for dm in re.findall(r"\d+時間\d+分|\d+時間|\d+分", raw_text)),
        default=0,
    )
    duration = _minutes_to_str(best_minutes) if best_minutes >= _MIN_DURATION_MINUTES else ""

    # ---- 料金（最大値を採用）----
    fare = ""
    fare_max = 0
    for m in re.finditer(r"([\d,]+)円", raw_text):
        raw_num = m.group(1).replace(",", "")
        if raw_num.isdigit():
            n = int(raw_num)
            if n >= _MIN_FARE_YEN and n > fare_max:
                fare_max = n
                fare = f"¥{m.group(1)}円"
    if not fare:
        for m in re.finditer(r"¥\s*([\d,]+)", raw_text):
            raw_num = m.group(1).replace(",", "")
            if raw_num.isdigit():
                n = int(raw_num)
                if n >= _MIN_FARE_YEN and n > fare_max:
                    fare_max = n
                    fare = f"¥{m.group(1)}円"

    # ---- セグメント（経路詳細）----
    segments = _extract_segments(item)

    # ---- 路線サマリー ----
    route_summary = _build_route_summary(segments) or f"{from_station}→{to_station}"

    # ---- 予約サイト判定 ----
    booking = determine_booking_site(from_station, to_station, raw_text)

    # 有効な情報が何もなければスキップ
    if not dep_time and not arr_time and not duration and not fare:
        return None

    # セグメントに時刻がない場合は dep_time / arr_time を補完する
    if segments:
        stations = [s for s in segments if s["type"] == "station"]
        if stations and not stations[0].get("time") and dep_time:
            stations[0]["time"] = dep_time
        if len(stations) >= 2 and not stations[-1].get("time") and arr_time:
            stations[-1]["time"] = arr_time

    return {
        "dep_time":      dep_time or "—",
        "arr_time":      arr_time or "—",
        "duration":      duration or "—",
        "fare":          fare or "—",
        "route_summary": route_summary,
        "segments":      segments,
        "booking_name":  booking["name"],
        "booking_url":   booking["url"],
    }


# ============================================================
# 経路一覧パーサー
# ============================================================

def parse_routes(soup: BeautifulSoup, from_station: str, to_station: str) -> list[dict]:
    """BeautifulSoupオブジェクトから経路候補を最大3件パースして返す。"""

    route_items = (
        soup.select("li.routeWrap")
        or soup.select("section.routeWrap")
        or soup.select("div[class*='route']")
    )

    results: list[dict] = []
    seen_keys: set[tuple] = set()

    for item in route_items:
        try:
            route_info = _parse_single_route(item, from_station, to_station)
            if not route_info:
                continue

            dep = route_info["dep_time"]
            arr = route_info["arr_time"]
            key = (dep, arr) if not (dep == "—" and arr == "—") else (route_info["fare"], route_info["duration"])
            if key in seen_keys:
                continue
            seen_keys.add(key)

            results.append(route_info)
            if len(results) >= 3:
                break
        except Exception:
            continue

    return results


# ============================================================
# セッション / 検索エントリポイント
# ============================================================

def _make_session() -> requests.Session:
    """Cookie付きセッションを作成する（トップページでCookieを取得）。"""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(YAHOO_TRANSIT_TOP, timeout=10)
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
    """
    yahoo_url = build_yahoo_url(from_station, to_station, arrival_time, trip_date)
    routes: list[dict] = []

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

    if not routes:
        booking = determine_booking_site(from_station, to_station)
        routes = [{
            "dep_time":      "—",
            "arr_time":      "—",
            "duration":      "—",
            "fare":          "—",
            "route_summary": (
                f"{from_station} → {to_station}\n"
                "（下の「Yahoo!乗換案内で確認」ボタンで時刻・料金をご確認ください）"
            ),
            "segments":      [],
            "booking_name":  booking["name"],
            "booking_url":   booking["url"],
        }]

    return routes, yahoo_url
