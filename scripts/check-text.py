"""Fail if tracked text files contain em dashes or emoji.

Project rule: committed prose and code use plain punctuation. Run from the
repository root; it checks the files Git tracks plus any new ones it would add.
"""

import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".html", ".css", ".js", ".json", ".toml", ".yml", ".yaml", ".svg", ".sh", ".example",
}
# Em dash, plus the main emoji blocks and the emoji variation selector.
FORBIDDEN = re.compile(r"[\u2014\U0001f300-\U0001faff\u2600-\u27bf\ufe0f]")

listed = subprocess.run(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
    cwd=root,
    capture_output=True,
    text=True,
    check=True,
).stdout.splitlines()

problems = []
for name in listed:
    path = root / name
    if path.suffix not in TEXT_SUFFIXES and path.name not in {"CODEOWNERS", ".editorconfig", ".gitattributes"}:
        continue
    if not path.is_file():
        continue
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if FORBIDDEN.search(line):
            problems.append(f"{name}:{number}: {line.strip()[:80]}")

if problems:
    print("Em dashes or emoji found:")
    print("\n".join(problems))
    sys.exit(1)

print("No em dashes or emoji in tracked text.")
