from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "node_modules", "dist", ".audit-cache", ".uv-cache"}
EXCLUDED_FILES = {
    ".env.example",
    "hmac-sha256-v1.json",
    "protocol-examples.json",
    "test_api_flow.py",
    "test_protocol.py",
    "secret_scan.py",
}
TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".sh",
    ".ps1",
}
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    "concrete_assignment": re.compile(
        r"(?i)\b(?:password|api[_-]?key|private[_-]?key)\b\s*[:=]\s*['\"][^'\"]{16,}['\"]"
    ),
}

findings: list[str] = []
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    if (
        any(part in EXCLUDED_PARTS for part in path.parts)
        or "tests" in path.parts
        or path.name in EXCLUDED_FILES
    ):
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{path.relative_to(ROOT)}:{line}: {name}")

report = ROOT / "release" / "secret-scan.txt"
if findings:
    report.write_text("\n".join(findings) + "\n", encoding="utf-8")
    raise SystemExit(f"potential committed secrets found: {len(findings)}")
report.write_text(
    "No known committed secrets detected by repository pattern scan.\n",
    encoding="utf-8",
)
print(report.read_text().strip())
