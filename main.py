"""
main.py
出張検索エージェント - メインGUI

起動方法:
    python main.py

必要な準備:
    1. pip install -r requirements.txt
    （APIキーは不要。楽天トラベル・じゃらんの検索URLを自動生成します）
"""

import json
import os
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from search_hotel import build_hotel_search_urls
from search_train import search_trains

# ============================================================
# 定数・設定
# ============================================================
LAST_INPUT_FILE = os.path.join(os.path.dirname(__file__), "last_input.json")
DEFAULT_INPUT = {
    "from_station": "東京",
    "to_station": "名古屋",
    "arrival_time": "10:00",
    "trip_date": "2026-06-01",
    "hotel_budget": 15000,
}

APP_BG = "#F5F6FA"
HEADER_BG = "#2C3E50"
HEADER_FG = "#FFFFFF"
SECTION_BG = "#FFFFFF"
CARD_BG = "#FAFBFC"
CARD_BORDER = "#DDE1E7"
BTN_PRIMARY = "#3498DB"
BTN_TRAIN_BOOK = "#E67E22"
BTN_HOTEL_BOOK = "#27AE60"
BTN_YAHOO = "#6C63FF"
BTN_RAKUTEN = "#BF0000"   # 楽天トラベル（ブランドレッド）
BTN_JALAN   = "#E85500"   # じゃらん（ブランドオレンジ）
BTN_FG = "#FFFFFF"
LABEL_FG = "#2C3E50"
MUTED_FG = "#7F8C8D"
ERROR_FG = "#E74C3C"
FONT_BASE = ("Hiragino Sans", 11) if os.name == "posix" else ("Yu Gothic UI", 11)
FONT_BOLD = (FONT_BASE[0], 11, "bold")
FONT_LARGE = (FONT_BASE[0], 14, "bold")
FONT_SMALL = (FONT_BASE[0], 9)
FONT_HEADER = (FONT_BASE[0], 18, "bold")


# ============================================================
# 前回入力の読み書き
# ============================================================

def load_last_input() -> dict:
    """last_input.json から前回入力を読み込む。ファイルがない場合はデフォルト値を返す。"""
    try:
        if os.path.exists(LAST_INPUT_FILE):
            with open(LAST_INPUT_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # 必要なキーを補完
            merged = {**DEFAULT_INPUT, **data}
            return merged
    except Exception as e:
        print(f"[main] last_input.json 読み込みエラー: {e}")
    return dict(DEFAULT_INPUT)


def save_last_input(data: dict) -> None:
    """入力値を last_input.json に保存する。"""
    try:
        with open(LAST_INPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[main] last_input.json 保存エラー: {e}")


# ============================================================
# ウィジェットヘルパー
# ============================================================

def make_button(parent, text, command, bg=BTN_PRIMARY, fg=BTN_FG, font=None, padx=14, pady=6) -> tk.Button:
    font = font or FONT_BASE
    btn = tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, font=font,
        relief="flat", bd=0,
        activebackground=_darken(bg), activeforeground=fg,
        cursor="hand2", padx=padx, pady=pady,
    )
    return btn


def _darken(hex_color: str, factor: float = 0.85) -> str:
    """16進カラーを少し暗くする。"""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r, g, b = int(r * factor), int(g * factor), int(b * factor)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


def make_label_value(parent, label_text: str, value_text: str, row: int) -> None:
    tk.Label(parent, text=label_text, font=FONT_SMALL, fg=MUTED_FG, bg=CARD_BG, anchor="w").grid(
        row=row, column=0, sticky="w", padx=(8, 4), pady=1
    )
    tk.Label(parent, text=value_text, font=FONT_BASE, fg=LABEL_FG, bg=CARD_BG, anchor="w").grid(
        row=row, column=1, sticky="w", padx=(0, 8), pady=1
    )


# ============================================================
# メインアプリケーション
# ============================================================

class TripAgentApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("出張検索エージェント")
        self.configure(bg=APP_BG)
        self.resizable(True, True)
        self.minsize(900, 600)

        self._last_input = load_last_input()
        self._train_results: list[dict] = []
        self._hotel_urls: dict = {}   # build_hotel_search_urls の戻り値
        self._yahoo_url: str = ""

        self._build_ui()
        self.after(100, self._center_window)

    def _center_window(self):
        self.update_idletasks()
        w, h = 1050, 750
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ----------------------------------------------------------
    # UI構築
    # ----------------------------------------------------------

    def _build_ui(self):
        # ヘッダー
        header = tk.Frame(self, bg=HEADER_BG, pady=12)
        header.pack(fill="x")
        tk.Label(
            header, text="✈  出張検索エージェント",
            font=FONT_HEADER, bg=HEADER_BG, fg=HEADER_FG,
        ).pack()
        tk.Label(
            header, text="新幹線・ホテルをまとめて検索",
            font=FONT_SMALL, bg=HEADER_BG, fg="#BDC3C7",
        ).pack()

        # メインコンテナ（スクロール対応）
        outer = tk.Frame(self, bg=APP_BG)
        outer.pack(fill="both", expand=True, padx=16, pady=12)

        # 入力フォーム
        self._build_input_frame(outer)

        # 結果エリア（左右分割）
        self._results_frame = tk.Frame(outer, bg=APP_BG)
        self._results_frame.pack(fill="both", expand=True, pady=(10, 0))
        self._results_frame.columnconfigure(0, weight=1)
        self._results_frame.columnconfigure(1, weight=1)

        # 左：新幹線結果
        self._train_frame = self._make_section(self._results_frame, "🚄  新幹線候補", 0)
        # 右：ホテル結果
        self._hotel_frame = self._make_section(self._results_frame, "🏨  ホテル候補", 1)

        self._show_placeholder(self._train_frame, "「検索する」ボタンを押してください")
        self._show_placeholder(self._hotel_frame, "「検索する」ボタンを押してください")

    def _build_input_frame(self, parent):
        frame = tk.LabelFrame(
            parent, text="  検索条件  ",
            font=FONT_BOLD, bg=SECTION_BG, fg=LABEL_FG,
            relief="groove", bd=1, padx=12, pady=10,
        )
        frame.pack(fill="x")

        # 入力変数
        li = self._last_input
        self.var_from = tk.StringVar(value=li.get("from_station", ""))
        self.var_to = tk.StringVar(value=li.get("to_station", ""))
        self.var_arr = tk.StringVar(value=li.get("arrival_time", ""))
        self.var_date = tk.StringVar(value=li.get("trip_date", ""))
        self.var_budget = tk.StringVar(value=str(li.get("hotel_budget", 15000)))

        fields = [
            ("出発地（最寄り駅）", self.var_from, "例：東京"),
            ("目的地（最寄り駅）", self.var_to, "例：名古屋"),
            ("希望着時刻 (HH:MM)", self.var_arr, "例：10:00"),
            ("出張日 (YYYY-MM-DD)", self.var_date, "例：2026-06-15"),
            ("ホテル予算上限 (円/泊)", self.var_budget, "例：15000"),
        ]

        for col_offset, (label, var, placeholder) in enumerate(fields):
            col = col_offset * 2
            tk.Label(frame, text=label, font=FONT_SMALL, fg=MUTED_FG, bg=SECTION_BG).grid(
                row=0, column=col, sticky="w", padx=(6 if col > 0 else 0, 2), pady=(0, 2)
            )
            entry = tk.Entry(
                frame, textvariable=var, font=FONT_BASE,
                width=16, relief="solid", bd=1,
            )
            entry.grid(row=1, column=col, sticky="ew", padx=(6 if col > 0 else 0, 4), pady=2)
            frame.columnconfigure(col, weight=1)

        # 検索ボタン
        btn_frame = tk.Frame(frame, bg=SECTION_BG)
        btn_frame.grid(row=0, column=10, rowspan=2, padx=(8, 0), sticky="ns")
        make_button(
            btn_frame, "🔍  検索する", self._on_search,
            bg=BTN_PRIMARY, padx=20, pady=10, font=FONT_BOLD,
        ).pack(fill="y", expand=True)

    def _make_section(self, parent, title: str, col: int) -> tk.Frame:
        """タイトル付きのセクションフレームを作成して返す。"""
        outer = tk.Frame(parent, bg=APP_BG)
        outer.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        tk.Label(
            outer, text=title, font=FONT_LARGE,
            bg=APP_BG, fg=LABEL_FG, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(8, 4))

        content = tk.Frame(outer, bg=APP_BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        return content

    def _show_placeholder(self, frame: tk.Frame, msg: str):
        for w in frame.winfo_children():
            w.destroy()
        tk.Label(
            frame, text=msg, font=FONT_BASE,
            fg=MUTED_FG, bg=APP_BG,
        ).pack(pady=30)

    def _show_loading(self, frame: tk.Frame, msg: str):
        for w in frame.winfo_children():
            w.destroy()
        lbl = tk.Label(frame, text=f"⏳  {msg}", font=FONT_BASE, fg=MUTED_FG, bg=APP_BG)
        lbl.pack(pady=30)
        bar = ttk.Progressbar(frame, mode="indeterminate", length=200)
        bar.pack(pady=4)
        bar.start(10)
        return bar

    # ----------------------------------------------------------
    # 検索ロジック
    # ----------------------------------------------------------

    def _on_search(self):
        """検索ボタン押下時の処理。"""
        from_st = self.var_from.get().strip()
        to_st = self.var_to.get().strip()
        arr_time = self.var_arr.get().strip()
        trip_date = self.var_date.get().strip()
        budget_str = self.var_budget.get().strip()

        # 入力バリデーション
        if not all([from_st, to_st, arr_time, trip_date, budget_str]):
            messagebox.showwarning("入力エラー", "すべての項目を入力してください。")
            return

        try:
            budget = int(budget_str)
            if budget <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("入力エラー", "ホテル予算は正の整数で入力してください。")
            return

        import re
        if not re.match(r"^\d{1,2}:\d{2}$", arr_time):
            messagebox.showwarning("入力エラー", "着時刻は HH:MM 形式で入力してください（例: 10:00）。")
            return
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", trip_date):
            messagebox.showwarning("入力エラー", "出張日は YYYY-MM-DD 形式で入力してください（例: 2026-06-15）。")
            return

        # 入力を保存
        save_last_input({
            "from_station": from_st,
            "to_station": to_st,
            "arrival_time": arr_time,
            "trip_date": trip_date,
            "hotel_budget": budget,
        })

        # ローディング表示（新幹線のみ非同期）
        self._bar_train = self._show_loading(self._train_frame, "新幹線を検索中...")
        self._show_placeholder(self._hotel_frame, "ホテル検索リンクを生成中...")

        # ホテルURLは即時生成（API不要）
        self._hotel_urls = build_hotel_search_urls(to_st, trip_date, budget)
        self.after(0, self._render_hotel_search_links)

        # 新幹線はバックグラウンドで検索
        threading.Thread(
            target=self._run_train_search,
            args=(from_st, to_st, arr_time, trip_date),
            daemon=True,
        ).start()

    def _run_train_search(self, from_st, to_st, arr_time, trip_date):
        try:
            routes, yahoo_url = search_trains(from_st, to_st, arr_time, trip_date)
            self._train_results = routes
            self._yahoo_url = yahoo_url
        except Exception as e:
            self._train_results = []
            self._yahoo_url = ""
            print(f"[main] 新幹線検索エラー: {e}")
        finally:
            self.after(0, self._render_train_results)

    # _run_hotel_search は削除（URLは即時生成のため不要）

    # ----------------------------------------------------------
    # 結果レンダリング
    # ----------------------------------------------------------

    def _render_train_results(self):
        for w in self._train_frame.winfo_children():
            w.destroy()

        if not self._train_results:
            self._show_placeholder(self._train_frame, "新幹線の検索結果がありませんでした")
            return

        for i, route in enumerate(self._train_results):
            self._build_train_card(self._train_frame, route, i + 1)

        # Yahoo!乗換案内で開くボタン
        if self._yahoo_url:
            make_button(
                self._train_frame,
                "Yahoo!乗換案内で全経路を確認する →",
                lambda url=self._yahoo_url: webbrowser.open(url),
                bg=BTN_YAHOO, padx=10, pady=6,
            ).pack(fill="x", padx=4, pady=(6, 0))

    def _build_train_card(self, parent: tk.Frame, route: dict, index: int):
        card = tk.Frame(
            parent, bg=CARD_BG,
            relief="solid", bd=1,
            highlightbackground=CARD_BORDER,
        )
        card.pack(fill="x", padx=4, pady=4)
        card.columnconfigure(1, weight=1)

        # ---- カード左：番号バッジ ----
        badge_rows = 6  # セグメントがある場合でも十分な行数
        tk.Label(
            card, text=f" {index} ",
            font=(FONT_BASE[0], 13, "bold"),
            bg=BTN_TRAIN_BOOK, fg=BTN_FG,
            width=3,
        ).grid(row=0, column=0, rowspan=badge_rows, sticky="ns", padx=(0, 8))

        row = 0

        # ---- 路線サマリー（例: のぞみ123号 / JR神戸線→のぞみ316号）----
        tk.Label(
            card, text=route.get("route_summary", "—"),
            font=FONT_BOLD, fg=LABEL_FG, bg=CARD_BG, anchor="w",
            wraplength=400,
        ).grid(row=row, column=1, columnspan=3, sticky="w", padx=4, pady=(6, 2))
        row += 1

        # ---- トータル: 出発・到着・所要時間・料金 ----
        info_frame = tk.Frame(card, bg=CARD_BG)
        info_frame.grid(row=row, column=1, columnspan=3, sticky="w", padx=4, pady=(0, 4))
        row += 1

        for col, (lbl, val) in enumerate([
            ("🕐 出発", route.get("dep_time", "—")),
            ("🏁 到着", route.get("arr_time", "—")),
            ("⏱ 所要", route.get("duration", "—")),
            ("💴 料金", route.get("fare", "—")),
        ]):
            tk.Label(info_frame, text=lbl, font=FONT_SMALL, fg=MUTED_FG, bg=CARD_BG).grid(
                row=0, column=col * 2, padx=(8 if col > 0 else 0, 2)
            )
            tk.Label(info_frame, text=val, font=FONT_BOLD, fg=LABEL_FG, bg=CARD_BG).grid(
                row=0, column=col * 2 + 1, padx=(0, 12)
            )

        # ---- 経路詳細セクション ----
        segments = route.get("segments", [])
        if segments:
            # 区切り線
            sep = tk.Frame(card, bg=CARD_BORDER, height=1)
            sep.grid(row=row, column=1, columnspan=3, sticky="ew", padx=8, pady=(2, 4))
            row += 1

            detail_frame = tk.Frame(card, bg=CARD_BG)
            detail_frame.grid(row=row, column=1, columnspan=3, sticky="w", padx=8, pady=(0, 4))
            row += 1

            for seg in segments:
                if seg["type"] == "station":
                    # 駅行: 「🚉 07:00  東京」
                    t    = seg.get("time", "")
                    name = seg.get("name", "")
                    time_str = f"{t}  " if t else ""
                    # 乗り換え駅（先頭・末尾以外）は色を変える
                    is_endpoint = (
                        seg is segments[0]
                        or seg is next((s for s in reversed(segments) if s["type"] == "station"), None)
                    )
                    fg_color = LABEL_FG if is_endpoint else "#E67E22"
                    prefix   = "🚉" if is_endpoint else "🔄"
                    tk.Label(
                        detail_frame,
                        text=f"{prefix} {time_str}{name}",
                        font=FONT_BASE,
                        fg=fg_color, bg=CARD_BG,
                        anchor="w",
                    ).pack(anchor="w", pady=1)

                elif seg["type"] == "train":
                    # 列車行: 「  ↓  のぞみ123号」
                    tk.Label(
                        detail_frame,
                        text=f"    ↓  {seg['name']}",
                        font=FONT_SMALL,
                        fg=MUTED_FG, bg=CARD_BG,
                        anchor="w",
                    ).pack(anchor="w", pady=0)
        else:
            # セグメント取得できなかった場合は1行空ける
            row += 1

        # ---- 予約ボタン ----
        booking_name = route.get("booking_name", "予約サイトを開く")
        booking_url  = route.get("booking_url", "")
        make_button(
            card,
            f"🎫 {booking_name} で予約する",
            lambda url=booking_url: webbrowser.open(url),
            bg=BTN_TRAIN_BOOK, padx=10, pady=5,
        ).grid(row=row, column=1, columnspan=3, sticky="w", padx=4, pady=(4, 8))

    def _render_hotel_search_links(self):
        """ホテル検索リンクエリアを描画する（楽天トラベル・じゃらんのボタン）。"""
        for w in self._hotel_frame.winfo_children():
            w.destroy()

        urls = self._hotel_urls
        if not urls:
            self._show_placeholder(self._hotel_frame, "検索条件を入力してください")
            return

        # ---- 検索条件サマリーカード ----
        summary_card = tk.Frame(
            self._hotel_frame, bg=CARD_BG,
            relief="solid", bd=1,
            highlightbackground=CARD_BORDER,
        )
        summary_card.pack(fill="x", padx=4, pady=(4, 8))

        tk.Label(
            summary_card, text="🔍  検索条件",
            font=FONT_BOLD, fg=LABEL_FG, bg=CARD_BG, anchor="w",
        ).pack(anchor="w", padx=12, pady=(10, 4))

        sep = tk.Frame(summary_card, bg=CARD_BORDER, height=1)
        sep.pack(fill="x", padx=8, pady=(0, 6))

        # 条件テキスト
        budget     = urls["budget"]
        checkin    = urls["checkin_date"]
        checkout   = urls["checkout_date"]
        dest       = urls["destination"]

        info_frame = tk.Frame(summary_card, bg=CARD_BG)
        info_frame.pack(anchor="w", padx=12, pady=(0, 10))

        conditions = [
            ("📍 目的地",       dest),
            ("📅 チェックイン",  checkin),
            ("📅 チェックアウト", checkout),
            ("💴 予算上限",     f"¥{budget:,}円以下 / 泊"),
        ]
        for row_idx, (label, value) in enumerate(conditions):
            tk.Label(
                info_frame, text=label,
                font=FONT_SMALL, fg=MUTED_FG, bg=CARD_BG, anchor="w", width=14,
            ).grid(row=row_idx, column=0, sticky="w", pady=2)
            tk.Label(
                info_frame, text=value,
                font=FONT_BOLD, fg=LABEL_FG, bg=CARD_BG, anchor="w",
            ).grid(row=row_idx, column=1, sticky="w", padx=(4, 0), pady=2)

        # ---- 楽天トラベルで検索ボタン ----
        make_button(
            self._hotel_frame,
            "🏨  楽天トラベルで検索する  →",
            lambda: webbrowser.open(urls["rakuten_url"]),
            bg=BTN_RAKUTEN, padx=12, pady=10, font=FONT_BOLD,
        ).pack(fill="x", padx=4, pady=(0, 6))

        # ---- じゃらんで検索ボタン ----
        make_button(
            self._hotel_frame,
            "🏯  じゃらんで検索する  →",
            lambda: webbrowser.open(urls["jalan_url"]),
            bg=BTN_JALAN, padx=12, pady=10, font=FONT_BOLD,
        ).pack(fill="x", padx=4, pady=(0, 4))

        # ---- ヒントテキスト ----
        tk.Label(
            self._hotel_frame,
            text="※ ボタンをクリックするとブラウザで検索結果が開きます",
            font=FONT_SMALL, fg=MUTED_FG, bg=APP_BG,
        ).pack(anchor="w", padx=8, pady=(2, 0))


# ============================================================
# エントリポイント
# ============================================================

if __name__ == "__main__":
    app = TripAgentApp()
    app.mainloop()
