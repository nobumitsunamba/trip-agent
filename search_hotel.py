"""
search_hotel.py
楽天トラベル・じゃらんのホテル検索URLを自動生成するモジュール

APIキー不要。目的地・チェックイン日・予算の条件から
ブラウザで直接開ける検索URLを生成します。

楽天トラベル検索URL:
    https://travel.rakuten.co.jp/keyword/
    パラメータ: f_key（キーワード）, f_nen1/f_tsuki1/f_hi1（チェックイン年月日）,
                f_nen2/f_tsuki2/f_hi2（チェックアウト年月日）,
                f_otona_su（大人人数）, f_heya_su（部屋数）, f_max_kinsen（予算上限）

じゃらん検索URL:
    https://www.jalan.net/ys/jalan/ydo/kanko/YadoList.do
    パラメータ: f_keyword（キーワード）, f_checkin_ym（YYYYMM）,
                f_checkin_ymd（YYYYMMDD）, f_checkout_ymd（YYYYMMDD）,
                f_heya_su（部屋数）, f_otona_su（大人人数）, f_max_price（予算上限）
"""

from datetime import datetime, timedelta
from urllib.parse import urlencode


# ---- エンドポイント ----
RAKUTEN_SEARCH_URL = "https://travel.rakuten.co.jp/keyword/"
JALAN_SEARCH_URL   = "https://www.jalan.net/ys/jalan/ydo/kanko/YadoList.do"


def build_rakuten_url(destination: str, trip_date: str, budget: int) -> str:
    """
    楽天トラベルのホテル検索URLを生成する。

    Args:
        destination: 目的地（駅名・地名）
        trip_date:   チェックイン日（YYYY-MM-DD）
        budget:      予算上限（円/泊）

    Returns:
        楽天トラベル検索URL文字列
    """
    checkin  = datetime.strptime(trip_date, "%Y-%m-%d")
    checkout = checkin + timedelta(days=1)

    params = {
        "f_key":        destination,
        "f_nen1":       checkin.year,
        "f_tsuki1":     checkin.month,
        "f_hi1":        checkin.day,
        "f_nen2":       checkout.year,
        "f_tsuki2":     checkout.month,
        "f_hi2":        checkout.day,
        "f_otona_su":   1,
        "f_heya_su":    1,
        "f_max_kinsen": budget,
    }
    return RAKUTEN_SEARCH_URL + "?" + urlencode(params)


def build_jalan_url(destination: str, trip_date: str, budget: int) -> str:
    """
    じゃらんのホテル検索URLを生成する。

    Args:
        destination: 目的地（駅名・地名）
        trip_date:   チェックイン日（YYYY-MM-DD）
        budget:      予算上限（円/泊）

    Returns:
        じゃらん検索URL文字列
    """
    checkin  = datetime.strptime(trip_date, "%Y-%m-%d")
    checkout = checkin + timedelta(days=1)

    params = {
        "f_keyword":      destination,
        "f_checkin_ym":   checkin.strftime("%Y%m"),
        "f_checkin_ymd":  checkin.strftime("%Y%m%d"),
        "f_checkout_ymd": checkout.strftime("%Y%m%d"),
        "f_heya_su":      1,
        "f_otona_su":     1,
        "f_max_price":    budget,
    }
    return JALAN_SEARCH_URL + "?" + urlencode(params)


def build_hotel_search_urls(destination: str, trip_date: str, budget: int) -> dict:
    """
    楽天トラベル・じゃらんのホテル検索URLを生成して返す。

    Args:
        destination: 目的地（駅名・地名）
        trip_date:   チェックイン日（YYYY-MM-DD）
        budget:      予算上限（円/泊）

    Returns:
        dict:
            rakuten_url:   楽天トラベル検索URL
            jalan_url:     じゃらん検索URL
            destination:   目的地名
            checkin_date:  チェックイン日（YYYY-MM-DD）
            checkout_date: チェックアウト日（YYYY-MM-DD）
            budget:        予算上限（円）
    """
    checkin  = datetime.strptime(trip_date, "%Y-%m-%d")
    checkout = checkin + timedelta(days=1)

    return {
        "rakuten_url":   build_rakuten_url(destination, trip_date, budget),
        "jalan_url":     build_jalan_url(destination, trip_date, budget),
        "destination":   destination,
        "checkin_date":  trip_date,
        "checkout_date": checkout.strftime("%Y-%m-%d"),
        "budget":        budget,
    }
