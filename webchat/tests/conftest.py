"""webchat/'s modules import each other as top-level names (`from
agent_contract import ...`), same convention as mcp/ - this makes that
resolve under pytest too."""

import sys
from pathlib import Path

_WEBCHAT_ROOT = Path(__file__).resolve().parent.parent
if str(_WEBCHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(_WEBCHAT_ROOT))
