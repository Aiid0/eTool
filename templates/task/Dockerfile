# 按任务需要选择基础镜像，并同步调整 Python 版本和依赖锁文件。
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/cache \
    CUDA_CACHE_PATH=/tmp/cuda-cache

WORKDIR /app

# 系统依赖在这里安装；Python 依赖通过此任务的 pyproject.toml/uv.lock 安装。
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-cache

COPY src/ ./src/
COPY config/ ./config/

ENTRYPOINT ["/app/.venv/bin/python", "/app/src/main.py"]
CMD ["--config", "/app/config/task.json", "--output", "/outputs"]
