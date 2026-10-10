"""Design systems and synthetic bilingual content for the deliverables bake-off.

Everything here is synthetic. Company names, people, numbers and contact
details are invented; phone numbers use the reserved 090-0000-0000 /
03-0000-0000 shapes (never assigned to real subscribers).
"""
from __future__ import annotations

EMU_PER_INCH = 914400

# Two design systems so that every edit task exists in two visual languages and
# a runtime cannot pass by hard-coding one palette.
DESIGNS = {
    "harbor": {
        "name": "Harbor",
        "font_latin": "Noto Sans",
        "font_ea": "Noto Sans CJK JP",
        "font_pdf": "IPAGothic",
        "primary": "1F3A5F",
        "accent": "E07A1F",
        "text": "222222",
        "muted": "6B7280",
        "surface": "F3F5F8",
        "title_pt": 34,
        "body_pt": 18,
        "margin_in": 0.5,
        "columns": 12,
        "gutter_in": 0.2,
    },
    "sakura": {
        "name": "Sakura",
        "font_latin": "Noto Serif",
        "font_ea": "Noto Serif CJK JP",
        "font_pdf": "IPAGothic",
        "primary": "7A1F3D",
        "accent": "2F7D6D",
        "text": "1A1A1A",
        "muted": "5F6368",
        "surface": "FBF5F7",
        "title_pt": 32,
        "body_pt": 17,
        "margin_in": 0.6,
        "columns": 12,
        "gutter_in": 0.2,
    },
}

LANGS = ("ja", "en")


def palette(design: dict) -> set[str]:
    """Colours a deliverable may use (plus pure white/black)."""
    return {design[k].upper() for k in ("primary", "accent", "text", "muted", "surface")} | {
        "FFFFFF",
        "000000",
    }


def allowed_fonts(design: dict) -> set[str]:
    return {design["font_latin"], design["font_ea"]}


def grid_lines_in(design: dict, width_in: float) -> list[float]:
    """Left/right column edges of the layout grid, in inches."""
    m, n, g = design["margin_in"], design["columns"], design["gutter_in"]
    col = (width_in - 2 * m - (n - 1) * g) / n
    edges = []
    x = m
    for _ in range(n):
        edges.append(x)
        edges.append(x + col)
        x += col + g
    edges.append(width_in / 2)
    return edges


# Quarterly figures (synthetic, in million JPY). Edit tasks change Q4.
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
SERIES = {"Domestic": [120.0, 132.0, 141.0, 150.0], "Overseas": [80.0, 86.0, 95.0, 101.0]}
SERIES_Q4_UPDATE = {"Domestic": 163.0, "Overseas": 112.0}

REGIONS = [
    # region, revenue, growth %, comment-ja, comment-en
    ("Kanto", 210.0, 8.2, "主力。新規顧客が増加", "Core region; new accounts up"),
    ("Kansai", 96.0, 5.1, "既存顧客の深耕", "Deeper existing accounts"),
    ("Kyushu", 54.0, 12.4, "物流拠点の効果", "New logistics hub paying off"),
]
REGION_UPDATE = ("Tohoku", 31.0, 15.0, "パイロット開始", "Pilot started")

CONTENT = {
    "ja": {
        "company": "株式会社ミナトデータ（架空）",
        "deck_title": "2026年度 事業レビュー",
        "deck_subtitle": "FY2026 Business Review ― 経営会議資料",
        "agenda_title": "アジェンダ",
        "agenda": ["業績ハイライト", "四半期売上の推移", "地域別の状況", "課題と次の打ち手"],
        "kpi_title": "業績ハイライト",
        "kpis": [("売上高", "¥543M", "+9.8% YoY"), ("営業利益率", "14.2%", "+1.1pt"), ("顧客数", "1,284", "+212")],
        "chart_title": "四半期売上の推移（百万円）",
        "chart_caption": "国内・海外とも増収。Q4は季節要因で伸長。",
        "table_title": "地域別の状況",
        "table_head": ["地域", "売上（百万円）", "成長率", "コメント"],
        "mediocre_title": "課題について",
        "mediocre_body": (
            "現状いろいろな課題があり、たとえば採用が遅れていることや、システムの老朽化、"
            "あとは海外の為替の影響もあるし、営業の人員配置も見直しが必要だと思われるので、"
            "来期に向けて色々と検討していく必要があると考えている。また、お客様からの問い合わせ対応の"
            "品質にもばらつきがあり、マニュアルの整備や研修も必要。コストも上がっている。"
        ),
        "closing_title": "ご清聴ありがとうございました",
        "closing_sub": "お問い合わせ: 経営企画部 03-0000-0000（架空）",
        "notes": [
            "冒頭で目的を30秒で説明する。",
            "4つの論点を順に話す。",
            "売上高は過去最高。利益率改善の要因はコスト管理。",
            "Q4の伸びは季節要因と新製品。",
            "九州の成長率は物流拠点の効果。",
            "ここは議論の時間を取る。",
            "質疑応答へ移る。",
        ],
        "brief_new_deck": (
            "新サービス『ミナト・アナリティクス』の社内向け提案資料を6〜8枚で作成してください。"
            "構成: 表紙、背景と課題、提案内容、導入効果（数値: 工数30%削減、年間¥12M削減）、"
            "スケジュール（4フェーズ）、体制、リスクと対策、まとめ。各スライドに話者ノートを付けること。"
        ),
        "doc_title": "情報セキュリティ運用手順書（架空）",
        "doc_sections": [
            ("1. 目的", "本手順書は、株式会社ミナトデータ（架空）における情報資産の取扱いを定める。"),
            ("2. 適用範囲", "全従業員および業務委託先に適用する。Contractors are also covered."),
            ("3. 運用手順", "アクセス権は四半期ごとに棚卸しする。退職者のアカウントは当日中に無効化する。"),
            ("4. 改訂履歴", "2026年4月 初版。"),
        ],
        "doc_table_head": ["項目", "頻度", "担当"],
        "doc_table_rows": [("アクセス権棚卸し", "四半期", "情報システム部"), ("バックアップ検証", "月次", "運用チーム"), ("教育研修", "年次", "人事部")],
        "doc_insert_section": ("3.1 インシデント対応", "インシデントを検知した場合は30分以内に責任者へ報告する。"),
        "brief_new_doc": (
            "『リモートワーク運用ガイドライン（架空）』を作成してください。目次、見出し階層（2段）、"
            "ヘッダー（文書名）とフッター（ページ番号）、手順の番号付きリスト、責任分担の表（3列以上）を含め、"
            "A4で3〜5ページ程度。"
        ),
        "sheet_notes": "このシートは編集対象外です。注記: 数値はすべて架空。",
        "pdf_title": "製品仕様書 ミナト・センサー MX-200（架空）",
        "pdf_sections": [
            ("概要", "MX-200は温湿度を計測する小型センサーである。"),
            ("仕様", "測定範囲は-20℃〜60℃、精度は±0.3℃。"),
            ("サポート窓口", "技術サポート: 090-0000-0000（架空）、平日9時〜17時。"),
        ],
        "pdf_table_head": ["型番", "測定範囲", "電源", "価格"],
        "pdf_table_rows": [("MX-200", "-20〜60℃", "USB-C", "¥12,800"), ("MX-200P", "-40〜85℃", "USB-C/PoE", "¥19,800")],
        "watermark": "社外秘 DRAFT",
        "brief_new_pdf": (
            "『2026年度 品質報告書（架空）』をA4縦のPDFで作成してください。表紙、要約、"
            "不良率の推移表（月次6か月）、改善施策3点、まとめ。日本語フォントを埋め込み、ページ番号を付けること。"
        ),
    },
    "en": {
        "company": "Minato Data Inc. (fictitious)",
        "deck_title": "FY2026 Business Review",
        "deck_subtitle": "2026年度 事業レビュー - Executive meeting",
        "agenda_title": "Agenda",
        "agenda": ["Performance highlights", "Quarterly revenue", "Regional status", "Issues and next steps"],
        "kpi_title": "Performance highlights",
        "kpis": [("Revenue", "¥543M", "+9.8% YoY"), ("Operating margin", "14.2%", "+1.1pt"), ("Customers", "1,284", "+212")],
        "chart_title": "Quarterly revenue (JPY million)",
        "chart_caption": "Both domestic and overseas grew; Q4 seasonally strong.",
        "table_title": "Regional status",
        "table_head": ["Region", "Revenue (JPY m)", "Growth", "Comment"],
        "mediocre_title": "About the issues",
        "mediocre_body": (
            "There are currently various issues, for example hiring is behind schedule and the systems are "
            "getting old, and there is also the FX impact overseas, and the sales staffing probably needs a "
            "review too, so we think we need to consider various things for next year. Also the quality of "
            "customer inquiry handling varies, manuals and training are needed. Costs are rising as well."
        ),
        "closing_title": "Thank you",
        "closing_sub": "Contact: Corporate Planning 03-0000-0000 (fictitious)",
        "notes": [
            "Explain the purpose in 30 seconds.",
            "Walk through the four topics in order.",
            "Record revenue; margin improved through cost control.",
            "Q4 growth is seasonal plus the new product.",
            "Kyushu growth comes from the logistics hub.",
            "Leave time for discussion here.",
            "Move to Q&A.",
        ],
        "brief_new_deck": (
            "Create a 6-8 slide internal proposal deck for the new service 'Minato Analytics'. Structure: "
            "title, background and problem, proposal, impact (30% less effort, JPY 12M saved per year), "
            "timeline (4 phases), team, risks and mitigations, summary. Add speaker notes to every slide."
        ),
        "doc_title": "Information Security Operating Procedure (fictitious)",
        "doc_sections": [
            ("1. Purpose", "This procedure defines how Minato Data Inc. (fictitious) handles information assets."),
            ("2. Scope", "It applies to all employees and contractors. 業務委託先も対象とする。"),
            ("3. Procedure", "Access rights are reviewed quarterly. Leavers' accounts are disabled the same day."),
            ("4. Revision history", "April 2026: first edition."),
        ],
        "doc_table_head": ["Item", "Frequency", "Owner"],
        "doc_table_rows": [("Access review", "Quarterly", "IT"), ("Backup verification", "Monthly", "Operations"), ("Training", "Yearly", "HR")],
        "doc_insert_section": ("3.1 Incident response", "When an incident is detected, report it to the owner within 30 minutes."),
        "brief_new_doc": (
            "Write 'Remote Work Operating Guideline (fictitious)'. Include a table of contents, two heading "
            "levels, a header (document name) and footer (page number), numbered procedure steps and a "
            "responsibility table (3+ columns); about 3-5 A4 pages."
        ),
        "sheet_notes": "Do not edit this sheet. Note: all figures are fictitious.",
        "pdf_title": "Product specification Minato Sensor MX-200 (fictitious)",
        "pdf_sections": [
            ("Overview", "The MX-200 is a compact temperature and humidity sensor."),
            ("Specification", "Range -20 to 60 C, accuracy +/-0.3 C. 測定範囲は-20℃〜60℃。"),
            ("Support", "Technical support: 090-0000-0000 (fictitious), weekdays 9-17."),
        ],
        "pdf_table_head": ["Model", "Range", "Power", "Price"],
        "pdf_table_rows": [("MX-200", "-20 to 60 C", "USB-C", "JPY 12,800"), ("MX-200P", "-40 to 85 C", "USB-C/PoE", "JPY 19,800")],
        "watermark": "CONFIDENTIAL DRAFT",
        "brief_new_pdf": (
            "Create 'FY2026 Quality Report (fictitious)' as an A4 portrait PDF: cover, summary, a monthly "
            "defect-rate table (6 months), three improvement actions, conclusion. Embed fonts (Japanese "
            "terms appear) and add page numbers. Use bilingual section headings, e.g. 'Summary / 要約'."
        ),
    },
}

SYNTHETIC_PII = ["090-0000-0000"]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
UNITS = [120, 135, 128, 150, 162, 171]
PRICES = [980, 980, 1000, 1000, 1020, 1020]
JUL_UNITS, JUL_PRICE = 180, 1020
