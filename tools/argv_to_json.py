"""
Print argv tokens as a JSON array for pasting into service_endpoints.json
(e.g. whisper_instances.*.launch.arguments).

  python tools/argv_to_json.py -- --model C:\\path\\to.bin --language en --vad

Omit "--" only when you have no flags before the script's own args.
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    if "--" in sys.argv:
        i = sys.argv.index("--")
        tokens = sys.argv[i + 1 :]
    else:
        tokens = sys.argv[1:]
    print(json.dumps(tokens, ensure_ascii=False))


if __name__ == "__main__":
    main()
