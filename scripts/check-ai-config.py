import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = [
    root / "AGENTS.md",
    root / "CLAUDE.md",
    root / ".github" / "copilot-instructions.md",
]
missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
claude_text = (root / "CLAUDE.md").read_text() if (root / "CLAUDE.md").is_file() else ""

if missing:
    print(f"Missing AI configuration: {', '.join(missing)}")
    sys.exit(1)
if claude_text.strip() != "@AGENTS.md":
    print("CLAUDE.md must contain only @AGENTS.md")
    sys.exit(1)

print("AI configuration is synchronized.")
