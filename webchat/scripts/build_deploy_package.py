"""H1.1: builds a reproducible zip containing ONLY what the App Service
runtime needs to run `webchat/` - never tests, never the MCP App Host's own
TypeScript source/node_modules/Puppeteer/Chrome (all under `host-src/`,
already-built into `static/vendor/mcp-app-host.bundle.js`, which IS
included), and never a local secret or user-data file that happens to
exist on this machine (`scope_private_key.pem`, `conversations.json`, any
`.env`).

This does not deploy anything - it only produces a zip on disk, for later
use with `az webapp deploy --type zip` (see docs/system-manifest.md section
12, "H1/H1.1 - Azure App Service hosting for webchat"). Run from anywhere;
paths are resolved relative to this file.

Usage:
    python scripts/build_deploy_package.py [output_zip_path]
    (defaults to ./ask-ariel-web-deploy.zip in the current directory)
"""

import sys
import zipfile
from pathlib import Path

WEBCHAT_ROOT = Path(__file__).resolve().parent.parent

# Exact files/directories to include - an explicit allowlist, not "everything
# except an exclude list", so a new file added to webchat/ in the future is
# NOT silently shipped to production until someone deliberately adds it here.
INCLUDE_FILES = [
    "server.py",
    "mcp_app.py",
    "agent_contract.py",
    "presentation_validator.py",
    "actions_registry.py",
    "requirements.txt",
]
INCLUDE_DIRS = [
    "templates",
    "static",  # includes static/vendor/mcp-app-host.bundle.js and static/vendor/echarts.min.js - both already-built artifacts, not source
]

# Defense in depth on top of the allowlist above: even if one of INCLUDE_DIRS
# ever grows a file that shouldn't ship, these are never included.
EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", "node_modules"}
EXCLUDED_SUFFIXES = {".pyc", ".pem"}
EXCLUDED_EXACT_FILENAMES = {"conversations.json", ".env"}


def _should_skip(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES:
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    if path.name in EXCLUDED_EXACT_FILENAMES or path.name.startswith(".env"):
        return True
    return False


def build(output_path: Path) -> None:
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in INCLUDE_FILES:
            src = WEBCHAT_ROOT / filename
            if not src.exists():
                raise FileNotFoundError(f"Expected runtime file missing: {src}")
            zf.write(src, arcname=filename)

        for dirname in INCLUDE_DIRS:
            root = WEBCHAT_ROOT / dirname
            if not root.exists():
                raise FileNotFoundError(f"Expected runtime directory missing: {root}")
            for path in sorted(root.rglob("*")):
                if path.is_dir():
                    continue
                if any(_should_skip(parent) for parent in path.relative_to(WEBCHAT_ROOT).parents) or _should_skip(path):
                    continue
                zf.write(path, arcname=str(path.relative_to(WEBCHAT_ROOT)))

    print(f"Wrote {output_path} ({output_path.stat().st_size} bytes)")


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / "ask-ariel-web-deploy.zip"
    build(output_path)


if __name__ == "__main__":
    main()
