"""Fail if a document quotes a test count or abuse-case count that is not the real one.

README, the agent guide, the landing page and llms.txt all say how many tests
there are, and several say how many abuse cases docs/security.md lists. This
collects the suite and counts the table rows, so the numbers cannot drift.
"""

import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
DOCUMENTS = ["README.md", "AGENTS.md", "llms.txt", "site/llms.txt", "site/index.html", "docs/architecture.md"]
# "525 tests", "525 passing tests", "tests-525-" in a badge URL, or a count in the page's proof block.
CLAIMS = [
    re.compile(r"\b(\d{2,5}) (?:passing )?tests\b"),
    re.compile(r"tests-(\d{2,5})-"),
    re.compile(r"Tests: (\d{2,5})\b"),
    re.compile(r'class="count">(\d{2,5})<'),
]

ABUSE_CLAIMS = [re.compile(r"\b(\d{1,3}) (?:documented )?abuse cases\b")]
ABUSE_DOCUMENTS = DOCUMENTS + ["CHANGELOG.md"]
abuse_rows = re.findall(r"^\| (\d+) \| \*\*", (root / "docs/security.md").read_text(encoding="utf-8"), re.MULTILINE)
abuse_actual = len(abuse_rows)
if [int(n) for n in abuse_rows] != list(range(1, abuse_actual + 1)):
    print("docs/security.md abuse cases are not numbered 1..N in order.")
    sys.exit(1)

collected = subprocess.run(
    [sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=root, capture_output=True, text=True
).stdout
match = re.search(r"^(\d+) tests? collected", collected, re.MULTILINE)
if not match:
    print("Could not count the tests:\n" + collected[-400:])
    sys.exit(1)
actual = int(match.group(1))

problems = []
for name in DOCUMENTS:
    text = (root / name).read_text(encoding="utf-8")
    for pattern in CLAIMS:
        for claim in pattern.finditer(text):
            if int(claim.group(1)) != actual:
                line = text.count("\n", 0, claim.start()) + 1
                problems.append(f"{name}:{line}: says {claim.group(1)}, the suite has {actual}")

for name in ABUSE_DOCUMENTS:
    text = (root / name).read_text(encoding="utf-8")
    for pattern in ABUSE_CLAIMS:
        for claim in pattern.finditer(text):
            if int(claim.group(1)) != abuse_actual:
                line = text.count("\n", 0, claim.start()) + 1
                problems.append(
                    f"{name}:{line}: says {claim.group(1)} abuse cases, docs/security.md lists {abuse_actual}"
                )

if problems:
    print("Counts in the docs are out of date:")
    print("\n".join(problems))
    sys.exit(1)

print(f"Docs agree with the suite: {actual} tests, {abuse_actual} abuse cases.")
