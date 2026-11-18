"""GitHub Actions에서 새 이슈가 생기면 실행되는 파일."""
from __future__ import annotations

import os
import sys

from .config import get_config
from .service import Service


def main() -> int:
    cfg = get_config()
    if not cfg.github_ready or not cfg.ai_ready:
        print("ERROR: GITHUB_REPO, GITHUB_TOKEN, OPENAI_API_KEY를 확인하세요.", file=sys.stderr)
        return 2
    try:
        number = int(os.environ.get("ISSUE_NUMBER", ""))
        if number < 1:
            raise ValueError
    except ValueError:
        print("ERROR: ISSUE_NUMBER는 1 이상의 숫자여야 합니다.", file=sys.stderr)
        return 2
    try:
        result = Service(cfg).run(number)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"Issue #{number}: {'already processed' if result['skipped'] else 'analysis complete (read-only)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
