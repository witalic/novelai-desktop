#!/usr/bin/env python3
"""PreToolUse(Bash) guard — blocks commands that would leak secrets or commit user data (exit 2 = block,
message to the user; exit 0 = allow). Stdlib only, so any Python runs it. Tuned for LOW false-positives: it
trips only on clearly-forbidden patterns at command position — the rules + code-reviewer catch the rest."""
import json
import re
import sys

# A command starts at the string start or right after a shell separator, so `grep cat`,
# `echo type`, or `git log --grep ".env"` are NOT mistaken for running those tools.
_START = r"(?:^|[|&;]+)\s*"


def _block(msg: str) -> None:
    print(f"[guard] {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        cmd = (json.load(sys.stdin).get("tool_input") or {}).get("command", "")
    except Exception:
        sys.exit(0)  # unparseable hook input -> never block
    if not isinstance(cmd, str) or not cmd.strip():
        sys.exit(0)
    c = cmd.lower()

    # 1. Secrets are never read into the open or committed. (rules/security.md)
    if re.search(_START + r"(git\s+add|cat|type|less|more|bat)\b[^|&;]*?\.env\b(?!\.example)", c):
        _block("Secrets in .env are never echoed or committed (only .env.example is tracked). (rules/security.md)")

    # 2. Never commit the vault, generated images, or build output — that's the user's private data. (rules/security.md)
    if re.search(_START + r"git\s+add\b[^|&;]*?\b(vault|generated|dist|node_modules)\b", c):
        _block("The vault, generated images, and build output are never committed. (rules/security.md, rules/git.md)")

    sys.exit(0)


if __name__ == "__main__":
    main()
