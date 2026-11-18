"""Analyzer context and comment formatting tests; never call the GPT API."""
from __future__ import annotations

import unittest

from agent.analyzer import (
    analysis_comment_markdown,
    extract_issue_evidence,
    issue_body_context,
)
from agent.service import Service


class AnalyzerContextTests(unittest.TestCase):
    def test_long_issue_context_keeps_start_and_log_tail(self):
        body = "docker compose up -d\n" + ("middle text " * 1000) + "\nError: port is already allocated"
        context = issue_body_context({"body": body}, limit=1200)
        self.assertIn("docker compose up -d", context)
        self.assertIn("port is already allocated", context)
        self.assertIn("중간 내용 생략", context)
        self.assertLessEqual(len(context), 1200)

    def test_common_runtime_error_logs_are_preserved_as_evidence(self):
        body = (
            "명령어:\ndocker compose up -d\n\n"
            "오류:\nError response from daemon:\nport is already allocated"
        )
        evidence = extract_issue_evidence(body)
        self.assertEqual(len(evidence), 1)
        self.assertIn("docker compose up -d", evidence[0])
        self.assertIn("port is already allocated", evidence[0])

    def test_runtime_log_families_are_recognized_without_external_calls(self):
        samples = [
            "Traceback (most recent call last):\nValueError: bad input",
            "Flask returned Internal Server Error",
            "sqlite3.OperationalError: database is locked",
            "Error response from daemon: port is already allocated",
            "Pod api-123: CrashLoopBackOff",
            "terraform apply failed\nError: Invalid provider",
            "GitHub Actions workflow failed with exit code 1",
        ]
        for sample in samples:
            with self.subTest(sample=sample[:40]):
                self.assertTrue(extract_issue_evidence(sample))
                self.assertIn(sample.splitlines()[0], issue_body_context({"body": sample}))

    def test_fenced_traceback_and_long_logs_are_bounded(self):
        body = (
            "Flask에서 500 응답\n```text\nTraceback (most recent call last):\n"
            + ("database connection failed\n" * 300)
            + "sqlite3.OperationalError: database is locked\n```"
        )
        evidence = extract_issue_evidence(body)
        self.assertEqual(len(evidence), 1)
        self.assertLessEqual(len(evidence[0]), 1600)
        self.assertIn("Traceback", evidence[0])

    def test_comment_preview_is_markdown_and_matches_posted_comment(self):
        analysis = {
            "category": "bug",
            "priority": "high",
            "area": "컨테이너 실행",
            "summary": "포트 충돌로 컨테이너가 시작되지 않을 수 있습니다.",
            "probable_cause": "다른 프로세스가 포트를 점유했을 가능성이 있습니다.",
            "suggestions": ["포트 점유 프로세스를 확인", "Compose 포트 설정을 점검"],
            "solution": "포트 사용 상황을 확인하고 설정을 조정합니다.",
            "evidence": ["docker compose up -d\nError: port is already allocated"],
            "source": "gpt",
        }
        markdown = analysis_comment_markdown(analysis)
        self.assertIn("## IssueFlow AI 분석 결과", markdown)
        self.assertIn("**추정 원인:**", markdown)
        self.assertIn("1. 포트 점유 프로세스를 확인", markdown)
        self.assertIn("```text", markdown)
        comment = Service.comment({**analysis, "comment_preview": markdown})
        self.assertTrue(comment.endswith(markdown))
        self.assertIn("<!-- issueflow-ai:analysis:v1 ", comment)


if __name__ == "__main__":
    unittest.main()
