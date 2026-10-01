import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from docx import Document

from core import (
    MATCH_BROAD,
    MATCH_EXACT,
    MATCH_PHRASE,
    budget_scenarios,
    campaign_split_proposal,
    clone_campaign_for_split,
    empty_campaign,
    format_keyword,
    google_char_count,
    keyword_status,
    keywords_complete_for_budget,
    make_sample_project,
    sync_ad_groups_to_targets,
    update_campaign_budget,
)
from document_export import build_project_docx
import storage


class CoreTests(unittest.TestCase):
    def test_match_formatting(self):
        self.assertEqual(format_keyword("マレーシア 賃貸", MATCH_EXACT), "[マレーシア 賃貸]")
        self.assertEqual(format_keyword("マレーシア 賃貸", MATCH_PHRASE), '"マレーシア 賃貸"')
        self.assertEqual(format_keyword("マレーシア 賃貸", MATCH_BROAD), "マレーシア 賃貸")

    def test_google_count(self):
        self.assertEqual(google_char_count("KL賃貸"), 2 + 4)

    def test_status(self):
        self.assertEqual(keyword_status(0, "低"), "自動除外")
        self.assertEqual(keyword_status(20, "低"), "推奨")
        self.assertEqual(keyword_status(None, "未取得"), "未取得")

    def test_budget_scenarios(self):
        result = budget_scenarios([
            {"selected": True, "low_bid": 50, "high_bid": 200},
            {"selected": True, "low_bid": 100, "high_bid": 400},
        ])
        self.assertEqual(len(result), 3)
        self.assertLess(result[0]["daily_budget"], result[2]["daily_budget"])
        custom = budget_scenarios(
            [{"selected": True, "low_bid": 50, "high_bid": 200}],
            {"抑制案": 8, "標準案": 12, "上部表示重視案": 20},
        )
        self.assertEqual([item["target_clicks"] for item in custom], [8, 12, 20])

    def test_targets_create_one_group_each(self):
        campaign = empty_campaign()
        campaign["targets"] = ["賃貸希望者", "投資希望者", "教育移住希望者"]
        groups = sync_ad_groups_to_targets(campaign)
        self.assertEqual(len(groups), 3)
        self.assertEqual([group["target"] for group in groups], campaign["targets"])
        self.assertTrue(all(group["name"].endswith("層") for group in groups))

    def test_budget_unlocks_only_after_every_keyword_table_is_saved(self):
        project = make_sample_project()
        campaign = project["campaigns"][0]
        campaign["ad_groups"][0]["keywords_saved"] = False
        self.assertFalse(keywords_complete_for_budget(campaign))
        self.assertEqual(update_campaign_budget(campaign), [])
        campaign["ad_groups"][0]["keywords_saved"] = True
        scenarios = update_campaign_budget(campaign)
        self.assertTrue(keywords_complete_for_budget(campaign))
        self.assertEqual(len(scenarios), 3)

    def test_split_campaign_starts_from_basic_information(self):
        project = make_sample_project()
        source = project["campaigns"][0]
        source["negative_keywords"] = ["求人"]
        proposal = campaign_split_proposal(source)
        self.assertIsNotNone(proposal)
        self.assertTrue(proposal["keywords"])
        clone = clone_campaign_for_split(project, source["id"], proposal)
        self.assertEqual(clone["objective"], source["objective"])
        self.assertEqual(clone["negative_keywords"], ["求人"])
        self.assertEqual(clone["target_mode"], "LPからAIが提案")
        self.assertEqual(clone["targets"], proposal["targets"])
        self.assertEqual(
            [row["text"] for row in clone["ai_target_rows"]],
            proposal["targets"],
        )
        self.assertEqual(clone["excluded_targets"], [])
        self.assertEqual(clone["ad_groups"], [])

    def test_save_load_and_docx(self):
        project = make_sample_project()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(storage, "DATA_DIR", Path(temp_dir)):
                path = storage.save_project(project)
                loaded = storage.load_project(path)
                self.assertEqual(loaded["name"], project["name"])
        docx_bytes = build_project_docx(project)
        self.assertTrue(docx_bytes.startswith(b"PK"))
        self.assertGreater(len(docx_bytes), 10_000)
        doc = Document(BytesIO(docx_bytes))
        document_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
        self.assertNotIn("上司確認", document_text)


if __name__ == "__main__":
    unittest.main()
