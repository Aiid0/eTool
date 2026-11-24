"""Command line interface for durable Docker experiment jobs."""

import argparse
import json
import sys
import time

from . import __version__
from .config import ConfigError, load_config


def parser():
    result = argparse.ArgumentParser(prog="etool", description="Package and run tasks on the configured GPU server.")
    result.add_argument("--version", action="version", version=f"etool {__version__}")
    result.add_argument("--config", help="Server configuration JSON (default: etool.json)")
    result.add_argument("--json", action="store_true", help="Emit the final result as JSON; send progress to stderr")
    commands = result.add_subparsers(dest="action", required=True)

    def add(name, help_text):
        child = commands.add_parser(name, help=help_text)
        child.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return child

    add("doctor", "Check local tools, SSH, Docker and the assigned GPU")
    add("list", "List locally recorded tasks")
    for action, description in (("pack", "Build and archive a task locally"),
                                ("run", "Build, submit, wait, fetch, verify and clean a task")):
        child = add(action, description)
        child.add_argument("project", help="Task configuration JSON, or a directory containing experiment.json")
        if action == "run":
            child.add_argument("--timeout", type=positive_float, help="Maximum local wait time in seconds; remote work continues on timeout")
    for action, description in (("submit", "Submit an already packed job and return immediately"),
                                ("wait", "Wait for a remote container to finish"),
                                ("fetch", "Retrieve and verify outputs and original remote logs"),
                                ("clean", "Delete a verified job's remote resources"),
                                ("cancel", "Stop this job while preserving its results and logs"),
                                ("discard", "Delete this stopped job's uncollected resources"),
                                ("resume", "Continue an interrupted job through retrieval and cleanup")):
        child = add(action, description)
        child.add_argument("job_id")
        if action in ("wait", "resume"):
            child.add_argument("--timeout", type=positive_float, help="Maximum local wait time in seconds")
        if action == "fetch":
            child.add_argument("--cleanup", action="store_true", help="Clean remote resources after successful verification")
    child = add("status", "Inspect one job, or show local job records")
    child.add_argument("job_id", nargs="?")
    child = add("logs", "Read original timestamped Docker logs")
    child.add_argument("job_id")
    child.add_argument("--tail", type=nonnegative_int, default=200, help="Initial number of lines (default: 200)")
    child.add_argument("--follow", "-f", action="store_true", help="Continue reading until the container exits")
    return result


def positive_float(value):
    import math
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def _exit_code(result):
    value = result.get("exit_code", 0) if isinstance(result, dict) else 0
    if value is None:
        return 0
    value = int(value)
    return min(value if value >= 0 else 128 - value, 255)


def _stopped(result):
    state = result.get("container_state")
    if isinstance(state, dict):
        state = state.get("Status") or state.get("status")
    return state in ("exited", "dead", "removed") or result.get("state") in ("exited", "collected", "verified", "cleaned")


def _follow(client, job_id, tail):
    previous = ""
    first = True
    while True:
        result = client.logs(job_id, tail=-1)
        text = result.get("text", "")
        if first:
            lines = text.splitlines(keepends=True)
            delta = "".join(lines[-tail:]) if tail else ""
            first = False
        elif text.startswith(previous):
            delta = text[len(previous):]
        else:
            # Preserve continuity if a daemon returns a shorter retained log.
            old_lines, new_lines = previous.splitlines(keepends=True), text.splitlines(keepends=True)
            overlap = 0
            for length in range(min(len(old_lines), len(new_lines)), 0, -1):
                if old_lines[-length:] == new_lines[:length]:
                    overlap = length
                    break
            delta = "".join(new_lines[overlap:])
        if delta:
            print(delta, end="", flush=True)
        previous = text
        if _stopped(result):
            return result
        time.sleep(1)


def _display(result, action, as_json):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif action == "logs":
        print(result.get("text", ""), end="", flush=True)
    elif isinstance(result, list):
        if not result:
            print("No recorded jobs.")
        for job in result:
            identity = job.get("id", job.get("job_id", "?"))
            state = job.get("state", job.get("status", "?"))
            project = job.get("project")
            fallback = project.get("name", "") if isinstance(project, dict) else ""
            name = job.get("name", fallback)
            print(f"{identity}  {state}  {name}")
    elif isinstance(result, dict) and ("id" in result or "job_id" in result):
        print(f"任务: {result.get('id', result.get('job_id'))}")
        if result.get("name"):
            print(f"实验: {result['name']}")
        print(f"状态: {result.get('state', result.get('status', '?'))}")
        if result.get("exit_code") is not None:
            print(f"实验退出码: {result['exit_code']}")
        if result.get("archive_bytes") is not None:
            print(f"镜像压缩包: {result['archive_bytes'] / 1024**2:.1f} MiB")
        gpu = result.get("gpu")
        if isinstance(gpu, dict):
            index = gpu.get("physical_index", gpu.get("host_gpu_index", "?"))
            print(f"远端物理 GPU: {index} / {gpu.get('name', gpu.get('device_name', ''))}")
            print(f"GPU UUID: {gpu.get('uuid', gpu.get('gpu_uuid', '?'))}")
        if result.get("local_dir"):
            print(f"本地目录: {result['local_dir']}")
        if result.get("verified_file_count") is not None:
            print(f"已校验回收文件: {result['verified_file_count']}")
        cleanup = result.get("cleanup")
        if isinstance(cleanup, dict):
            fields = (("容器", "container_absent"), ("镜像标签", "image_tag_absent"),
                      ("镜像 ID", "image_id_absent"), ("远端目录", "task_directory_absent"))
            print("清理检查: " + "; ".join(
                f"{label}={'已移除' if cleanup[key] else '仍存在'}" for label, key in fields if key in cleanup))
            for field, label in (("image_id_set_restored", "镜像 ID 集合"),
                                 ("container_id_set_restored", "容器 ID 集合")):
                if field in cleanup:
                    print(f"{label}: {'与执行前一致' if cleanup[field] else '有变化，详见 cleanup.json'}")
        if result.get("last_error"):
            error = result["last_error"]
            print(f"最近错误: {error.get('message', error) if isinstance(error, dict) else error}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.action == "logs" and args.follow and args.json:
        arguments.error("logs --follow streams text; use logs --json for a JSON snapshot")
    from .client import Client, ToolError
    from .transport import TransportError

    def progress(*items):
        print(*items, file=sys.stderr if args.json else sys.stdout, flush=True)

    try:
        client = Client(load_config(args.config), progress=progress)
        action = args.action
        if action == "doctor":
            result = client.doctor()
        elif action == "list" or (action == "status" and args.job_id is None):
            result = client.list_jobs()
        elif action == "pack":
            result = client.pack(args.project)
        elif action == "run":
            result = client.run(args.project, timeout=args.timeout)
        elif action == "resume":
            result = client.resume(args.job_id, timeout=args.timeout)
        elif action == "wait":
            result = client.wait(args.job_id, timeout=args.timeout)
        elif action == "fetch":
            result = client.fetch(args.job_id, cleanup=args.cleanup)
        elif action == "logs":
            if args.follow:
                _follow(client, args.job_id, args.tail)
                return 0
            result = client.logs(args.job_id, tail=args.tail)
        else:
            method = {"submit": client.submit, "status": client.status, "clean": client.clean,
                      "cancel": client.cancel, "discard": client.discard}[action]
            result = method(args.job_id)
        _display(result, action, args.json)
        return _exit_code(result) if action in ("run", "resume", "wait") else 0
    except (ConfigError, ToolError, TransportError) as error:
        if args.json:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        else:
            print(f"etool: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\netool: interrupted; remote work is preserved. Use status and resume with the job ID.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
