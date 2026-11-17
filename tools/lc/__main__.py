from __future__ import annotations

import json
import sys
import time

from .client import Client


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"setup", "remove", "doctor"}:
        from .integration import main as native_main

        return native_main(sys.argv[1:])
    if len(sys.argv) < 2 or sys.argv[1] not in {
        "connect",
        "help",
        "read",
        "edit",
        "capture",
        "lgg_card",
        "lgg_draft",
        "lgg_run",
    }:
        print('Usage: python -m lc {connect|help|read|edit|capture} "command"', file=sys.stderr)
        return 2
    tool, command = sys.argv[1], " ".join(sys.argv[2:])
    started = time.perf_counter()
    try:
        if tool.startswith("lgg_"):
            from lgg.service import DesignService

            result = DesignService(Client()).invoke(tool, command)
        else:
            result = getattr(Client(), tool)(command)
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
        status = result.get("status", "ok") if isinstance(result, dict) else "ok"
        return 1 if status in {"rejected", "failed"} else 0
    except (ValueError, ConnectionError, TimeoutError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        tool_name = tool if tool.startswith("lgg_") else f"lc.{tool}"
        print(f"{tool_name} {command} | {time.perf_counter() - started:.3f} s", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
