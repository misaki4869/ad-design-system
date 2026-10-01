from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import importlib

import pandas as pd
import streamlit as st

import core as core_module

# Streamlitの開発中ホットリロードでも、更新済みの業務ロジックを確実に読み直す。
importlib.reload(core_module)

from core import (
    MATCH_TYPES,
    budget_scenarios,
    campaign_split_proposal,
    clone_campaign_for_split,
    empty_ad_group,
    empty_campaign,
    fill_mock_metrics,
    format_keyword,
    generate_mock_ads,
    generate_mock_groups,
    generate_mock_keywords,
    google_char_count,
    keywords_complete_for_budget,
    make_sample_project,
    new_id,
    new_project,
    refresh_keyword_rows,
    should_suggest_campaign_split,
    sync_ad_groups_to_targets,
    update_campaign_budget,
)
from document_export import build_project_docx
from storage import delete_project, list_projects, load_project, save_project


# Streamlit UI entry point.
st.set_page_config(page_title="広告設計支援アプリ", page_icon="📣", layout="wide")

st.markdown(
    """
    <style>
    :root {
      --main-bg: #ffffff;
      --cyan: #54daff;
      --purple: #b441ff;
      --error: #cc5252;
      --ink: #1f2933;
    }
    .stApp { background: var(--main-bg); color: var(--ink); }
    h1, h2, h3 { color: #111827; }
    [data-testid="stSidebar"] { background: #f8fbfd; }
    div.stButton > button[kind="primary"] {
      background: var(--purple); border-color: var(--purple); color: white;
    }
    div.stButton > button:not([kind="primary"]) {
      border-color: var(--cyan);
    }
    .prototype-note {
      padding: .7rem 1rem; border-left: 5px solid var(--cyan);
      background: #eafbff; border-radius: .35rem; margin-bottom: 1rem;
    }
    .ai-note {
      padding: .7rem 1rem; border-left: 5px solid var(--purple);
      background: #f6ecff; border-radius: .35rem; margin: .7rem 0;
    }
    .error-note {
      padding: .7rem 1rem; border-left: 5px solid var(--error);
      background: #fbeded; border-radius: .35rem; margin: .7rem 0;
    }
    .metric-card {
      padding: .8rem; border: 1px solid #d9d9d9; border-radius: .5rem;
      background: white; min-height: 8rem;
    }
    .metric-card.selected {
      border: 3px solid var(--purple); background: #f6ecff;
      box-shadow: 0 0 0 2px rgba(180,65,255,.08);
    }
    .metric-card.dimmed { opacity: .42; }
    div[data-testid="stVerticalBlockBorderWrapper"]:has(.ad-row-error-marker) {
      background: #fbeded; border-color: var(--error) !important;
      border-width: 2px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
      padding: .18rem .65rem !important;
      margin-bottom: .18rem !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] > div > [data-testid="stVerticalBlock"] {
      gap: 0 !important;
    }
    .ad-row-error-marker {
      color: var(--error); font-weight: 700; font-size: .66rem;
      line-height: .7rem; margin: 0; padding: 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def split_tags(value: str) -> list[str]:
    return [item.strip() for item in value.replace("、", ",").split(",") if item.strip()]


def tags_text(values: list[str]) -> str:
    return "、".join(values)


def slash_tags(value: str) -> list[str]:
    return [item.strip() for item in value.replace("／", "/").split("/") if item.strip()]


def slash_tags_text(values: list[str]) -> str:
    return "/".join(values)


def normalize_project_schema(p: dict) -> None:
    for campaign in p.get("campaigns", []):
        campaign.setdefault("lp_url", "")
        campaign.setdefault("target_mode", "併用")
        campaign.setdefault("budget_target_clicks", {"抑制案": 5, "標準案": 10, "上部表示重視案": 15})
        if "manual_target_rows" not in campaign:
            values = list(campaign.get("targets", []))
            campaign["manual_target_rows"] = [
                {"id": new_id("target"), "text": value} for value in values
            ]
        while len(campaign["manual_target_rows"]) < 3:
            campaign["manual_target_rows"].append({"id": new_id("target"), "text": ""})
        campaign.setdefault("ai_target_rows", [])
        if "excluded_target_rows" not in campaign:
            campaign["excluded_target_rows"] = [
                {"id": new_id("excluded"), "text": value}
                for value in campaign.get("excluded_targets", [])
            ]
        while len(campaign["excluded_target_rows"]) < 3:
            campaign["excluded_target_rows"].append({"id": new_id("excluded"), "text": ""})
        for group in campaign.get("ad_groups", []):
            group.setdefault("keywords_saved", False)
            group.setdefault("headlines_saved", False)
            group.setdefault("descriptions_saved", False)
            for kind, prefix in (("headlines", "headline"), ("descriptions", "description")):
                for row in group.get(kind, []):
                    row.setdefault("id", new_id(prefix))


def ensure_state():
    if "project" not in st.session_state:
        saved_projects = list_projects()
        st.session_state.project = load_project(saved_projects[0]) if saved_projects else make_sample_project()
    normalize_project_schema(st.session_state.project)
    if "nav" not in st.session_state:
        st.session_state.nav = "プロジェクト概要"
    if "step_by_campaign" not in st.session_state:
        st.session_state.step_by_campaign = {}
    if "flash" not in st.session_state:
        st.session_state.flash = ""
    if "review_mode" not in st.session_state:
        st.session_state.review_mode = False


def set_campaign_step(campaign_id: str, step: str) -> None:
    st.session_state.step_by_campaign[campaign_id] = step


def request_navigation(destination: str, step: str | None = None) -> None:
    """Queue navigation so widget-backed state is changed before widgets exist."""
    st.session_state.pending_navigation = {"destination": destination, "step": step}


def apply_pending_navigation() -> None:
    pending = st.session_state.pop("pending_navigation", None)
    if not pending:
        return
    destination = pending["destination"]
    st.session_state.nav = destination
    st.session_state.nav_widget = destination
    st.session_state.review_mode = False
    step = pending.get("step")
    if destination != "プロジェクト概要" and step:
        set_campaign_step(destination, step)
        st.session_state[f"step_widget_{destination}"] = step


def nav_changed() -> None:
    st.session_state.nav = st.session_state.nav_widget


def step_changed(campaign_id: str) -> None:
    st.session_state.step_by_campaign[campaign_id] = st.session_state[f"step_widget_{campaign_id}"]


def effective_lp_url(campaign: dict) -> str:
    return campaign.get("lp_url", "").strip() or project().get("lp_url", "").strip()


def project() -> dict:
    return st.session_state.project


def campaign_by_id(campaign_id: str) -> dict:
    return next(c for c in project()["campaigns"] if c["id"] == campaign_id)


def autosave():
    save_project(project())


def show_flash():
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = ""


def dataframe_records(editor_result: pd.DataFrame) -> list[dict]:
    records = editor_result.where(pd.notnull(editor_result), None).to_dict("records")
    for row in records:
        for key, value in list(row.items()):
            if hasattr(value, "item"):
                try:
                    row[key] = value.item()
                except ValueError:
                    pass
    return records


def sidebar():
    st.sidebar.title("📣 広告設計支援")
    st.sidebar.caption("試作品1A・外部API未接続")
    st.sidebar.markdown("**API費用**")
    st.sidebar.progress(0, text="¥0 / 警告 ¥200")
    st.sidebar.caption("1AではOpenAI APIを使わないため料金は発生しません。")

    if st.sidebar.button("＋ 新しいプロジェクト", width="stretch"):
        st.session_state.project = new_project()
        st.session_state.nav = "プロジェクト概要"
        st.session_state.step_by_campaign = {}
        st.session_state.review_mode = False
        autosave()
        st.rerun()

    if st.sidebar.button("サンプル案件を開く", width="stretch"):
        st.session_state.project = make_sample_project()
        st.session_state.nav = "プロジェクト概要"
        st.session_state.step_by_campaign = {}
        st.session_state.review_mode = False
        autosave()
        st.rerun()

    paths = list_projects()
    if paths:
        selected = st.sidebar.selectbox(
            "保存済みプロジェクト",
            options=paths,
            format_func=lambda path: path.stem.split("_", 2)[-1],
        )
        cols = st.sidebar.columns(2)
        if cols[0].button("開く", width="stretch"):
            st.session_state.project = load_project(selected)
            normalize_project_schema(st.session_state.project)
            st.session_state.nav = "プロジェクト概要"
            st.session_state.step_by_campaign = {}
            st.session_state.review_mode = False
            st.rerun()
        if cols[1].button("削除", width="stretch"):
            delete_project(load_project(selected)["id"])
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("**学習ポイント**")
    st.sidebar.caption("画面は app.py、業務ルールは core.py、保存は storage.py に分けています。")


def top_navigation():
    p = project()
    st.title("広告設計支援アプリ")
    st.markdown(
        '<div class="prototype-note">これは試作品1Aです。検索数・競合性・bid・AI生成は動作確認用のサンプルです。</div>',
        unsafe_allow_html=True,
    )
    options = ["プロジェクト概要"] + [c["id"] for c in p["campaigns"]]
    label_by_value = {"プロジェクト概要": "プロジェクト概要"} | {c["id"]: c["name"] for c in p["campaigns"]}
    if st.session_state.nav not in options:
        st.session_state.nav = "プロジェクト概要"
    if st.session_state.get("nav_widget") != st.session_state.nav:
        st.session_state.nav_widget = st.session_state.nav
    st.radio(
        "プロジェクトとキャンペーン",
        options,
        horizontal=True,
        format_func=lambda value: label_by_value[value],
        label_visibility="collapsed",
        key="nav_widget",
        on_change=nav_changed,
    )
    cols = st.columns([1, 5])
    if cols[0].button("＋ キャンペーン", width="stretch"):
        new_campaign = empty_campaign(f"キャンペーン{len(p['campaigns']) + 1}")
        p["campaigns"].append(new_campaign)
        st.session_state.nav = new_campaign["id"]
        set_campaign_step(new_campaign["id"], "1 基本情報")
        autosave()
        st.rerun()
    cols[1].caption(f"最終保存: {p.get('updated_at', '未保存')}")
    active_id = st.session_state.nav if st.session_state.nav != "プロジェクト概要" else None
    if st.button(
        "選択中のキャンペーンを削除",
        disabled=active_id is None,
        key="request_campaign_delete",
    ):
        st.session_state.pending_campaign_delete = active_id
    if st.session_state.get("pending_campaign_delete") == active_id and active_id:
        active_name = campaign_by_id(active_id)["name"]
        st.warning(f"「{active_name}」を削除します。元に戻せません。")
        confirm, cancel, _ = st.columns([1, 1, 4])
        if confirm.button("削除する", type="primary", key=f"confirm_delete_{active_id}"):
            p["campaigns"] = [c for c in p["campaigns"] if c["id"] != active_id]
            st.session_state.nav = "プロジェクト概要"
            st.session_state.pending_campaign_delete = None
            autosave()
            st.rerun()
        if cancel.button("キャンセル", key=f"cancel_delete_{active_id}"):
            st.session_state.pending_campaign_delete = None
            st.rerun()


def render_project_overview():
    p = project()
    show_flash()
    st.header("プロジェクト概要")
    left, right = st.columns(2)
    p["name"] = left.text_input("プロジェクト名", p.get("name", ""), key=f"project_name_{p['id']}")
    p["brand"] = right.text_input("会社・ブランド名", p.get("brand", ""), key=f"brand_{p['id']}")
    p["product"] = st.text_input("広告する商品・サービス", p.get("product", ""), key=f"product_{p['id']}")
    p["objective"] = st.text_area("広告プロジェクトの目的", p.get("objective", ""), key=f"objective_{p['id']}")
    p["lp_url"] = st.text_input("LP URL", p.get("lp_url", ""), key=f"lp_{p['id']}")
    if st.button("LPから情報を読み取る 試作"):
        p["lp_summary"] = "試作品1AではURL接続を行わず、サンプルのLP要約を表示します。"
        st.info(p["lp_summary"])
    p["lp_summary"] = st.text_area("LPの要約または手入力情報", p.get("lp_summary", ""), key=f"lp_summary_{p['id']}")
    p["strengths"] = st.text_area("会社・商品の強み", p.get("strengths", ""), key=f"strengths_{p['id']}")
    p["shared_negative_keywords"] = split_tags(
        st.text_area("プロジェクト共通の除外キーワード", tags_text(p.get("shared_negative_keywords", [])), help="読点またはカンマで区切ります。")
    )
    p["competitor_names"] = slash_tags(
        st.text_input(
            "広告文で使用しない競合他社名",
            slash_tags_text(p.get("competitor_names", [])),
            placeholder="例：競合A/競合B/競合C",
            help="複数ある場合は半角スラッシュ（/）で区切ってください。",
        )
    )
    st.caption("複数の競合他社名はスラッシュ（/）で区切って入力してください。")
    p["ad_language"] = st.selectbox("広告文の初期言語", ["日本語", "英語", "マレー語", "中国語"], index=["日本語", "英語", "マレー語", "中国語"].index(p.get("ad_language", "日本語")))

    cols = st.columns([1, 1, 3])
    if cols[0].button("保存", type="primary", width="stretch"):
        autosave()
        st.success("プロジェクトを保存しました。")

    st.subheader("キャンペーン一覧")
    summary = []
    warnings = []
    for campaign in p["campaigns"]:
        groups = campaign.get("ad_groups", [])
        selected_count = sum(
            1 for group in groups for row in group.get("keywords", []) if row.get("selected", True)
        )
        over_limit = sum(
            1
            for group in groups
            for row in group.get("headlines", []) + group.get("descriptions", [])
            if row.get("google_count", 0) > row.get("limit", 999)
        )
        if over_limit:
            warnings.append(f"{campaign['name']}: 文字数超過 {over_limit}件")
        summary.append(
            {
                "キャンペーン": campaign["name"],
                "広告グループ": len(groups),
                "採用キーワード": selected_count,
                "日予算": str(campaign.get("daily_budget") or "未確定"),
                "予算案": campaign.get("budget_choice", "未確定"),
            }
        )
    st.dataframe(pd.DataFrame(summary), width="stretch", hide_index=True)
    if warnings:
        st.markdown('<div class="error-note">' + "<br>".join(warnings) + "</div>", unsafe_allow_html=True)
    else:
        st.success("現在、文字数超過の警告はありません。")

    if st.button("プロジェクト全体の確認画面を開く", type="primary"):
        st.session_state.review_mode = True
        st.rerun()
    autosave()


def collect_review_issues(p: dict) -> list[dict]:
    issues: list[dict] = []

    def add(location: str, problem: str, campaign_id: str | None = None, step: str | None = None, group_id: str | None = None):
        issues.append({
            "location": location,
            "problem": problem,
            "campaign_id": campaign_id,
            "step": step,
            "group_id": group_id,
        })

    for field, label in (("name", "プロジェクト名"), ("brand", "会社・ブランド名"), ("product", "商品・サービス"), ("objective", "プロジェクト目的")):
        if not str(p.get(field, "")).strip():
            add("プロジェクト概要", f"{label}が空欄です。")

    for campaign in p.get("campaigns", []):
        campaign_id = campaign["id"]
        campaign_name = campaign.get("name") or "名称未設定キャンペーン"
        for field, label in (("name", "キャンペーン名"), ("objective", "広告目的"), ("locations", "配信地域"), ("language", "言語"), ("network", "検索ネットワーク")):
            if not str(campaign.get(field, "")).strip():
                add(campaign_name, f"{label}が空欄です。", campaign_id, "1 基本情報")
        if not effective_lp_url(campaign):
            add(campaign_name, "LP URLが空欄です。", campaign_id, "1 基本情報")
        if not campaign.get("targets"):
            add(campaign_name, "採用ターゲットがありません。", campaign_id, "1 基本情報")
        groups = campaign.get("ad_groups", [])
        if not groups:
            add(campaign_name, "広告グループがありません。", campaign_id, "2 広告グループ")

        for group in groups:
            group_id = group["id"]
            group_name = group.get("name") or "名称未設定広告グループ"
            location = f"{campaign_name} ＞ {group_name}"
            for field, label in (("name", "広告グループ名"), ("target", "ターゲット"), ("intent", "検索意図"), ("service", "サービス")):
                if not str(group.get(field, "")).strip():
                    add(location, f"{label}が空欄です。", campaign_id, "2 広告グループ", group_id)

            keywords = group.get("keywords", [])
            if not keywords:
                add(location, "キーワード表が作成されていません。", campaign_id, "3 キーワード", group_id)
            else:
                if not any(row.get("selected") for row in keywords):
                    add(location, "採用キーワードが1件も選ばれていません。", campaign_id, "3 キーワード", group_id)
                if any(not str(row.get("keyword", "")).strip() for row in keywords):
                    add(location, "空欄のキーワードがあります。", campaign_id, "3 キーワード", group_id)
                if any(row.get("low_bid") in (None, "") or row.get("high_bid") in (None, "") for row in keywords):
                    add(location, "入札指標が未取得のキーワードがあります。", campaign_id, "3 キーワード", group_id)
            if not group.get("keywords_saved", False):
                add(location, "キーワード表が保存されていません。", campaign_id, "3 キーワード", group_id)

            for kind, label, step in (("headlines", "見出し", "4 広告文"), ("descriptions", "説明文", "4 広告文")):
                rows = group.get(kind, [])
                if not rows:
                    add(location, f"{label}が生成されていません。", campaign_id, step, group_id)
                else:
                    if not group.get(f"{kind}_saved", False):
                        add(location, f"{label}の表が保存されていません。", campaign_id, step, group_id)
                    for index, row in enumerate(rows, start=1):
                        if not str(row.get("text", "")).strip():
                            add(location, f"{label}{index}が空欄です。", campaign_id, step, group_id)
                        elif row.get("google_count", google_char_count(row.get("text", ""))) > row.get("limit", 30 if kind == "headlines" else 90):
                            add(location, f"{label}{index}が文字数上限を超えています。", campaign_id, step, group_id)

        if campaign.get("budget_choice", "未確定") == "未確定":
            add(campaign_name, "予算案がまだ採用されていません。", campaign_id, "3 キーワード")
    return issues


def open_review_issue(issue: dict) -> None:
    st.session_state.review_mode = False
    campaign_id = issue.get("campaign_id")
    if not campaign_id:
        st.session_state.nav = "プロジェクト概要"
        st.session_state.nav_widget = "プロジェクト概要"
        return
    st.session_state.nav = campaign_id
    st.session_state.nav_widget = campaign_id
    step = issue.get("step") or "1 基本情報"
    set_campaign_step(campaign_id, step)
    st.session_state[f"step_widget_{campaign_id}"] = step
    group_id = issue.get("group_id")
    if group_id and step == "3 キーワード":
        st.session_state[f"kw_group_{campaign_id}"] = group_id
    if group_id and step == "4 広告文":
        st.session_state[f"ads_group_{campaign_id}"] = group_id


def render_review():
    p = project()
    if st.button("← 設計に戻る", type="primary", key="back_to_design"):
        st.session_state.review_mode = False
        st.rerun()
    st.title("最終確認とWord出力")
    st.markdown(
        '<div class="prototype-note">すべてのキャンペーンを1つの広告プロジェクトとして確認します。</div>',
        unsafe_allow_html=True,
    )
    rows = []
    for campaign in p.get("campaigns", []):
        groups = campaign.get("ad_groups", [])
        rows.append(
            {
                "キャンペーン": campaign["name"],
                "LP": effective_lp_url(campaign) or "未設定",
                "広告グループ": len(groups),
                "採用キーワード": sum(
                    1 for group in groups for row in group.get("keywords", []) if row.get("selected", True)
                ),
                "日予算": str(campaign.get("daily_budget") or "未確定"),
                "予算案": campaign.get("budget_choice", "未確定"),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.subheader("修正が必要な箇所")
    issues = collect_review_issues(p)
    if issues:
        st.caption(f"{len(issues)}件あります。「ここを直す」から該当する設計画面へ移動できます。")
        header = st.columns([3, 6, 1.5])
        header[0].markdown("**該当箇所**")
        header[1].markdown("**確認内容**")
        header[2].markdown("**修正**")
        for index, issue in enumerate(issues):
            columns = st.columns([3, 6, 1.5])
            columns[0].write(issue["location"])
            columns[1].write(issue["problem"])
            if columns[2].button("ここを直す →", key=f"fix_issue_{index}", type="tertiary"):
                open_review_issue(issue)
                st.rerun()
    else:
        st.success("修正が必要な箇所はありません。Word出力の準備ができています。")
    for campaign in p.get("campaigns", []):
        with st.expander(campaign["name"], expanded=False):
            st.write(f"広告目的：{campaign.get('objective', '')}")
            st.write(f"配信地域：{campaign.get('locations', '')}")
            st.write(f"採用ターゲット：{tags_text(campaign.get('targets', [])) or '未設定'}")
            st.write(f"除外ターゲット：{tags_text(campaign.get('excluded_targets', [])) or '未設定'}")
            st.write(f"広告グループ数：{len(campaign.get('ad_groups', []))}")
    st.subheader("プロジェクト全体のWord出力")
    docx_data = build_project_docx(p)
    st.download_button(
        "Wordファイルを作成してダウンロード",
        data=docx_data,
        file_name=f"{p['name']}_広告設計書.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        type="primary",
    )


def render_target_rows(campaign: dict) -> None:
    mode = campaign["target_mode"]
    if mode in ("LPからAIが提案", "併用"):
        st.markdown("**AIの提案**")
        st.caption("チェックした候補だけが採用されます。")
        if st.button("LPからターゲットを提案 試作", key=f"target_mock_{campaign['id']}"):
            campaign["ai_target_rows"] = [
                {"id": new_id("ai_target"), "text": text, "selected": True}
                for text in [
                    "マレーシア不動産を検討する日本語話者",
                    "海外移住を検討する家族",
                    "海外不動産投資を検討する人",
                ]
            ]
            autosave()
            st.rerun()
        if not campaign.get("ai_target_rows"):
            st.info("上のボタンを押すと、AIのターゲット候補がここに表示されます。")
        for row in campaign.get("ai_target_rows", []):
            check_col, text_col = st.columns([1, 12])
            row["selected"] = check_col.checkbox(
                "採用",
                value=bool(row.get("selected", True)),
                key=f"ai_target_selected_{row['id']}",
                label_visibility="collapsed",
            )
            row["text"] = text_col.text_input(
                "AIターゲット候補",
                row.get("text", ""),
                key=f"ai_target_text_{row['id']}",
                label_visibility="collapsed",
            )

    if mode in ("自分で入力", "併用"):
        st.markdown("**自分で入力するターゲット**")
        st.caption("入力したターゲットはすべて採用され、1ターゲットにつき1広告グループを作ります。")
        for index, row in enumerate(campaign["manual_target_rows"], start=1):
            row["text"] = st.text_input(
                f"ターゲット {index}",
                row.get("text", ""),
                key=f"manual_target_{row['id']}",
                placeholder="例：マレーシア不動産を賃貸したい層",
            )
        if st.button("＋ ターゲットを追加", key=f"add_target_{campaign['id']}"):
            campaign["manual_target_rows"].append({"id": new_id("target"), "text": ""})
            st.rerun()

    ai_targets = [
        row.get("text", "").strip()
        for row in campaign.get("ai_target_rows", [])
        if row.get("selected") and row.get("text", "").strip()
    ] if mode in ("LPからAIが提案", "併用") else []
    manual_targets = [
        row.get("text", "").strip()
        for row in campaign.get("manual_target_rows", [])
        if row.get("text", "").strip()
    ] if mode in ("自分で入力", "併用") else []
    campaign["targets"] = list(dict.fromkeys(ai_targets + manual_targets))
    st.caption(f"現在の採用ターゲット：{len(campaign['targets'])}件 → 広告グループも{len(campaign['targets'])}件になります。")


def render_excluded_target_rows(campaign: dict) -> None:
    st.markdown("**除外ターゲット**")
    for index, row in enumerate(campaign["excluded_target_rows"], start=1):
        row["text"] = st.text_input(
            f"除外ターゲット {index}",
            row.get("text", ""),
            key=f"excluded_target_{row['id']}",
            placeholder="例：求人を探している人",
        )
    if st.button("＋ 除外ターゲットを追加", key=f"add_excluded_target_{campaign['id']}"):
        campaign["excluded_target_rows"].append({"id": new_id("excluded"), "text": ""})
        st.rerun()
    campaign["excluded_targets"] = [
        row.get("text", "").strip()
        for row in campaign["excluded_target_rows"]
        if row.get("text", "").strip()
    ]


def step_one(campaign: dict):
    st.subheader("1 基本情報 ターゲット 除外")
    left, right = st.columns(2)
    campaign["name"] = left.text_input("キャンペーン名", campaign["name"], key=f"cname_{campaign['id']}")
    campaign["objective"] = right.selectbox(
        "広告目的",
        ["上部表示を重視", "ウェブサイト流入", "問い合わせ獲得", "認知拡大"],
        index=max(0, ["上部表示を重視", "ウェブサイト流入", "問い合わせ獲得", "認知拡大"].index(campaign.get("objective", "上部表示を重視"))),
        key=f"cobjective_{campaign['id']}",
    )
    campaign["locations"] = left.text_input("配信地域", campaign.get("locations", ""), key=f"locations_{campaign['id']}")
    campaign["language"] = right.selectbox("言語", ["日本語", "英語", "マレー語", "中国語"], index=["日本語", "英語", "マレー語", "中国語"].index(campaign.get("language", "日本語")), key=f"language_{campaign['id']}")
    campaign["network"] = st.selectbox("検索ネットワーク", ["Google", "GoogleとGoogle検索パートナー"], index=["Google", "GoogleとGoogle検索パートナー"].index(campaign.get("network", "GoogleとGoogle検索パートナー")), key=f"network_{campaign['id']}")

    lp_key = f"campaign_lp_{campaign['id']}"
    lp_cols = st.columns([4, 1])
    if lp_cols[1].button("プロジェクトのURLと同じ", key=f"copy_project_lp_{campaign['id']}", width="stretch"):
        campaign["lp_url"] = project().get("lp_url", "")
        st.session_state[lp_key] = campaign["lp_url"]
        autosave()
        st.rerun()
    campaign["lp_url"] = lp_cols[0].text_input(
        "キャンペーンのLP URL",
        campaign.get("lp_url", ""),
        key=lp_key,
        help="AI生成ではこのURLをプロジェクト共通URLより優先します。空欄の場合はプロジェクト共通URLを使います。",
    )
    st.caption("このキャンペーンでAIが参照するLP: " + (effective_lp_url(campaign) or "未設定"))

    campaign["target_mode"] = st.radio(
        "ターゲット層の決定",
        ["自分で入力", "LPからAIが提案", "併用"],
        index=["自分で入力", "LPからAIが提案", "併用"].index(campaign.get("target_mode", "併用")),
        horizontal=True,
        key=f"target_mode_{campaign['id']}",
    )
    render_target_rows(campaign)
    st.markdown("---")
    render_excluded_target_rows(campaign)
    st.caption("プロジェクト共通除外: " + (tags_text(project().get("shared_negative_keywords", [])) or "未設定"))
    campaign["negative_keywords"] = split_tags(st.text_area("キャンペーン固有の除外キーワード", tags_text(campaign.get("negative_keywords", [])), key=f"negative_{campaign['id']}"))
    if st.button("保存して広告グループへ", type="primary", key=f"to_groups_{campaign['id']}"):
        sync_ad_groups_to_targets(campaign)
        autosave()
        set_campaign_step(campaign["id"], "2 広告グループ")
        st.rerun()


def step_two(campaign: dict):
    st.subheader("2 広告グループ")
    st.markdown('<div class="ai-note">ターゲット、検索意図、提供サービスの違いから広告グループを提案します。</div>', unsafe_allow_html=True)
    sync_ad_groups_to_targets(campaign)
    cols = st.columns([1, 1, 4])
    if cols[0].button("試作AIで生成", type="primary", key=f"gen_groups_{campaign['id']}"):
        campaign["ad_groups"] = generate_mock_groups(campaign)
        autosave()
        st.rerun()
    if cols[1].button("1件追加", key=f"add_group_{campaign['id']}"):
        campaign.setdefault("ad_groups", []).append(empty_ad_group())
        st.rerun()

    groups = campaign.get("ad_groups", [])
    rows = [{"id": g["id"], "広告グループ": g["name"], "ターゲット": g["target"], "検索意図": g["intent"], "サービス": g["service"]} for g in groups]
    edited = st.data_editor(
        pd.DataFrame(rows, columns=["id", "広告グループ", "ターゲット", "検索意図", "サービス"]),
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        column_config={"id": None},
        key=f"groups_editor_{campaign['id']}",
    )
    old_by_id = {g["id"]: g for g in groups}
    updated = []
    for row in dataframe_records(edited):
        group_id = row.get("id") or new_id("group")
        group = old_by_id.get(group_id, empty_ad_group())
        group["id"] = group_id
        group["name"] = row.get("広告グループ") or "新しい広告グループ"
        group["target"] = row.get("ターゲット") or ""
        group["intent"] = row.get("検索意図") or ""
        group["service"] = row.get("サービス") or ""
        updated.append(group)
    campaign["ad_groups"] = updated
    if st.button("保存してキーワードへ", type="primary", key=f"to_keywords_{campaign['id']}"):
        autosave()
        set_campaign_step(campaign["id"], "3 キーワード")
        st.rerun()


def keyword_editor(campaign: dict, group: dict):
    buttons = st.columns([1, 1, 1, 3])
    if buttons[0].button("候補を生成", type="primary", key=f"gen_kw_{group['id']}"):
        group["keywords"] = generate_mock_keywords(group)
        group["keywords_saved"] = False
        update_campaign_budget(campaign)
        autosave()
        st.rerun()
    if buttons[1].button("仮の指標を取得", key=f"metrics_{group['id']}"):
        if not group.get("keywords"):
            group["keywords"] = generate_mock_keywords(group)
        fill_mock_metrics(group["keywords"])
        group["keywords_saved"] = False
        update_campaign_budget(campaign)
        autosave()
        st.rerun()
    if buttons[2].button("キーワードを追加", key=f"add_kw_{group['id']}"):
        group.setdefault("keywords", []).append({
            "id": new_id("kw"), "selected": True, "keyword": "", "match_type": "フレーズ一致",
            "copy_text": "", "monthly_searches": None, "competition": "未取得", "low_bid": None,
            "high_bid": None, "relevance": "高", "status": "未取得", "source": "手動追加",
        })
        group["keywords_saved"] = False
        update_campaign_budget(campaign)
        autosave()
        st.rerun()

    refresh_keyword_rows(group.setdefault("keywords", []))
    display_columns = ["id", "selected", "keyword", "copy_text", "match_type", "monthly_searches", "competition", "low_bid", "high_bid", "status", "source"]
    with st.form(key=f"kw_form_{group['id']}"):
        edited = st.data_editor(
            pd.DataFrame(group["keywords"], columns=display_columns),
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "id": None,
                "selected": st.column_config.CheckboxColumn("採用"),
                "keyword": st.column_config.TextColumn("キーワード", width="large"),
                "copy_text": st.column_config.TextColumn("コピー用", disabled=True, width="large"),
                "match_type": st.column_config.SelectboxColumn("マッチ", options=MATCH_TYPES, required=True),
                "monthly_searches": st.column_config.NumberColumn("月間検索数", min_value=0, step=10),
                "competition": st.column_config.SelectboxColumn("競合性", options=["未取得", "低", "中", "高"]),
                "low_bid": st.column_config.NumberColumn("低額帯", min_value=0, format="¥%d"),
                "high_bid": st.column_config.NumberColumn("高額帯", min_value=0, format="¥%d"),
                "status": st.column_config.TextColumn("判定", disabled=True),
                "source": st.column_config.TextColumn("出所", disabled=True),
            },
            key=f"kw_editor_{group['id']}",
        )
        apply_changes = st.form_submit_button("キーワード表の変更を保存", type="primary")
    if apply_changes:
        old_by_id = {row["id"]: row for row in group["keywords"]}
        updated = []
        for row in dataframe_records(edited):
            row_id = row.get("id") or new_id("kw")
            base = old_by_id.get(row_id, {})
            base.update(row)
            base["id"] = row_id
            base["relevance"] = base.get("relevance", "高")
            base["source"] = base.get("source") or "手動追加"
            updated.append(base)
        refresh_keyword_rows(updated)
        group["keywords"] = updated
        group["keywords_saved"] = True
        update_campaign_budget(campaign)
        autosave()
        st.success("キーワード表の変更を保存しました。")

    copy_lines = [row.get("copy_text", "") for row in group["keywords"] if row.get("selected") and row.get("copy_text")]
    st.markdown("**採用キーワードのコピー用列をまとめてコピー**")
    st.caption("下の枠の右上にあるコピーマークで、採用中のキーワードをまとめてコピーできます。")
    st.code("\n".join(copy_lines) if copy_lines else "採用中のキーワードはありません", language=None)


def step_three(campaign: dict):
    st.subheader("3 キーワード調査 予算 構成提案")
    groups = campaign.get("ad_groups", [])
    if not groups:
        st.warning("先に広告グループを作成してください。")
        return
    group_id = st.selectbox("広告グループ", [g["id"] for g in groups], format_func=lambda value: next(g["name"] for g in groups if g["id"] == value), key=f"kw_group_{campaign['id']}")
    group = next(g for g in groups if g["id"] == group_id)
    keyword_editor(campaign, group)

    all_keywords = [row for g in groups for row in g.get("keywords", [])]
    st.markdown("### 予算案")
    st.markdown("※ 各案で想定する1日あたりのクリック数を入力してください。入力値と全キーワードの入札指標から日予算を計算します。")
    click_defaults = {"抑制案": 5, "標準案": 10, "上部表示重視案": 15}
    campaign.setdefault("budget_target_clicks", click_defaults.copy())
    click_columns = st.columns(3)
    for column, name in zip(click_columns, click_defaults):
        campaign["budget_target_clicks"][name] = int(column.number_input(
            f"{name}の想定クリック数／日",
            min_value=1,
            step=1,
            value=int(campaign["budget_target_clicks"].get(name, click_defaults[name])),
            key=f"budget_clicks_{campaign['id']}_{name}",
        ))
    saved_count = sum(1 for item in groups if item.get("keywords_saved") and item.get("keywords"))
    budget_ready = keywords_complete_for_budget(campaign)
    st.caption(f"キーワード表の保存状況：{saved_count}/{len(groups)} 広告グループ")
    if budget_ready:
        scenarios = update_campaign_budget(campaign)
        st.success("全広告グループのキーワード表が保存済みです。予算案を採用できます。")
    else:
        scenarios = budget_scenarios(all_keywords, campaign["budget_target_clicks"])
        st.info("全広告グループのキーワード表を保存すると、予算案を採用できるようになります。")
    if scenarios:
        columns = st.columns(3)
        for column, scenario in zip(columns, scenarios):
            with column:
                is_selected = campaign.get("budget_choice") == scenario["name"]
                has_selected = any(campaign.get("budget_choice") == item["name"] for item in scenarios)
                card_class = "dimmed" if not budget_ready else ("selected" if is_selected else ("dimmed" if has_selected else ""))
                st.markdown(f"<div class='metric-card {card_class}'><b>{scenario['name']}</b><br>参考CPC ¥{scenario['reference_cpc']:,}<br>目標 {scenario['target_clicks']}クリック/日<br><b>日予算 ¥{scenario['daily_budget']:,.0f}</b><br>月額目安 ¥{scenario['monthly_budget']:,.0f}</div>", unsafe_allow_html=True)
                if st.button(
                    "全表保存後に利用可能" if not budget_ready else ("採用中" if is_selected else "この案を採用"),
                    key=f"budget_{campaign['id']}_{scenario['name']}",
                    disabled=(not budget_ready) or is_selected,
                    width="stretch",
                ):
                    campaign["daily_budget"] = scenario["daily_budget"]
                    campaign["budget_choice"] = scenario["name"]
                    autosave()
                    st.rerun()
        if budget_ready and any(campaign.get("budget_choice") == item["name"] for item in scenarios):
            st.success(f"採用中の予算案：{campaign['budget_choice']}（日予算 ¥{campaign['daily_budget']:,.0f}）")
    else:
        st.info("入札データを取得すると予算案のプレビューを表示します。")

    split_needed, split_reason = should_suggest_campaign_split(all_keywords)
    if budget_ready and split_needed:
        st.markdown(f'<div class="ai-note"><b>キャンペーン分割案</b><br>{split_reason}</div>', unsafe_allow_html=True)
        proposal = campaign_split_proposal(campaign)
        if proposal:
            proposal_rows = []
            for index, item in enumerate(proposal["keywords"]):
                proposal_rows.append({
                    "新キャンペーン名": proposal["campaign_name"] if index == 0 else "",
                    "ターゲット": item["target"],
                    "移動候補キーワード": format_keyword(item["keyword"], item["match_type"]),
                    "現在の広告グループ": item["group_name"],
                    "月間検索数": item["monthly_searches"],
                    "低額帯": item["low_bid"],
                    "高額帯": item["high_bid"],
                })
            st.caption(f"AI抽出基準：高額帯bidが概ね ¥{proposal['threshold']:,} 以上の採用キーワード")
            st.dataframe(
                pd.DataFrame(proposal_rows),
                width="stretch",
                hide_index=True,
                column_config={
                    "低額帯": st.column_config.NumberColumn(format="¥%d"),
                    "高額帯": st.column_config.NumberColumn(format="¥%d"),
                },
            )
        if proposal and st.button("高入札群を新しいキャンペーンとして追加", key=f"split_{campaign['id']}"):
            clone = clone_campaign_for_split(project(), campaign["id"], proposal)
            request_navigation(clone["id"], "1 基本情報")
            autosave()
            st.rerun()
    elif budget_ready:
        st.caption("構成判定: " + split_reason)
    else:
        st.caption("構成判定は、全広告グループのキーワード表を保存した後に行います。")

    if st.button("保存して広告文へ", type="primary", key=f"to_ads_{campaign['id']}"):
        autosave()
        set_campaign_step(campaign["id"], "4 広告文")
        st.rerun()


def recalc_ads(rows: list[dict], limit: int):
    for row in rows:
        text = row.get("text", "") or ""
        row["normal_count"] = len(text)
        row["google_count"] = google_char_count(text)
        row["limit"] = limit


def regenerate_ad_rows(
    group: dict,
    kind: str,
    limit: int,
    selected_only: bool,
    instruction: str = "",
) -> None:
    rows = group.get(kind, [])
    for index, row in enumerate(rows):
        if selected_only and not row.get("selected"):
            continue
        text = row.get("text", "")
        if instruction.strip():
            request = instruction.strip()
            if kind == "headlines":
                row["text"] = f"{request[:8]}｜{text}"
            else:
                row["text"] = f"{request[:24]}。{text}"
        elif "相談" in text:
            row["text"] = text.replace("相談", "お問い合わせ", 1)
        elif kind == "headlines":
            row["text"] = f"{text} 新提案{index + 1}"
        else:
            row["text"] = f"{text} 別の表現でご案内します。"
    recalc_ads(rows, limit)
    group[f"{kind}_saved"] = False


def ads_editor(group: dict, kind: str, limit: int, key_prefix: str):
    rows = group.setdefault(kind, [])
    recalc_ads(rows, limit)
    label = "見出し" if kind == "headlines" else "説明文"
    header = st.columns([1, 8, 1.2, 1.2, 1.2])
    header[0].caption("再生成")
    header[1].caption(label)
    header[2].caption("通常")
    header[3].caption("Google換算")
    header[4].caption("上限")
    for index, row in enumerate(rows, start=1):
        row.setdefault("id", new_id(key_prefix))
        with st.container(border=True):
            columns = st.columns([1, 8, 1.2, 1.2, 1.2])
            if row.get("google_count", 0) > limit:
                columns[1].markdown('<div class="ad-row-error-marker">⚠ 文字数が上限を超えています</div>', unsafe_allow_html=True)
            row["selected"] = columns[0].checkbox(
                "再生成対象",
                value=bool(row.get("selected", False)),
                key=f"{key_prefix}_selected_{row['id']}",
                label_visibility="collapsed",
            )
            previous_text = row.get("text", "")
            new_text = columns[1].text_input(
                f"{label}{index}",
                previous_text,
                key=f"{key_prefix}_text_{row['id']}",
                label_visibility="collapsed",
            )
            if new_text != previous_text:
                group[f"{kind}_saved"] = False
            row["text"] = new_text
            row["normal_count"] = len(row["text"])
            row["google_count"] = google_char_count(row["text"])
            row["limit"] = limit
            columns[2].markdown(str(row["normal_count"]))
            columns[3].markdown(f"**{row['google_count']}**")
            columns[4].markdown(str(limit))
    exceeded = [row for row in rows if row["google_count"] > limit]
    if exceeded:
        st.error(f"{label}で文字数上限を超えているものが{len(exceeded)}件あります。")

    instruction = st.text_area(
        f"{label}をどのように修正しますか？",
        key=f"instruction_{kind}_{group['id']}",
        placeholder="空欄の場合は、AIが別案を考えます。例：安心感を強める",
    )
    buttons = st.columns([1, 1, 1, 3])
    if buttons[0].button("選択分を再生成", key=f"regen_selected_{kind}_{group['id']}"):
        regenerate_ad_rows(group, kind, limit, selected_only=True, instruction=instruction)
        st.rerun()
    if buttons[1].button("すべて再生成", key=f"regen_all_{kind}_{group['id']}"):
        regenerate_ad_rows(group, kind, limit, selected_only=False, instruction=instruction)
        st.rerun()
    if buttons[2].button("保存", key=f"save_{kind}_{group['id']}"):
        group[f"{kind}_saved"] = True
        autosave()
        st.success(f"{label}を保存しました。")


def step_four(campaign: dict):
    st.subheader("4 広告文")
    groups = campaign.get("ad_groups", [])
    if not groups:
        st.warning("先に広告グループを作成してください。")
        return
    group_id = st.selectbox("広告グループ", [g["id"] for g in groups], format_func=lambda value: next(g["name"] for g in groups if g["id"] == value), key=f"ads_group_{campaign['id']}")
    group = next(g for g in groups if g["id"] == group_id)
    language = st.selectbox("広告文の言語", ["日本語", "英語", "マレー語", "中国語"], index=["日本語", "英語", "マレー語", "中国語"].index(campaign.get("language", "日本語")), key=f"ads_lang_{group['id']}")
    campaign["language"] = language
    if st.button("試作AIで広告文を生成", type="primary", key=f"gen_ads_{group['id']}"):
        generate_mock_ads(group, project().get("brand", "EQUINOX"))
        group["headlines_saved"] = False
        group["descriptions_saved"] = False
        autosave()
        st.rerun()

    if not group.get("headlines"):
        st.info("広告文を生成すると、見出し15件と説明文4件が表示されます。")
        return

    st.markdown("### 見出し15件")
    ads_editor(group, "headlines", 30, "headline_editor")
    st.markdown("### 説明文4件")
    ads_editor(group, "descriptions", 90, "description_editor")
    group["paths"] = [
        st.text_input("Path 1", group.get("paths", ["malaysia", "support"])[0], key=f"path1_{group['id']}"),
        st.text_input("Path 2", group.get("paths", ["malaysia", "support"])[1], key=f"path2_{group['id']}"),
    ]
    group["ctas"] = split_tags(st.text_input("CTA候補", tags_text(group.get("ctas", [])), key=f"ctas_{group['id']}"))
    if st.button("確認してファイルを作成", type="primary", key=f"review_{campaign['id']}"):
        autosave()
        st.session_state.review_mode = True
        st.rerun()


def render_campaign(campaign: dict):
    st.header(campaign["name"])
    steps = ["1 基本情報", "2 広告グループ", "3 キーワード", "4 広告文"]
    current = st.session_state.step_by_campaign.get(campaign["id"], steps[0])
    widget_key = f"step_widget_{campaign['id']}"
    if st.session_state.get(widget_key) != current:
        st.session_state[widget_key] = current
    step = st.radio(
        "作業ステップ",
        steps,
        horizontal=True,
        key=widget_key,
        on_change=step_changed,
        args=(campaign["id"],),
    )
    if step == steps[0]:
        step_one(campaign)
    elif step == steps[1]:
        step_two(campaign)
    elif step == steps[2]:
        step_three(campaign)
    else:
        step_four(campaign)
    autosave()


ensure_state()
apply_pending_navigation()
sidebar()
if st.session_state.review_mode:
    render_review()
else:
    top_navigation()
    if st.session_state.nav == "プロジェクト概要":
        render_project_overview()
    else:
        render_campaign(campaign_by_id(st.session_state.nav))
