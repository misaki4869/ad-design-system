from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from statistics import median
from uuid import uuid4


MATCH_EXACT = "完全一致"
MATCH_PHRASE = "フレーズ一致"
MATCH_BROAD = "部分一致"
MATCH_TYPES = [MATCH_EXACT, MATCH_PHRASE, MATCH_BROAD]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def google_char_count(text: str) -> int:
    """Google換算の試作用カウント。ASCIIは1、それ以外は2で数える。"""
    return sum(1 if ord(char) < 128 else 2 for char in text)


def format_keyword(keyword: str, match_type: str) -> str:
    clean = keyword.strip().strip("[]").strip('"').strip()
    if match_type == MATCH_EXACT:
        return f"[{clean}]"
    if match_type == MATCH_PHRASE:
        return f'"{clean}"'
    return clean


def keyword_status(searches, competition: str, relevance: str = "高") -> str:
    if searches is None:
        return "未取得"
    try:
        volume = int(searches)
    except (TypeError, ValueError):
        return "未取得"
    if volume == 0:
        return "自動除外"
    if relevance != "高" or competition in {"高", "HIGH"}:
        return "要確認"
    if volume >= 20:
        return "推奨"
    if volume >= 10:
        return "採用候補"
    return "要確認"


def suggest_match_type(keyword: str, searches: int | None, relevance: str = "高") -> str:
    words = [word for word in keyword.replace("　", " ").split(" ") if word]
    if relevance != "高":
        return MATCH_EXACT
    if len(words) >= 4 or (searches is not None and searches < 20):
        return MATCH_EXACT
    return MATCH_PHRASE


def budget_scenarios(keywords: list[dict], target_clicks: dict[str, int] | None = None) -> list[dict]:
    selected = [row for row in keywords if row.get("selected", True)]
    lows = [float(row["low_bid"]) for row in selected if row.get("low_bid") not in (None, "", 0)]
    highs = [float(row["high_bid"]) for row in selected if row.get("high_bid") not in (None, "", 0)]
    if not lows and not highs:
        return []
    low_ref = median(lows) if lows else median(highs) * 0.55
    high_ref = median(highs) if highs else median(lows) * 1.6
    middle_ref = (low_ref + high_ref) / 2
    clicks = {"抑制案": 5, "標準案": 10, "上部表示重視案": 15}
    if target_clicks:
        for name in clicks:
            try:
                clicks[name] = max(1, int(target_clicks.get(name, clicks[name])))
            except (TypeError, ValueError):
                pass
    definitions = [
        ("抑制案", low_ref, clicks["抑制案"]),
        ("標準案", middle_ref, clicks["標準案"]),
        ("上部表示重視案", high_ref, clicks["上部表示重視案"]),
    ]
    return [
        {
            "name": name,
            "reference_cpc": round(cpc),
            "target_clicks": clicks,
            "daily_budget": round(cpc * clicks, -1),
            "monthly_budget": round(cpc * clicks * 30, -2),
        }
        for name, cpc, clicks in definitions
    ]


def should_suggest_campaign_split(keywords: list[dict]) -> tuple[bool, str]:
    bids = sorted(
        float(row["high_bid"])
        for row in keywords
        if row.get("selected", True) and row.get("high_bid") not in (None, "", 0)
    )
    if len(bids) < 4:
        return False, "判断に必要な入札データが不足しています。"
    lower = median(bids[: max(2, len(bids) // 2)])
    upper = median(bids[len(bids) // 2 :])
    if lower and upper / lower >= 2.5:
        return True, f"高額帯の中央値に約{upper / lower:.1f}倍の差があります。高入札群を別キャンペーンに分けると予算を管理しやすくなる可能性があります。"
    return False, "現時点では、入札水準だけを理由にキャンペーンを分ける必要性は高くありません。"


def campaign_split_proposal(campaign: dict) -> dict | None:
    """Build a concrete high-bid split proposal with source group context."""
    candidates = []
    for group in campaign.get("ad_groups", []):
        for row in group.get("keywords", []):
            if not row.get("selected", True) or row.get("high_bid") in (None, "", 0):
                continue
            candidates.append({
                "keyword": row.get("keyword", ""),
                "match_type": row.get("match_type", MATCH_PHRASE),
                "monthly_searches": row.get("monthly_searches"),
                "low_bid": row.get("low_bid"),
                "high_bid": float(row["high_bid"]),
                "group_name": group.get("name", ""),
                "target": group.get("target", ""),
            })
    if len(candidates) < 4:
        return None
    bid_values = [item["high_bid"] for item in candidates]
    bid_median = median(bid_values)
    threshold = bid_median * 1.75
    high_bid_rows = [item for item in candidates if item["high_bid"] >= threshold]
    if not high_bid_rows:
        highest = max(bid_values)
        high_bid_rows = [item for item in candidates if item["high_bid"] == highest]
    targets = list(dict.fromkeys(item["target"] for item in high_bid_rows if item["target"]))
    group_names = list(dict.fromkeys(item["group_name"] for item in high_bid_rows if item["group_name"]))
    if len(group_names) == 1:
        campaign_name = f"{group_names[0]} 高入札キーワードキャンペーン"
    elif all("マレーシア" in item["keyword"] for item in high_bid_rows):
        campaign_name = "マレーシア不動産 高入札キーワードキャンペーン"
    else:
        campaign_name = f"{campaign.get('name', '広告')} 高入札キーワード分割"
    return {
        "campaign_name": campaign_name,
        "targets": targets,
        "keywords": sorted(high_bid_rows, key=lambda item: item["high_bid"], reverse=True),
        "threshold": round(threshold),
    }


def keywords_complete_for_budget(campaign: dict) -> bool:
    groups = campaign.get("ad_groups", [])
    return bool(groups) and all(
        group.get("keywords_saved", False) and bool(group.get("keywords"))
        for group in groups
    )


def update_campaign_budget(campaign: dict) -> list[dict]:
    """Recalculate scenarios from every saved keyword table in the campaign."""
    if not keywords_complete_for_budget(campaign):
        campaign["budget_scenarios"] = []
        return []
    all_keywords = [row for group in campaign["ad_groups"] for row in group.get("keywords", [])]
    scenarios = budget_scenarios(all_keywords, campaign.get("budget_target_clicks"))
    campaign["budget_scenarios"] = scenarios
    selected = next(
        (item for item in scenarios if item["name"] == campaign.get("budget_choice")),
        None,
    )
    if selected:
        campaign["daily_budget"] = selected["daily_budget"]
    return scenarios


def empty_ad_group(name: str = "新しい広告グループ", target: str = "") -> dict:
    return {
        "id": new_id("group"),
        "name": name,
        "target": target,
        "intent": "",
        "service": "",
        "keywords": [],
        "keywords_saved": False,
        "headlines": [],
        "headlines_saved": False,
        "descriptions": [],
        "descriptions_saved": False,
        "paths": ["malaysia", "support"],
        "ctas": [],
    }


def empty_campaign(name: str = "キャンペーン1") -> dict:
    return {
        "id": new_id("campaign"),
        "name": name,
        "lp_url": "",
        "objective": "上部表示を重視",
        "locations": "日本、マレーシア",
        "language": "日本語",
        "network": "GoogleとGoogle検索パートナー",
        "target_mode": "併用",
        "targets": [],
        "manual_target_rows": [
            {"id": new_id("target"), "text": ""} for _ in range(3)
        ],
        "ai_target_rows": [],
        "excluded_targets": [],
        "excluded_target_rows": [
            {"id": new_id("excluded"), "text": ""} for _ in range(3)
        ],
        "negative_keywords": [],
        "daily_budget": None,
        "budget_choice": "未確定",
        "budget_target_clicks": {"抑制案": 5, "標準案": 10, "上部表示重視案": 15},
        "ad_groups": [],
    }


def new_project() -> dict:
    campaign = empty_campaign()
    return {
        "id": new_id("project"),
        "name": "新しい広告プロジェクト",
        "product": "",
        "objective": "",
        "lp_url": "",
        "lp_summary": "",
        "brand": "",
        "strengths": "",
        "shared_negative_keywords": [],
        "competitor_names": [],
        "ad_language": "日本語",
        "campaigns": [campaign],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "api_warning_yen": 200,
        "prototype_mode": True,
    }


SAMPLE_GROUPS = [
    ("賃貸希望層", "マレーシア不動産を賃貸したい層", "賃貸物件を探す", "賃貸サポート"),
    ("居住用不動産購入層", "居住用に購入したい層", "居住用物件を購入する", "購入サポート"),
    ("不動産投資検討層", "投資用に購入したい層", "投資収益と物件を比較する", "投資相談"),
    ("不動産会社比較層", "不動産会社を比較している層", "日本語対応の会社を比較する", "仲介サポート"),
    ("MM2H・移住検討層", "MM2Hや移住を考えている層", "ビザと住居を検討する", "移住サポート"),
    ("教育移住検討層", "教育移住を考えている家族", "学校と住居を検討する", "教育移住サポート"),
]


KEYWORD_SEEDS = {
    "賃貸": ["マレーシア 賃貸", "マレーシア コンドミニアム 賃貸", "クアラルンプール 賃貸", "モントキアラ 賃貸", "マレーシア 不動産 賃貸"],
    "居住用": ["マレーシア 不動産 購入", "マレーシア 物件 購入", "マレーシア マンション 購入", "マレーシア コンドミニアム 購入", "マレーシア 不動産 価格"],
    "投資": ["マレーシア 不動産 投資", "マレーシア 物件 投資", "マレーシア 不動産 利回り", "マレーシア マンション 投資", "海外 不動産 投資 マレーシア"],
    "比較": ["マレーシア 不動産 会社", "マレーシア 不動産 エージェント", "マレーシア 不動産 日系", "クアラルンプール 不動産", "マレーシア 不動産 仲介"],
    "MM2H": ["マレーシア MM2H", "マレーシア MM2H 条件", "マレーシア 移住 ビザ", "マレーシア 移住 物件", "MM2H 不動産 購入"],
    "教育": ["マレーシア 教育 移住", "マレーシア 親子 留学", "マレーシア 母子 留学", "マレーシア 教育移住 費用", "マレーシア 移住 賃貸"],
}


def suggest_ad_group_name(target: str) -> str:
    """試作AI用：ターゲット文から短い「〜層」の名称を作る。"""
    rules = [
        (("教育移住", "親子留学", "母子留学"), "教育移住検討層"),
        (("MM2H",), "MM2H・移住検討層"),
        (("比較",), "不動産会社比較層"),
        (("投資",), "不動産投資検討層"),
        (("居住用",), "居住用不動産購入層"),
        (("賃貸",), "賃貸希望層"),
        (("移住",), "海外移住検討層"),
        (("購入",), "不動産購入検討層"),
    ]
    for words, name in rules:
        if any(word in target for word in words):
            return name
    cleaned = target.strip().rstrip("層").replace("マレーシア", "").strip(" ・、")
    return f"{(cleaned[:14] or '新規ターゲット')}層"


def generate_mock_groups(campaign: dict) -> list[dict]:
    targets = [target.strip() for target in campaign.get("targets", []) if target.strip()]
    sample_by_target = {target: (name, intent, service) for name, target, intent, service in SAMPLE_GROUPS}
    groups = []
    for index, target in enumerate(targets, start=1):
        name, intent, service = sample_by_target.get(
            target,
            (suggest_ad_group_name(target), "このターゲットの検索意図を確認", "LPに関連するサービス"),
        )
        group = empty_ad_group(name, target)
        group["intent"] = intent
        group["service"] = service
        groups.append(group)
    return groups


def sync_ad_groups_to_targets(campaign: dict) -> list[dict]:
    """Keep exactly one ad group for every adopted target."""
    targets = [target.strip() for target in campaign.get("targets", []) if target.strip()]
    existing = {group.get("target", ""): group for group in campaign.get("ad_groups", [])}
    suggestions = {group["target"]: group for group in generate_mock_groups(campaign)}
    synced = []
    legacy_names = {"賃貸", "居住用購入", "投資用購入", "会社比較", "MM2Hと移住", "教育移住"}
    for target in targets:
        group = existing.get(target) or suggestions[target]
        group["target"] = target
        if group.get("name") in legacy_names or group.get("name", "").startswith("ターゲット"):
            group["name"] = suggest_ad_group_name(target)
        synced.append(group)
    campaign["ad_groups"] = synced
    return synced


def generate_mock_keywords(group: dict) -> list[dict]:
    source_text = f"{group.get('name', '')} {group.get('target', '')}"
    seed_key = next((key for key in KEYWORD_SEEDS if key in source_text), None)
    base = KEYWORD_SEEDS.get(seed_key, [f"マレーシア {group['name']}"])
    expanded = base + [f"{value} 日本語" for value in base]
    rows = []
    for idx, keyword in enumerate(expanded[:10]):
        rows.append(
            {
                "id": new_id("kw"),
                "selected": True,
                "keyword": keyword,
                "match_type": MATCH_PHRASE if idx % 3 else MATCH_EXACT,
                "copy_text": "",
                "monthly_searches": None,
                "competition": "未取得",
                "low_bid": None,
                "high_bid": None,
                "relevance": "高",
                "status": "未取得",
                "source": "試作AI候補",
            }
        )
    refresh_keyword_rows(rows)
    return rows


def fill_mock_metrics(keywords: list[dict]) -> list[dict]:
    volumes = [320, 170, 70, 50, 30, 20, 10, 10, 0, 40]
    competitions = ["低", "低", "低", "中", "低", "低", "中", "低", "低", "高"]
    lows = [25, 39, 52, 68, 19, 30, 49, None, None, 105]
    highs = [188, 211, 432, 811, 594, 324, 653, None, None, 1200]
    for idx, row in enumerate(keywords):
        row["monthly_searches"] = volumes[idx % len(volumes)]
        row["competition"] = competitions[idx % len(competitions)]
        row["low_bid"] = lows[idx % len(lows)]
        row["high_bid"] = highs[idx % len(highs)]
        row["match_type"] = suggest_match_type(row["keyword"], row["monthly_searches"], row.get("relevance", "高"))
    refresh_keyword_rows(keywords)
    return keywords


def refresh_keyword_rows(keywords: list[dict]) -> None:
    for row in keywords:
        row["copy_text"] = format_keyword(row.get("keyword", ""), row.get("match_type", MATCH_PHRASE))
        row["status"] = keyword_status(row.get("monthly_searches"), row.get("competition", "未取得"), row.get("relevance", "高"))
        if row["status"] == "自動除外":
            row["selected"] = False


def generate_mock_ads(group: dict, brand: str = "EQUINOX") -> None:
    subject = group.get("name", "不動産相談")
    headline_templates = [
        f"マレーシア{subject}を日本語相談",
        f"{subject}を現地でサポート",
        f"初めての{subject}も安心",
        f"{subject}の条件を比較",
        f"日本語で{subject}相談",
        f"現地視点で{subject}をご提案",
        f"{subject}の無料相談",
        f"希望に合う{subject}を探す",
        f"マレーシア生活をサポート",
        f"契約前の不安を相談",
        f"現地スタッフが日本語対応",
        f"目的に合うプランをご提案",
        f"相談から手続きまで対応",
        f"マレーシア専門チーム",
        f"{brand}に相談",
    ]
    description_templates = [
        f"マレーシアの{subject}を日本語で相談。比較から手続きまで現地で丁寧にサポートします。",
        f"初めての{subject}も安心。ご希望や予算に合わせて選択肢を分かりやすくご案内します。",
        f"現地情報と実績をもとに{subject}をご提案。まずは無料相談からお気軽にお問い合わせください。",
        f"物件選びだけでなく、契約や生活準備まで一貫して日本語でサポートします。",
    ]
    group["headlines"] = [
        {"id": new_id("headline"), "selected": False, "text": text, "normal_count": len(text), "google_count": google_char_count(text), "limit": 30}
        for text in headline_templates
    ]
    group["descriptions"] = [
        {"id": new_id("description"), "selected": False, "text": text, "normal_count": len(text), "google_count": google_char_count(text), "limit": 90}
        for text in description_templates
    ]
    group["ctas"] = ["無料相談を予約", "お問い合わせ", "サービス内容を見る"]


def make_sample_project() -> dict:
    project = new_project()
    project.update(
        {
            "name": "会社HPリスティング広告",
            "product": "EQUINOX PROPTECHの会社HP",
            "objective": "会社HPをスポンサー広告上部に表示し、サービスを探している利用者をサイトへ誘導する",
            "lp_url": "https://equinox-proptech.com/",
            "lp_summary": "マレーシア不動産の賃貸、購入、投資、移住、教育移住を日本語で支援する会社HP",
            "brand": "EQUINOX PROPTECH",
            "strengths": "一貫した日本語対応、幅広いサポート、実績、個別相談無料",
            "shared_negative_keywords": ["求人", "採用", "転職", "ホテル", "旅行", "民泊"],
        }
    )
    campaign = project["campaigns"][0]
    campaign["name"] = "会社HP メインキャンペーン"
    campaign["lp_url"] = project["lp_url"]
    campaign["targets"] = [target for _, target, _, _ in SAMPLE_GROUPS]
    campaign["manual_target_rows"] = [
        {"id": new_id("target"), "text": target} for target in campaign["targets"]
    ]
    campaign["excluded_targets"] = ["求人を探す人", "短期旅行者"]
    campaign["excluded_target_rows"] = [
        {"id": new_id("excluded"), "text": value}
        for value in campaign["excluded_targets"]
    ] + [{"id": new_id("excluded"), "text": ""}]
    campaign["ad_groups"] = generate_mock_groups(campaign)
    for group in campaign["ad_groups"]:
        group["keywords"] = fill_mock_metrics(generate_mock_keywords(group))
        generate_mock_ads(group, project["brand"])
        group["keywords_saved"] = True
        group["headlines_saved"] = True
        group["descriptions_saved"] = True
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return project


def clone_campaign_for_split(project: dict, source_campaign_id: str, proposal: dict | None = None) -> dict:
    source = next(c for c in project["campaigns"] if c["id"] == source_campaign_id)
    proposal = proposal or campaign_split_proposal(source) or {}
    proposed_targets = list(proposal.get("targets", []))
    clone = empty_campaign(proposal.get("campaign_name") or f"{source['name']} 高入札キーワード用")
    clone.update(
        {
            "lp_url": source.get("lp_url", ""),
            "objective": source.get("objective", "上部表示を重視"),
            "locations": source.get("locations", ""),
            "language": source.get("language", "日本語"),
            "network": source.get("network", "GoogleとGoogle検索パートナー"),
            "negative_keywords": deepcopy(source.get("negative_keywords", [])),
            "target_mode": "LPからAIが提案",
            "targets": proposed_targets,
            "manual_target_rows": [
                {"id": new_id("target"), "text": ""} for _ in range(3)
            ],
            "ai_target_rows": [
                {"id": new_id("ai_target"), "text": target, "selected": True}
                for target in proposed_targets
            ],
            "excluded_targets": [],
            "excluded_target_rows": [
                {"id": new_id("excluded"), "text": ""} for _ in range(3)
            ],
            "daily_budget": None,
            "budget_choice": "未確定",
            "ad_groups": [],
        }
    )
    project["campaigns"].append(clone)
    return clone
