"""Set the public GitHub repository embedded in future Windows EXEs."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from valorant_translator.updates.github import validate_repository

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = ROOT / "config" / "update_channel.json"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：python -m scripts.configure_updates https://github.com/用户名/仓库名")
    value = sys.argv[1].strip().rstrip("/")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.query
            or parsed.fragment or parsed.username or parsed.password or parsed.port):
        raise SystemExit("只接受公开 GitHub 仓库的 HTTPS 地址。")
    repo = parsed.path.strip("/").removesuffix(".git")
    repo = validate_repository(repo)
    CHANNEL.write_text(json.dumps({"github_repository": repo}, indent=2) + "\n", encoding="utf-8")
    print(f"Future Windows builds will check GitHub Releases in {repo}.")


if __name__ == "__main__":
    main()
