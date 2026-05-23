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

import os
import re
import urllib.parse
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag

# TRANSIT_DEBUG=1 を設定するとセグメント抽出のデバッグ情報を stdout に出力する
_TRANSIT_DEBUG = os.getenv("TRANSIT_DEBUG", "").lower() in ("1", "true", "yes")

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
    text = re.sub(r"[　\s]+", " ", text).strip()
    # 先頭の発/着/ー記号を除去（Yahoo Transit UI要素が混入している場合）
    text = re.sub(r"^[発着ーー\-\s]+", "", text)
    text = re.sub(r"[発着]$", "", text)          # 語尾「発」「着」
    text = re.sub(r"乗り換え.*$", "", text)      # 「乗り換えX分」以降
    text = re.sub(r"\([^)]*\)", "", text)         # (…) 注記（半角括弧）
    text = re.sub(r"（[^）]*）", "", text)         # （…） 注記（全角括弧・閉じあり）
    text = re.sub(r"（[^）]*$", "", text)          # （…   注記（閉じ括弧なし）
    text = re.sub(r"[\d,]+円.*$", "", text)      # 料金が混入した場合
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


def _is_train_name_line(text: str) -> bool:
    """列車名・路線名らしい行かどうかを判定する。"""
    text = text.strip()
    if not text or len(text) > 80:
        return False
    return bool(
        re.search(r"\d+号", text)                              # のぞみ123号, はやぶさ5号
        or "新幹線" in text                                    # 東海道新幹線
        or re.search(r"JR[\s\w]*線", text)                    # JR神戸線
        or re.search(r"[ぁ-ん一-鿿]{2,}線", text)     # 山手線, 京浜東北線
        or re.search(r"特急|急行|快速|各停|普通|準急", text)    # 種別
        or re.search(r"新快速|特別快速|通勤快速", text)         # 種別詳細
    )


def _is_skip_line(text: str) -> bool:
    """セグメント抽出でスキップすべき行かどうか。"""
    text = text.strip()
    return bool(
        not text
        or re.match(r"^[↓→⇒▼▶＞\-ー|｜…]+$", text)     # 矢印・区切り記号のみ
        or re.match(r"^[\d,]+円$", text)                   # 料金のみ
        or re.match(r"^\d+分$", text)                      # 分数のみ（乗り換え時間）
        or re.match(r"^\d+時間(\d+分)?$", text)            # 所要時間のみ
        or text in {"発", "着", "乗換", "乗り換え"}         # 単独記号
        # ---- Yahoo!乗換案内 特有の UI 要素 ----
        # 「発ー」「着ー」「発 」など：発/着 の後ろが記号・空白のみ
        or re.match(r"^[発着][ーー\-\s]*$", text)
        # 「着（乗車1時間28分）」など：発/着 の直後に全角/半角括弧
        or re.match(r"^[発着][（(]", text)
        # 「（乗車XX分）」など：括弧始まりのアノテーション
        or text.startswith("（")
        or text.startswith("(")
        # 「3番線」などのホーム番号
        or re.match(r"^\d+番線", text)
        # 「乗換X分」など
        or re.match(r"^乗換\d", text)
    )


# ---- CSS 構造ベースのパーサー ----

def _extract_segments_from_structure(item: Tag) -> list[dict]:
    """
    CSS クラスベースのパースでセグメントを抽出する。
    ul ベース（li.station / li.transport）と div ベース（div.station / div.transport）
    の両方に対応する。
    """
    segments: list[dict] = []

    # --- 方式 A: ul/ol 内の li 要素を探す ---
    route_ul = (
        item.select_one("ul.route")
        or item.select_one("ul.transportResult")
        or item.select_one("ul.step")          # Yahoo Transit: ul.step
        or item.select_one("ol.step")
        or item.select_one(".routeDetail ul")
        or item.select_one(".routeDetail ol")
        or item.select_one("[class*='route'] > ul")
        or item.select_one("[class*='transport'] > ul")
    )

    candidates = []
    if route_ul:
        candidates = route_ul.find_all("li", recursive=False)

    # --- 方式 B: li が見つからない場合、div コンテナから探す ---
    if not candidates:
        route_div = (
            item.select_one(".routeDetail")
            or item.select_one(".step")
            or item.select_one("[class*='detail']")
            or item.select_one("[class*='step']")
        )
        if route_div:
            candidates = route_div.find_all(
                ["div", "section", "li"],
                recursive=False,
            )

    for el in candidates:
        cls = " ".join(el.get("class", []))

        if any(x in cls for x in (
            "station", "stop", "point", "dep", "arr",
            "departure", "arrival",          # Yahoo Transit 新形式
        )):
            # ---- 駅 ----
            time_el = (
                el.select_one("em")
                or el.select_one(".time")
                or el.select_one("[class*='time']")
            )
            name_el = (
                el.select_one(".stationName")
                or el.select_one("[class*='stationName']")
                or el.select_one(".title")                # Yahoo Transit: .title に駅名
                or el.select_one("[class*='station']:not([class*='time'])")
                or el.select_one(".name")
                or el.select_one("a")                     # 駅名はリンクになっていることが多い
            )
            t = _extract_first_time(time_el.get_text() if time_el else "")
            if not t:
                t = _extract_first_time(el.get_text())
            n = _clean_station_name(name_el.get_text() if name_el else "")
            # name_el が取れなかったら全テキストから推定
            if not n:
                raw = el.get_text(" ", strip=True)
                n = _clean_station_name(re.sub(r"\d{1,2}:\d{2}", "", raw))
            if n and len(n) >= 1 and not re.match(r"^\d+$", n) and not _is_skip_line(n):
                segments.append({"type": "station", "name": n, "time": t})

        elif any(x in cls for x in ("transport", "train", "line", "section", "transit")):
            # ---- 交通手段 ----
            name_el = (
                el.select_one(".trainName")
                or el.select_one("[class*='trainName']")
                or el.select_one(".lineName")
                or el.select_one(".trainLine")            # Yahoo Transit: .trainLine
                or el.select_one("[class*='line']")
                or el.select_one("p")                     # 列車名は <p> に入ることが多い
                or el.select_one("a")
                or el.select_one(".name")
            )
            type_el = el.select_one(".trainType, .lineType, [class*='type']")
            train_name = (name_el.get_text(strip=True) if name_el else "").strip()
            train_type = (type_el.get_text(strip=True) if type_el else "").strip()
            if train_type and train_name and train_type not in train_name:
                display = f"{train_type} {train_name}"
            elif train_name:
                display = train_name
            else:
                display = el.get_text(" ", strip=True)[:50]
            if display and not _is_skip_line(display):
                segments.append({"type": "train", "name": display})

    return segments


# ---- テキストベースのパーサー（フォールバック）----

def _starts_with_time(text: str) -> str | None:
    """text が HH:MM で始まる場合その時刻を返す。それ以外は None。"""
    m = re.match(r"^(\d{1,2}:\d{2})", text)
    return m.group(1) if m and _is_valid_time(m.group(1)) else None


def _lookahead_station(lines: list[str], i: int) -> tuple[str, int]:
    """
    lines[i] の次から最大4行を見て駅名候補を探す。
    戻り値: (駅名, consumed行数)  駅名が見つからない場合は ("", 0)
    """
    name = ""
    consumed = 0
    for offset in range(1, 5):
        if i + offset >= len(lines):
            break
        nxt = lines[i + offset]
        if _is_skip_line(nxt):
            consumed = offset
            continue
        # 次の時刻行（"09:58" や "09:58着ー" など）が来たら終了
        if _starts_with_time(nxt):
            break
        if _is_train_name_line(nxt):
            break
        candidate = _clean_station_name(nxt)
        if (candidate
                and len(candidate) >= 1
                and not re.match(r"^\d+$", candidate)
                and not _is_skip_line(candidate)):
            name = candidate
            consumed = offset
            break
    return name, consumed


def _lines_to_segments(lines: list[str]) -> list[dict]:
    """
    テキスト行リストからセグメントを抽出する内部ロジック。

    Yahoo!乗換案内のHTMLでは時刻・発着記号・駅名が様々な形で現れるため、
    以下のパターンをすべて処理する:

      (0) 時刻+発着記号 行（Yahoo Transit 実際の形式）
          例: "06:30発ー"  "06:30発東京"  "09:58着（乗車1時間28分）"
          → 発着記号以降に駅名があれば使用、なければ次行を lookahead
      (1) 時刻のみ行
          例: "07:00"  → 次の非スキップ行を駅名として取得
      (2) 時刻+スペース+駅名 同一行
          例: "07:00 東京" / "東京 07:00"
      (3) 列車名行
          例: "東海道新幹線 のぞみ123号"
    """
    if _TRANSIT_DEBUG:
        print(f"[TRANSIT_DEBUG] _lines_to_segments: {len(lines)} lines")
        for j, ln in enumerate(lines):
            print(f"  {j:3d}: {ln!r}")

    segments: list[dict] = []
    seen_times: set[str] = set()

    i = 0
    while i < len(lines):
        line = lines[i]

        # ===== (0) 時刻+発着記号 "06:30発" "06:30発ー" "06:30発東京" "09:58着（乗車...）" =====
        # Yahoo Transit は時刻と発着記号を同一テキストノードに入れることがある
        m0 = re.match(r"^(\d{1,2}:\d{2})[発着](.*)$", line)
        if m0 and _is_valid_time(m0.group(1)):
            t = m0.group(1)
            if t not in seen_times:
                seen_times.add(t)
                # 発着記号の後ろに駅名が続く場合: "06:30発東京" → rest="東京"
                rest = _clean_station_name(m0.group(2))  # "ー", "（乗車...）", "東京", "" など
                if rest and not _is_skip_line(rest) and not re.match(r"^\d+$", rest):
                    segments.append({"type": "station", "name": rest, "time": t})
                    i += 1
                else:
                    # 後ろが空 or スキップ対象 → 次行から駅名を lookahead
                    name, consumed = _lookahead_station(lines, i)
                    if name:
                        segments.append({"type": "station", "name": name, "time": t})
                        i += consumed + 1
                    else:
                        i += 1
            else:
                i += 1
            continue

        # ===== (1) 時刻のみの行 "07:00" =====
        if re.match(r"^\d{1,2}:\d{2}$", line) and _is_valid_time(line):
            t = line
            if t not in seen_times:
                seen_times.add(t)
                name, consumed = _lookahead_station(lines, i)
                if name:
                    segments.append({"type": "station", "name": name, "time": t})
                    i += consumed + 1
                else:
                    i += 1
            else:
                i += 1
            continue

        # ===== (2a) 時刻+駅名 同一行 "07:00 東京" =====
        m = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", line)
        if m and _is_valid_time(m.group(1)):
            t, raw_name = m.group(1), m.group(2)
            if t not in seen_times:
                seen_times.add(t)
                n = _clean_station_name(raw_name)
                if n and not _is_skip_line(n):
                    segments.append({"type": "station", "name": n, "time": t})
                elif not n or _is_skip_line(n):
                    # 残りがスキップ対象なら次行を lookahead
                    name, consumed = _lookahead_station(lines, i)
                    if name:
                        segments.append({"type": "station", "name": name, "time": t})
                        i += consumed + 1
                        continue
            i += 1
            continue

        # ===== (2b) 駅名+時刻 同一行 "東京 07:00" =====
        m = re.match(r"^(.+?)\s+(\d{1,2}:\d{2})$", line)
        if m and _is_valid_time(m.group(2)):
            t, raw_name = m.group(2), m.group(1)
            if t not in seen_times:
                seen_times.add(t)
                n = _clean_station_name(raw_name)
                if n and not _is_skip_line(n):
                    segments.append({"type": "station", "name": n, "time": t})
            i += 1
            continue

        # ===== (3) 列車名・路線名 =====
        if _is_train_name_line(line) and not _is_skip_line(line):
            # 直前が既に列車名なら連結する（「東海道新幹線\nのぞみ123号」対応）
            if segments and segments[-1]["type"] == "train":
                prev = segments[-1]["name"]
                if line not in prev:
                    segments[-1]["name"] = f"{prev} {line}"[:60]
            else:
                segments.append({"type": "train", "name": line[:60]})

        i += 1

    return segments


def _extract_segments_from_text(item: Tag) -> list[dict]:
    """Tag オブジェクトからテキストを取り出し _lines_to_segments に渡す。"""
    raw = item.get_text("\n")
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    return _lines_to_segments(lines)


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

    # アプローチ2: テキストパターンベース（フォールバック）
    # 条件: セグメント2件未満、または列車情報がない（駅だけでは不完全）
    has_train = any(s.get("type") == "train" for s in segments)
    if len(segments) < 2 or not has_train:
        segments = _extract_segments_from_text(item)

    # 重複除去: 連続する同じ要素は1つにまとめる
    deduped: list[dict] = []
    for seg in segments:
        if deduped and deduped[-1] == seg:
            continue
        deduped.append(seg)

    return deduped


def _extract_text_trains(lines: list[str]) -> list[str]:
    """
    テキスト行から列車名・路線名を順番に抽出する（HTML構造非依存）。

    連続する列車名行は結合する（「東海道新幹線」+「のぞみ316号」→「東海道新幹線 のぞみ316号」）。
    """
    trains: list[str] = []
    prev_was_train = False

    for line in lines:
        if not _is_train_name_line(line) or _is_skip_line(line):
            prev_was_train = False
            continue
        # 乗車時間注記（「1時間28分」など）を除去
        clean = re.sub(r"[（(]\s*\d+時間\d*分?\s*[）)]", "", line)
        clean = re.sub(r"[（(]\s*\d+分\s*[）)]", "", clean).strip()[:60]
        if not clean:
            prev_was_train = False
            continue
        # 直前も列車名なら連結（「東海道新幹線\nのぞみ316号」対応）
        if prev_was_train and trains and clean not in trains[-1]:
            trains[-1] = f"{trains[-1]} {clean}"[:60]
        else:
            trains.append(clean)
        prev_was_train = True

    return trains


def _extract_transfer_count(raw_text: str) -> int:
    """乗換回数をテキストから抽出する。見つからない場合は -1 を返す。"""
    m = re.search(r"乗換[：:]\s*(\d+)\s*回", raw_text)
    if m:
        return int(m.group(1))
    # 「乗換0回」「乗換なし」など
    if re.search(r"乗換なし|乗換：?0", raw_text):
        return 0
    return -1


def _build_route_summary(segments: list[dict]) -> str:
    """セグメントリストから路線サマリー文字列を生成する。例: のぞみ123号 / JR神戸線→のぞみ316号"""
    trains = [s["name"] for s in segments if s["type"] == "train"]
    return "→".join(trains[:4]) if trains else ""


# ============================================================
# 単一経路パーサー
# ============================================================

def _parse_single_route(
    item: Tag,
    from_station: str,
    to_station: str,
    step_el: "Tag | None" = None,
) -> "dict | None":
    """
    単一の経路要素をパースして辞書を返す。

    Args:
        item:         経路サマリー要素（li.routeWrap など）
        from_station: 出発駅名
        to_station:   到着駅名
        step_el:      ステップ詳細要素（Yahoo Transit では routeWrap の外にある場合）
                      None の場合は item のみを使用する

    ポイント:
    - 料金: 全「XX,XXX円」パターンの最大値を採用（小さい数字の誤検知を除外）
    - 所要時間: 全マッチの最大値を採用（乗り換え時間など短い値の誤検知を除外）
    - 時刻: \b を使わない（日本語文字との境界で機能しないため）
    - segments: step_el → item 構造パース → テキストパース → フォールバック
    """
    raw_text = item.get_text()
    lines = [l.strip() for l in item.get_text("\n").split("\n") if l.strip()]
    # step_el があればそのテキスト行も追加（列車名・駅名の取得用）
    if step_el:
        lines = lines + [l.strip() for l in step_el.get_text("\n").split("\n") if l.strip()]

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

    # ---- 乗換回数を抽出 ----
    transfer_count = _extract_transfer_count(raw_text)

    # ---- 列車名をテキストから直接抽出（HTML構造非依存・フォールバック用）----
    text_trains = _extract_text_trains(lines)

    # ---- セグメント（経路詳細）: step_el → item 構造パース → テキストパース ----
    # 1. step_el があれば優先的に試みる（Yahoo Transit では routeWrap 外にステップがある）
    segments: list[dict] = []
    if step_el:
        segments = _extract_segments(step_el)
    # 2. step_el から取得できなければ item 自身を解析
    if not (any(s["type"] == "train" for s in segments) and
            sum(1 for s in segments if s["type"] == "station") >= 2):
        segments = _extract_segments(item)

    seg_has_train   = any(s["type"] == "train"   for s in segments)
    seg_has_station = sum(1 for s in segments if s["type"] == "station") >= 2

    # ---- フォールバック段落構築 ----
    # HTML/テキストパースが不完全な場合: dep/arr + 利用可能情報で段落を生成
    if not seg_has_train or not seg_has_station:
        fb: list[dict] = []
        fb.append({"type": "station", "name": from_station, "time": dep_time or ""})

        if text_trains:
            # テキストから列車名が取得できた場合
            for train_name in text_trains[:4]:
                fb.append({"type": "train", "name": train_name})
        else:
            # 列車名未取得：乗換回数で代替表示（1エントリにまとめる）
            if transfer_count == 0:
                label = "直通（列車名は下のボタンで確認）"
            elif transfer_count > 0:
                label = f"乗換{transfer_count}回（詳細は下のボタンで確認）"
            else:
                label = "（詳細は下のボタンで確認）"
            fb.append({"type": "train", "name": label})

        fb.append({"type": "station", "name": to_station, "time": arr_time or ""})
        segments = fb
        seg_has_train = True

    # ---- 路線サマリー ----
    # segments から列車名 → なければ text_trains → なければ「出発→到着」
    route_summary = _build_route_summary(segments)
    if not route_summary and text_trains:
        route_summary = "→".join(text_trains[:4])
    if not route_summary:
        route_summary = f"{from_station}→{to_station}"

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

    # ---- ページ全体からステップ詳細コンテナを探す ----
    # Yahoo Transit では routeWrap の外（兄弟要素や別セクション）にある場合がある
    page_step_lists: list[Tag] = (
        soup.select("ul.step")
        or soup.select("ol.step")
        or soup.select("[class*='routeDetail'] ul")
        or soup.select("[class*='routeResult'] ul")
        or []
    )

    results: list[dict] = []
    seen_keys: set[tuple] = set()

    for idx, item in enumerate(route_items):
        # このルートに対応するステップ要素を決定
        step_el: "Tag | None" = None

        # 方法1: ページ全体のステップリストからインデックスで対応付け
        if idx < len(page_step_lists):
            step_el = page_step_lists[idx]

        # 方法2: 次の兄弟要素（routeWrap でないもの）にステップがある場合
        if not step_el:
            nxt = item.find_next_sibling()
            if nxt and "routeWrap" not in " ".join(nxt.get("class", [])):
                # 次の兄弟がステップ情報を持つか簡易確認
                nxt_text = nxt.get_text()
                if any(c in nxt_text for c in ["号", "新幹線", "線", "発", "着"]):
                    step_el = nxt

        try:
            route_info = _parse_single_route(item, from_station, to_station, step_el=step_el)
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
