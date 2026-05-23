"""
search_hotel.py
楽天トラベルAPIを使ってホテルを検索するモジュール（緯度経度ベース）

API仕様:
    VacantHotelSearch/20170426
        https://webservice.rakuten.co.jp/documentation/vacant-hotel-search
        必須: applicationId, checkinDate, checkoutDate + (latitude+longitude)
        sort: +roomCharge, -roomCharge, +hotelReviewAverage, -hotelReviewAverage
        ※ roomCharge は実際の空室料金。日付あり検索専用。

    SimpleHotelSearch/20170426
        https://webservice.rakuten.co.jp/documentation/simple-hotel-search
        必須: applicationId + (latitude+longitude)
        sort: +hotelMinCharge, -hotelMinCharge, +hotelReviewAverage, -hotelReviewAverage,
              +hotelNo, -hotelNo, +hotelName, -hotelName, standard
        ※ roomCharge は VacantHotelSearch 専用。SimpleHotelSearch で使うと 400 エラー。

    ※ datumType パラメータは v20170426 では不要（送ると 400 エラーの原因になる）
ジオコーディング:
    Nominatim（OpenStreetMap）を使用。APIキー不要。
    主要新幹線駅は座標辞書からハードコード値を即時返却（高速・安定）。
"""

import os
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

# ---- エンドポイント ----
VACANT_HOTEL_URL = "https://app.rakuten.co.jp/services/api/Travel/VacantHotelSearch/20170426"
SIMPLE_HOTEL_URL = "https://app.rakuten.co.jp/services/api/Travel/SimpleHotelSearch/20170426"
NOMINATIM_URL    = "https://nominatim.openstreetmap.org/search"

NOMINATIM_UA = "TripAgent/1.0 (business-trip-search; https://github.com/nobumitsunamba/trip-agent)"

# ---- 主要新幹線駅の座標ハードコード辞書（Nominatim を呼ばずに即時返却） ----
_STATION_COORDS: dict[str, tuple[float, float]] = {
    # 東海道・山陽新幹線
    "東京":      (35.6812, 139.7671),
    "品川":      (35.6285, 139.7388),
    "新横浜":    (35.5062, 139.6161),
    "小田原":    (35.2563, 139.1555),
    "熱海":      (35.1027, 139.0737),
    "三島":      (35.1233, 138.9107),
    "新富士":    (35.1497, 138.6750),
    "静岡":      (34.9757, 138.3829),
    "掛川":      (34.7508, 137.9969),
    "浜松":      (34.7035, 137.7323),
    "豊橋":      (34.7693, 137.3913),
    "三河安城":  (34.9787, 137.0810),
    "名古屋":    (35.1706, 136.8816),
    "岐阜羽島":  (35.3340, 136.7218),
    "米原":      (35.3199, 136.2862),
    "京都":      (34.9856, 135.7588),
    "新大阪":    (34.7334, 135.5000),
    "新神戸":    (34.6998, 135.1952),
    "西明石":    (34.6583, 134.9896),
    "姫路":      (34.8268, 134.6918),
    "相生":      (34.7974, 134.4667),
    "岡山":      (34.6555, 133.9191),
    "新倉敷":    (34.5870, 133.7691),
    "福山":      (34.4854, 133.3630),
    "新尾道":    (34.4209, 133.2809),
    "三原":      (34.3999, 133.0799),
    "東広島":    (34.3931, 132.7296),
    "広島":      (34.3966, 132.4596),
    "新岩国":    (34.1685, 132.2213),
    "徳山":      (34.0516, 131.8097),
    "新山口":    (34.1740, 131.4664),
    "厚狭":      (34.0509, 131.1661),
    "新下関":    (33.9548, 130.9421),
    "小倉":      (33.8831, 130.8750),
    "博多":      (33.5903, 130.4208),
    "新鳥栖":    (33.3780, 130.4744),
    "久留米":    (33.3201, 130.5050),
    "筑後船小屋": (33.1869, 130.5374),
    "新大牟田":  (33.0268, 130.4697),
    "熊本":      (32.7898, 130.7417),
    "新玉名":    (32.9315, 130.5462),
    "鹿児島中央": (31.5890, 130.5418),

    # 東北・北海道新幹線
    "上野":      (35.7141, 139.7774),
    "大宮":      (35.9062, 139.6239),
    "小山":      (36.3128, 139.7964),
    "宇都宮":    (36.5497, 139.7036),
    "那須塩原":  (36.9614, 140.0499),
    "新白河":    (37.1249, 140.2122),
    "郡山":      (37.3942, 140.3826),
    "福島":      (37.7602, 140.4744),
    "白石蔵王":  (38.0032, 140.5929),
    "仙台":      (38.2600, 140.8820),
    "古川":      (38.5754, 140.9534),
    "くりこま高原": (38.7447, 141.1266),
    "一ノ関":    (38.8858, 141.1275),
    "水沢江刺":  (39.1295, 141.1295),
    "北上":      (39.2917, 141.1125),
    "新花巻":    (39.3753, 141.1330),
    "盛岡":      (39.7027, 141.1578),
    "いわて沼宮内": (39.9305, 141.1015),
    "二戸":      (40.2691, 141.3009),
    "八戸":      (40.5124, 141.4884),
    "七戸十和田": (40.6849, 141.2074),
    "新青森":    (40.8257, 140.6874),
    "新函館北斗": (41.8279, 140.6521),

    # 上越・北陸新幹線
    "高崎":      (36.3232, 139.0032),
    "上毛高原":  (36.6873, 138.9099),
    "越後湯沢":  (36.9312, 138.8157),
    "浦佐":      (37.0997, 138.9689),
    "長岡":      (37.4476, 138.8456),
    "燕三条":    (37.6637, 138.8661),
    "新潟":      (37.9161, 139.0596),
    "安中榛名":  (36.3370, 138.8250),
    "軽井沢":    (36.3414, 138.6283),
    "佐久平":    (36.2494, 138.4809),
    "上田":      (36.4008, 138.2494),
    "長野":      (36.6447, 138.1882),
    "飯山":      (36.8515, 138.3686),
    "上越妙高":  (37.1279, 138.2413),
    "糸魚川":    (37.0393, 137.8582),
    "黒部宇奈月温泉": (36.8688, 137.5283),
    "富山":      (36.7021, 137.2122),
    "新高岡":    (36.5866, 136.9795),
    "金沢":      (36.5780, 136.6482),
    "小松":      (36.4068, 136.4428),
    "加賀温泉":  (36.3055, 136.3164),
    "芦原温泉":  (36.2209, 136.2269),
    "福井":      (36.0615, 136.2196),
    "越前たけふ": (35.9019, 136.1750),
    "敦賀":      (35.6454, 136.0603),

    # 山形・秋田新幹線（在来線直通）
    "山形":      (38.2528, 140.3396),
    "秋田":      (39.7183, 140.1024),

    # よく使う在来線ターミナル
    "大阪":      (34.7024, 135.4959),
    "神戸":      (34.6894, 135.1953),
    "三ノ宮":    (34.6913, 135.1948),
    "西宮":      (34.7349, 135.3409),
    "奈良":      (34.6851, 135.8325),
    "和歌山":    (34.2260, 135.1675),
    "天王寺":    (34.6460, 135.5136),
    "札幌":      (43.0686, 141.3507),
    "函館":      (41.7728, 140.7284),
    "旭川":      (43.7725, 142.3648),
}


def geocode_station(station_name: str) -> tuple[float, float] | None:
    """
    駅名/地名を (latitude, longitude) に変換する。

    1. ハードコード辞書から即時返却（主要新幹線駅）
    2. Nominatim（OpenStreetMap）で検索

    Nominatim 利用規約:
        https://operations.osmfoundation.org/policies/nominatim/
        - リクエスト間隔: 最低 1 秒以上
        - User-Agent 必須
    """
    # 1. ハードコード辞書から検索
    key = station_name.strip()
    if key in _STATION_COORDS:
        return _STATION_COORDS[key]

    # 辞書にない場合は部分マッチを試みる（例: "名古屋駅" → "名古屋"）
    for dict_key, coords in _STATION_COORDS.items():
        if key in dict_key or dict_key in key:
            return coords

    # 2. Nominatim ジオコーディング（外部API呼び出し）
    for query in [f"{key}駅", key]:
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "json",
                    "countrycodes": "jp",
                    "limit": 1,
                    "accept-language": "ja",
                },
                headers={"User-Agent": NOMINATIM_UA},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                _STATION_COORDS[key] = (lat, lon)  # キャッシュ
                return lat, lon
            time.sleep(1)  # Nominatim レート制限対応
        except Exception as e:
            print(f"[geocode] {query}: {e}")

    return None


def _get_app_id() -> str:
    """環境変数からAPIキーを取得する。"""
    app_id = os.getenv("RAKUTEN_APP_ID", "")
    if not app_id or app_id == "YOUR_RAKUTEN_APP_ID_HERE":
        raise EnvironmentError(
            ".env ファイルに RAKUTEN_APP_ID が設定されていません。\n"
            "https://webservice.rakuten.co.jp/ でアプリIDを取得し、\n"
            ".env ファイルに RAKUTEN_APP_ID=<ID> と記載してください。"
        )
    return app_id


def _format_price(price: int | None) -> str:
    if price is None:
        return "—"
    return f"¥{price:,}円〜"


def _build_hotel_url(hotel_no) -> str:
    return f"https://hotel.travel.rakuten.co.jp/hotelinfo/plan/list/{hotel_no}/"


def _extract_hotel_basic_info(hotel_wrap) -> dict:
    """APIレスポンスの1件エントリから hotelBasicInfo を取り出す。"""
    # 標準形式: {"hotel": [{"hotelBasicInfo": {...}}, ...]}
    if isinstance(hotel_wrap, dict) and "hotel" in hotel_wrap:
        for entry in hotel_wrap["hotel"]:
            if isinstance(entry, dict) and "hotelBasicInfo" in entry:
                return entry["hotelBasicInfo"]
    # formatVersion=2 形式: [{"hotelBasicInfo": {...}}, ...]
    if isinstance(hotel_wrap, list):
        for entry in hotel_wrap:
            if isinstance(entry, dict) and "hotelBasicInfo" in entry:
                return entry["hotelBasicInfo"]
    return {}


def parse_hotel_info(hotel_wrap) -> dict | None:
    """楽天トラベルAPIのレスポンスから1件のホテル情報を抽出する。"""
    try:
        basic = _extract_hotel_basic_info(hotel_wrap)
        if not basic:
            return None
        hotel_no   = basic.get("hotelNo", "")
        name       = basic.get("hotelName", "不明")
        address    = (basic.get("address1", "") + basic.get("address2", "")).strip()
        min_charge = basic.get("hotelMinCharge")
        review_avg = basic.get("reviewAverage")
        access     = basic.get("access", "")
        hotel_url  = basic.get("hotelInformationUrl") or _build_hotel_url(hotel_no)
        return {
            "hotel_no": str(hotel_no),
            "name": name,
            "address": address,
            "price": _format_price(min_charge),
            "min_charge_raw": min_charge,
            "review": f"★{review_avg}" if review_avg else "—",
            "access": access,
            "booking_url": hotel_url,
        }
    except Exception:
        return None


def _parse_hotels_response(data: dict, hits: int) -> list[dict]:
    """APIレスポンス JSON からホテルリストをパースして返す。"""
    hotels_raw = data.get("hotels", [])
    results = []
    for hotel_wrap in hotels_raw:
        info = parse_hotel_info(hotel_wrap)
        if info:
            results.append(info)
    return results[:hits]


def search_hotels_vacant_geo(
    latitude: float,
    longitude: float,
    trip_date: str,
    budget: int,
    hits: int = 3,
) -> list[dict]:
    """
    VacantHotelSearch/20170426 で緯度経度 + 日付から空室ホテルを検索する。

    ※ datumType は削除（v20170426 では lat/lon と同時に送ると 400 の原因になる場合がある）
    sort: +roomCharge（安い順）
    """
    checkout = (
        datetime.strptime(trip_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    params = {
        "applicationId": _get_app_id(),
        "latitude":      round(latitude, 6),
        "longitude":     round(longitude, 6),
        "searchRadius":  1.0,           # 半径1km
        "checkinDate":   trip_date,
        "checkoutDate":  checkout,
        "adultNum":      1,
        "roomNum":       1,
        "maxCharge":     budget,
        "hits":          hits,
        "sort":          "+roomCharge",  # 安い順
    }
    resp = requests.get(VACANT_HOTEL_URL, params=params, timeout=15)
    resp.raise_for_status()
    return _parse_hotels_response(resp.json(), hits)


def search_hotels_simple_geo(
    latitude: float,
    longitude: float,
    budget: int,
    hits: int = 3,
) -> list[dict]:
    """
    SimpleHotelSearch/20170426 で緯度経度からホテルを検索する（日付なし）。

    ※ datumType は削除（v20170426 では不要、送ると 400 の原因になる場合がある）
    ※ sort パラメータは省略（デフォルト: standard）
        +roomCharge / +hotelMinCharge ともに 400 になる環境があるため、
        まず sort なしで動作確認してから正しい値を追加する。
    """
    params = {
        "applicationId": _get_app_id(),
        "latitude":      round(latitude, 6),
        "longitude":     round(longitude, 6),
        "searchRadius":  1.0,
        "maxCharge":     budget,
        "hits":          hits,
        # sort は意図的に省略（デフォルト順）
    }
    resp = requests.get(SIMPLE_HOTEL_URL, params=params, timeout=15)
    resp.raise_for_status()
    return _parse_hotels_response(resp.json(), hits)


def search_hotels(
    destination: str,
    trip_date: str,
    budget: int,
) -> tuple[list[dict], str | None]:
    """
    楽天トラベルAPIで目的地駅周辺のホテルを最大3件検索する。

    手順:
      1. Nominatim / ハードコード辞書で目的地を緯度経度に変換
      2. VacantHotelSearch (空室・日付あり) で検索
      3. 失敗時: SimpleHotelSearch (日付なし) へフォールバック

    Returns:
        (hotels, error_message)  — 正常時 error_message は None
    """
    try:
        _get_app_id()
    except EnvironmentError as e:
        return [], str(e)

    # ジオコーディング
    coords = geocode_station(destination)
    if not coords:
        return [], (
            f"目的地「{destination}」の座標を取得できませんでした。\n"
            "駅名を正確に入力してください（例: 名古屋、新大阪、博多）。"
        )
    lat, lon = coords

    # 1. 空室検索（日付あり）
    try:
        hotels = search_hotels_vacant_geo(lat, lon, trip_date, budget)
        if hotels:
            return hotels, None
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"[search_hotel] VacantHotelSearch HTTP {status}: {e}")
    except Exception as e:
        print(f"[search_hotel] VacantHotelSearch エラー: {e}")

    # 2. フォールバック（日付なし）
    try:
        hotels = search_hotels_simple_geo(lat, lon, budget)
        return hotels, None
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        msg = f"楽天トラベルAPI エラー (HTTP {status}): {e}"
        print(f"[search_hotel] SimpleHotelSearch {msg}")
        return [], msg
    except Exception as e:
        msg = f"ホテル検索エラー: {e}"
        print(f"[search_hotel] {msg}")
        return [], msg
