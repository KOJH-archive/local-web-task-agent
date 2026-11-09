from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import TaskRunner
from .config import load_settings
from .database import AgentDatabase
from .ego import EgoBrowser
from .ollama import OllamaClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local ego-browser web task agent")
    parser.add_argument("command", nargs="?", help="Natural-language web task")
    parser.add_argument("--project-dir", default=str(Path.cwd()))
    parser.add_argument("--allow-domain", action="append", default=[])
    parser.add_argument("--write", action="store_true", help="Relax read-only mode; approvals still apply")
    parser.add_argument("--diagnostics", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args.project_dir)
    if args.diagnostics:
        result = {
            "ollama": OllamaClient(settings.ollama_url).diagnostics(),
            "ego_browser": EgoBrowser(settings.runtime_dir).check_installation(),
            "database": str(AgentDatabase(settings.db_path).path),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.command:
        raise SystemExit("Provide a command or use --diagnostics")
    runner = TaskRunner(settings)
    task_id = runner.create_task(args.command, read_only=not args.write, allowed_domains=args.allow_domain)
    result = runner.run(task_id)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

