#!/usr/bin/env python3
"""
Yahoo!乗換案内のHTML構造をデバッグ出力するスクリプト。

使い方:
    python debug_transit.py <出発駅> <到着駅> <着時刻> <日付>
    例: python debug_transit.py 東京 名古屋 10:00 2026-06-15

出力ファイル:
    route_item_1.html  ... 1件目の経路アイテムの生HTML
    route_item_1.txt   ... get_text("\\n") で取り出したテキスト行（行番号付き）
    route_item_N.html/txt  ... 最大3件分

標準出力:
    - 取得URLとHTTPステータス
    - 各経路アイテムのテキスト行（先頭30行）
    - _lines_to_segments の抽出結果

これらの出力を確認することで、Yahoo Transit の実際のHTML構造を把握し
セグメント抽出コードを改善するためのデバッグ情報を得られます。
"""

import sys
import os

# search_train の DEBUG モードを有効化
os.environ["TRANSIT_DEBUG"] = "1"

from bs4 import BeautifulSoup
from search_train import (
    build_yahoo_url, _make_session, parse_routes,
    _lines_to_segments, _extract_segments,
    YAHOO_TRANSIT_TOP,
)


def main() -> None:
    if len(sys.argv) < 5:
        print("使い方: python debug_transit.py <出発> <到着> <着時刻HH:MM> <日付YYYY-MM-DD>")
        print("例:     python debug_transit.py 東京 名古屋 10:00 2026-06-15")
        sys.exit(1)

    from_st, to_st, arr_time, trip_date = sys.argv[1:5]

    url = build_yahoo_url(from_st, to_st, arr_time, trip_date)
    print(f"=== Yahoo Transit URL ===\n{url}\n")

    session = _make_session()
    session.headers["Referer"] = YAHOO_TRANSIT_TOP

    try:
        resp = session.get(url, timeout=15)
        print(f"HTTP Status: {resp.status_code}")
        resp.raise_for_status()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    route_items = (
        soup.select("li.routeWrap")
        or soup.select("section.routeWrap")
        or soup.select("div[class*='route']")
    )

    print(f"\n=== 経路アイテム数: {len(route_items)} ===")

    for idx, item in enumerate(route_items[:3], 1):
        print(f"\n{'='*60}")
        print(f"=== 経路 {idx} ===")
        print(f"{'='*60}")

        # HTML保存
        html_path = f"route_item_{idx}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(str(item))
        print(f"HTML → {html_path}")

        # テキスト行抽出
        raw = item.get_text("\n")
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]

        txt_path = f"route_item_{idx}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            for j, ln in enumerate(lines, 1):
                f.write(f"{j:4d}: {ln}\n")
        print(f"Text ({len(lines)} lines) → {txt_path}")

        print("\n--- テキスト行（先頭40行）---")
        for j, ln in enumerate(lines[:40], 1):
            print(f"  {j:3d}: {ln!r}")
        if len(lines) > 40:
            print(f"  ... ({len(lines) - 40} 行省略)")

        # セグメント抽出
        print("\n--- _lines_to_segments の結果 ---")
        # TRANSIT_DEBUG=1 なので詳細ログも出力される
        segs = _lines_to_segments(lines)
        if segs:
            for seg in segs:
                if seg["type"] == "station":
                    print(f"  🚉 {seg.get('time',''):5s}  {seg['name']}")
                else:
                    print(f"       ↓  {seg['name']}")
        else:
            print("  (セグメントなし)")

        print("\n--- _extract_segments の結果（構造+テキスト併用） ---")
        full_segs = _extract_segments(item)
        if full_segs:
            for seg in full_segs:
                if seg["type"] == "station":
                    print(f"  🚉 {seg.get('time',''):5s}  {seg['name']}")
                else:
                    print(f"       ↓  {seg['name']}")
        else:
            print("  (セグメントなし)")

    print("\n=== parse_routes の結果 ===")
    routes = parse_routes(soup, from_st, to_st)
    for r in routes:
        print(f"\n  {r['dep_time']} → {r['arr_time']}  {r['duration']}  {r['fare']}")
        print(f"  路線: {r['route_summary']}")
        for seg in r.get("segments", []):
            if seg["type"] == "station":
                print(f"    🚉 {seg.get('time',''):5s}  {seg['name']}")
            else:
                print(f"         ↓  {seg['name']}")

    print(f"\nデバッグファイル: route_item_1.html / route_item_1.txt (など) を確認してください。")


if __name__ == "__main__":
    main()
