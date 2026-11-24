"""在 run_task 中实现任务逻辑，依赖通过任务的 pyproject.toml 声明。"""

from pathlib import Path


def run_task(config: dict, output_dir: Path) -> dict:
    """读取 config 中的业务参数，将产物写入 output_dir，并返回结果摘要。"""
    return {"status": "template_ready"}
