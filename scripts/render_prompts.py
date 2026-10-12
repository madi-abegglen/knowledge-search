"""Print a hash of the rendered system prompt (exactly what answer.py sends) per prompt version.

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\render_prompts.py [outfile_prefix]
With a prefix, the rendered text of each version is also written to <prefix>_v<N>.txt so two runs
can be diffed byte for byte. Used to prove that front-matter-only edits do not change the prompt.
"""

import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.answer as A  # noqa: E402


def main(argv: list[str]) -> int:
    prefix = argv[0] if argv else None
    for version in ("1", "2"):
        os.environ["PROMPT_VERSION"] = version
        rendered = A._load_prompt()
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        print(f"prompt v{version}: {len(rendered.encode('utf-8'))} bytes, sha256 {digest}")
        if prefix:
            Path(f"{prefix}_v{version}.txt").write_bytes(rendered.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
