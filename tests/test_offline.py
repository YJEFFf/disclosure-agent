"""공개본 회귀 시험: 합성 데이터만 사용하고 외부 네트워크 호출을 차단한다."""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class OfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="gongsi-public-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        raw, meta = root / "01_공시원문", root / "02_구조화"
        raw.mkdir()
        meta.mkdir()
        # 실제 기업·공시 자료를 복제하지 않는 최소 합성 메타데이터.
        (meta / "manifest.jsonl").write_text(
            json.dumps({"corp_name": "예제기업", "rcept_no": "DEMO-001"}) + "\n",
            encoding="utf-8",
        )
        (meta / "universe.csv").write_text(
            "corp_name,listed_name,corp_eng_name,stock_code,corp_code\n"
            "예제기업,예제기업,Example Corp,DEMO,DEMO\n", encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GONGSI_") and k != "CLOVASTUDIO_API_KEY"}
        env.update(GONGSI_DATA_ROOT=str(root), ANSWER_STATE_DIR=str(root / "state"))
        cls.environment = patch.dict(os.environ, env, clear=True)
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        for target in ("socket.socket.connect", "requests.sessions.Session.request"):
            blocker = patch(target, side_effect=AssertionError("오프라인 시험: 네트워크 금지"))
            blocker.start()
            cls.addClassCleanup(blocker.stop)
        sys.path.insert(0, str(ROOT / "src" / "gongsi" / "api"))
        cls.addClassCleanup(sys.path.remove, str(ROOT / "src" / "gongsi" / "api"))
        cls.server = importlib.import_module("server")
        cls.answerer = cls.server.answerer
        cls.loader = cls.server.loader
        cls.addClassCleanup(cls.server._pool.shutdown, wait=True)

    def setUp(self):
        self.case_tmp = tempfile.TemporaryDirectory(dir=self.tmp.name)
        self.addCleanup(self.case_tmp.cleanup)
        root = Path(self.case_tmp.name)
        for target, key, value in (
            (self.server, "STATE_DIR", root / "state"),
            (self.loader, "DERIVED_DIR", root / "stores"),
        ):
            override = patch.object(target, key, value)
            override.start()
            self.addCleanup(override.stop)
        self.server._mem.clear()

    @staticmethod
    def payload(question="예제 질문", question_id="DEMO-001"):
        return {"question_id": question_id, "question": question,
                "retrieved_context": "합성 근거", "think_trace": "모의 처리",
                "answer": "예제 답변"}

    def test_amount_conversion(self):
        for number, expected in ((4_852_500_000_000, "4조 8,525억원"),
                                 (-2_045_300_000_000, "-2조 453억원"),
                                 (0, "0원")):
            with self.subTest(number=number):
                self.assertEqual(self.answerer.kr_amount(number), expected)

    def test_ungrounded_number_detection(self):
        context = "계약 금액은 4조 8,525억원입니다."
        self.assertFalse(self.answerer.ungrounded_numbers("4조 8,525억원", context))
        self.assertTrue(self.answerer.ungrounded_numbers("48조 8,250억원", context))

    def test_largest_shareholder_is_not_superlative(self):
        self.assertIsNone(self.answerer._SUPERLATIVE_Q.search("예제기업 최대주주 지분율은?"))
        self.assertIsNotNone(self.answerer._SUPERLATIVE_Q.search("최대 계약 금액은?"))

    def test_unsupported_url_removed_and_supported_url_preserved(self):
        result = self.payload()
        result["retrieved_context"] = "공시 원문 https://example.org/disclosure"
        result["answer"] = (
            "[원문](https://example.org/disclosure) "
            "[추가 정보](https://unsupported.invalid/report)"
        )
        with patch.object(self.answerer, "_answer_impl", return_value=result):
            answer = self.answerer.answer("예제기업 공시를 설명해 줘")["answer"]
        self.assertIn("https://example.org/disclosure", answer)
        self.assertNotIn("https://unsupported.invalid/report", answer)
        self.assertNotIn("]()", answer)

    def test_response_schema(self):
        with patch.object(self.answerer, "answer", side_effect=self.payload):
            response = self.server.get_answer(question="예제 질문", question_id="DEMO-001")
        data = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(data), set(self.payload()))
        self.assertTrue(all(isinstance(value, str) for value in data.values()))

    def test_cache_survives_memory_reset(self):
        with patch.object(self.answerer, "answer", side_effect=self.payload) as call:
            first = self.server.get_answer(question="예제 질문", question_id="CACHE-001")
            self.server._mem.clear()  # 디스크에서 재적재하는 경로 확인
            second = self.server.get_answer(question="예제 질문", question_id="CACHE-001")
        self.assertEqual(first.body, second.body)
        self.assertEqual(call.call_count, 1)

    def test_failure_is_not_cached_and_retry_recovers(self):
        with patch.object(self.answerer, "answer", side_effect=RuntimeError("모의 오류")):
            failed = self.server.get_answer(question="예제 질문", question_id="RETRY-001")
        self.assertEqual(failed.status_code, 200)
        self.assertIn("내부 오류", json.loads(failed.body)["answer"])
        self.assertFalse(self.server._state_path("RETRY-001").exists())
        with patch.object(self.answerer, "answer", side_effect=self.payload) as call:
            recovered = self.server.get_answer(question="예제 질문", question_id="RETRY-001")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(json.loads(recovered.body)["answer"], "예제 답변")

    def test_empty_question_does_not_call_answerer(self):
        with patch.object(self.answerer, "answer") as call:
            response = self.server.get_answer(question="  ", question_id="EMPTY-001")
        call.assert_not_called()
        self.assertIn("비어", json.loads(response.body)["answer"])

    def test_health_detects_missing_store(self):
        stores = self.loader.DERIVED_DIR
        (stores / "db").mkdir(parents=True)
        for filename, table in (("event.sqlite", "events"), ("section.sqlite", "sections")):
            with sqlite3.connect(stores / "db" / filename) as connection:
                connection.execute(f"CREATE TABLE {table} (id INTEGER)")
                connection.execute(f"INSERT INTO {table} VALUES (1)")
        for relative in self.loader.STORE_FILES:
            if relative.endswith(".jsonl"):
                (stores / relative).write_text('{"synthetic": true}\n', encoding="utf-8")
        self.assertEqual(json.loads(self.server.health().body)["status"], "ok")
        (stores / "db" / "ordinals.jsonl").unlink()
        self.assertEqual(json.loads(self.server.health().body)["status"], "degraded")

    def test_empty_data_reports_degraded(self):
        self.assertEqual(json.loads(self.server.health().body)["status"], "degraded")
        self.assertEqual(json.loads(self.server.selfcheck().body)["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
