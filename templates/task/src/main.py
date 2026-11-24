"""读取任务配置，调用业务入口，并保存返回结果。"""

import argparse
import json
from pathlib import Path

from task import run_task


def main():
    parser = argparse.ArgumentParser(description="Run the configured task")
    parser.add_argument(
        "--config", type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "task.json",
    )
    parser.add_argument("--output", type=Path, default=Path("/outputs"))
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Task configuration must be a JSON object")

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Task configuration: {args.config.resolve()}", flush=True)
    print(f"Output directory: {output_dir}", flush=True)

    result = run_task(config, output_dir)
    if not isinstance(result, dict):
        raise TypeError("run_task must return a JSON-serializable dictionary")
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (output_dir / "result.json").write_text(serialized + "\n", encoding="utf-8")
    print(serialized, flush=True)


if __name__ == "__main__":
    main()
