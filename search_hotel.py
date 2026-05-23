"""
search_hotel.py
楽天トラベルAPIを使ってホテルを検索するモジュール

事前設定:
    .env ファイルに以下を記載してください:
        RAKUTEN_APP_ID=<楽天アプリID>

    楽天アプリIDは https://webservice.rakuten.co.jp/ で取得できます。
"""

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

RAKUTEN_VACANT_HOTEL_URL = (
    "https://app.rakuten.co.jp/services/api/Travel/VacantHotelSearch/20170426"
)
RAKUTEN_KEYWORD_HOTEL_URL = (
    "https://app.rakuten.co.jp/services/api/Travel/KeywordHotelSearch/20170426"
)


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


def _build_hotel_url(hotel_no: str | int) -> str:
    """楽天トラベルのホテル詳細ページURLを生成する。"""
    return f"https://hotel.travel.rakuten.co.jp/hotelinfo/plan/list/{hotel_no}/"


def parse_hotel_info(hotel_data: dict) -> dict | None:
    """楽天トラベルAPIのレスポンスから1件のホテル情報を抽出する。"""
    try:
        basic = hotel_data.get("hotelBasicInfo", {})
        hotel_no = basic.get("hotelNo", "")
        name = basic.get("hotelName", "不明")
        address = (basic.get("address1", "") + basic.get("address2", "")).strip()
        min_charge = basic.get("hotelMinCharge")
        review_avg = basic.get("reviewAverage")
        access = basic.get("access", "")
        hotel_url = basic.get("hotelInformationUrl") or _build_hotel_url(hotel_no)
        image_url = basic.get("hotelImageUrl", "")

        return {
            "hotel_no": str(hotel_no),
            "name": name,
            "address": address,
            "price": _format_price(min_charge),
            "min_charge_raw": min_charge,
            "review": f"★{review_avg}" if review_avg else "—",
            "access": access,
            "booking_url": hotel_url,
            "image_url": image_url,
        }
    except Exception:
        return None


def search_hotels_vacant(
    keyword: str,
    trip_date: str,
    budget: int,
    hits: int = 3,
) -> list[dict]:
    """
    VacantHotelSearch API（空室検索）でホテルを検索する。
    チェックイン日=trip_date、チェックアウト=翌日、1泊の条件。
    """
    app_id = _get_app_id()
    checkin = trip_date  # YYYY-MM-DD
    dt = datetime.strptime(trip_date, "%Y-%m-%d")
    checkout = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

    params = {
        "applicationId": app_id,
        "keyword": keyword,
        "checkinDate": checkin,
        "checkoutDate": checkout,
        "maxCharge": budget,
        "hits": hits,
        "sort": "+roomCharge",   # 料金の安い順
        "datumType": 1,          # 世界測地系
        "formatVersion": 2,
    }

    resp = requests.get(RAKUTEN_VACANT_HOTEL_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hotels_raw = data.get("hotels", [])
    results = []
    for hotel_wrap in hotels_raw:
        # APIレスポンス形式: [{"hotel": [{"hotelBasicInfo":...}, {"roomInfo":...}]}]
        if isinstance(hotel_wrap, list):
            hotel_data = hotel_wrap[0] if hotel_wrap else {}
        elif isinstance(hotel_wrap, dict):
            hotel_data = hotel_wrap.get("hotel", [{}])[0] if hotel_wrap.get("hotel") else hotel_wrap
        else:
            continue

        info = parse_hotel_info(hotel_data)
        if info:
            results.append(info)

    return results[:hits]


def search_hotels_keyword(
    keyword: str,
    budget: int,
    hits: int = 3,
) -> list[dict]:
    """
    KeywordHotelSearch API（キーワード検索）でホテルを検索する。
    日付を問わない場合や、空室APIが失敗した場合のフォールバックとして使用。
    """
    app_id = _get_app_id()

    params = {
        "applicationId": app_id,
        "keyword": keyword,
        "maxCharge": budget,
        "hits": hits,
        "sort": "+roomCharge",
        "datumType": 1,
        "formatVersion": 2,
    }

    resp = requests.get(RAKUTEN_KEYWORD_HOTEL_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hotels_raw = data.get("hotels", [])
    results = []
    for hotel_wrap in hotels_raw:
        if isinstance(hotel_wrap, list):
            hotel_data = hotel_wrap[0] if hotel_wrap else {}
        elif isinstance(hotel_wrap, dict):
            hotel_data = hotel_wrap.get("hotel", [{}])[0] if hotel_wrap.get("hotel") else hotel_wrap
        else:
            continue

        info = parse_hotel_info(hotel_data)
        if info:
            results.append(info)

    return results[:hits]


def search_hotels(
    destination: str,
    trip_date: str,
    budget: int,
) -> tuple[list[dict], str | None]:
    """
    楽天トラベルAPIでホテルを最大3件検索して返す。
    空室検索 → 失敗時にキーワード検索へフォールバック。

    Args:
        destination: 目的地（駅名・エリア名）
        trip_date:   出張日 (YYYY-MM-DD)
        budget:      1泊あたり予算上限（円）

    Returns:
        (hotels, error_message)
        hotels: ホテル候補リスト（最大3件）
        error_message: エラー発生時のメッセージ、正常時は None
    """
    try:
        _get_app_id()  # APIキー確認
    except EnvironmentError as e:
        return [], str(e)

    # 検索キーワードは目的地駅名をそのまま使用
    keyword = destination

    # 1. 空室検索を試みる
    try:
        hotels = search_hotels_vacant(keyword, trip_date, budget)
        if hotels:
            return hotels, None
    except requests.HTTPError as e:
        print(f"[search_hotel] 空室検索HTTP エラー: {e}")
    except requests.RequestException as e:
        print(f"[search_hotel] 空室検索通信エラー: {e}")
    except Exception as e:
        print(f"[search_hotel] 空室検索その他エラー: {e}")

    # 2. フォールバック: キーワード検索
    try:
        hotels = search_hotels_keyword(keyword, budget)
        return hotels, None
    except requests.HTTPError as e:
        msg = f"楽天トラベルAPI エラー: {e}"
        print(f"[search_hotel] {msg}")
        return [], msg
    except requests.RequestException as e:
        msg = f"楽天トラベルAPI 通信エラー: {e}"
        print(f"[search_hotel] {msg}")
        return [], msg
    except Exception as e:
        msg = f"ホテル検索中にエラーが発生しました: {e}"
        print(f"[search_hotel] {msg}")
        return [], msg
