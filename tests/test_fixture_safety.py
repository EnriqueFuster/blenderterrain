"""Reject common secret-bearing content from committed contract fixtures."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
FORBIDDEN_PATTERNS = {
    "authorization header": re.compile(rb"(?im)^\s*authorization\s*:"),
    "cookie header": re.compile(rb"(?im)^\s*cookie\s*:"),
    "set-cookie header": re.compile(rb"(?im)^\s*set-cookie\s*:"),
    "Windows user path": re.compile(rb"(?i)[a-z]:\\users\\[^\\\r\n]+"),
}


class FixtureSafetyTests(unittest.TestCase):
    def test_fixtures_do_not_contain_common_sensitive_fields(self) -> None:
        violations: list[str] = []
        for path in FIXTURE_ROOT.rglob("*"):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            content = path.read_bytes()
            for description, pattern in FORBIDDEN_PATTERNS.items():
                if pattern.search(content):
                    violations.append(f"{path.relative_to(FIXTURE_ROOT)}: {description}")

        self.assertEqual(violations, [], "Unsafe fixture content:\n" + "\n".join(violations))

    def test_contract_fixtures_have_matching_provenance_digest(self) -> None:
        violations: list[str] = []
        contract_fixtures = (
            path
            for path in FIXTURE_ROOT.rglob("*")
            if path.suffix in {".html", ".json"}
            and not path.name.endswith(".provenance.json")
        )
        for path in contract_fixtures:
            provenance_path = path.with_suffix(".provenance.json")
            if not provenance_path.is_file():
                violations.append(f"{path.relative_to(FIXTURE_ROOT)}: missing provenance")
                continue
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if provenance.get("sanitized_sha256", "").lower() != actual_digest:
                violations.append(f"{path.relative_to(FIXTURE_ROOT)}: digest mismatch")

        self.assertEqual(violations, [], "Invalid fixture provenance:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
