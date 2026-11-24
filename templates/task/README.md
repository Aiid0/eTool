# 官方任务模板

复制本目录建立自己的任务，按下面的位置修改代码、配置和运行环境。以下命令均从 eTool 根目录执行。

## 创建任务目录

将 `my-task` 替换为新的任务目录名：

```bash
mkdir -p tasks
cp -R templates/task tasks/my-task
```

任务也可以放在其他项目目录，提交时把配置路径指向那个目录即可。日常修改复制后的任务目录。

```text
tasks/my-task/
├── src/
│   ├── main.py          # 参数解析、配置读取、日志和结果写入
│   └── task.py          # 在 run_task 中实现业务逻辑
├── config/task.json     # 业务参数
├── experiment.json      # eTool 执行参数、资源和本地检查
├── Dockerfile           # 镜像基础环境和系统依赖
├── pyproject.toml       # 任务 Python 依赖
├── uv.lock              # 任务依赖锁文件
├── .python-version      # 本地任务解释器版本
├── .dockerignore        # 进入镜像构建上下文的文件
└── README.md
```

## 修改代码与配置

在 `src/task.py` 的 `run_task(config, output_dir)` 中实现任务逻辑，其他业务模块也放在 `src/`。函数接收 `config/task.json` 的 JSON 对象和输出目录，返回一个可序列化为 JSON 的字典。`main.py` 将返回值写入输出目录的 `result.json`；其他产物由业务代码写入 `output_dir`。

初始入口返回 `{"status": "template_ready"}`，用于确认配置、调用入口和结果写入能够衔接。替换为业务逻辑后，业务结果的判断也由该任务实现。

三个配置文件分别负责：

| 文件 | 负责的内容 |
| --- | --- |
| 任务的 `config/task.json` | 模型、输入、算法参数等业务配置，初始为 `{"parameters": {}}` |
| 任务的 `experiment.json` | 任务名称、镜像来源、启动参数、资源和可选本地检查 |
| eTool 根目录的 `etool.json` | 服务器、SSH、GPU 和本地状态目录 |

模板镜像的 ENTRYPOINT 已固定为 Python 和 `src/main.py`，因此 `experiment.json` 的 `command`、`local_check` 只填写 `--config`、`--output` 等参数，不再重复 Python 和脚本路径。这与 Docker 的 [ENTRYPOINT/CMD 规则](https://docs.docker.com/reference/dockerfile/#entrypoint) 一致。

## 打包路径与容器路径

打包命令接收任务配置文件的路径。模板的 `experiment.json` 设置 `"context": "."`，表示以该配置文件所在目录作为构建上下文；相对的 `context` 按配置文件位置解析，`dockerfile` 按构建上下文解析。两者也可使用绝对路径，Dockerfile 必须位于构建上下文内。

以复制后的 `tasks/my-task/` 为例，路径关系如下：

| 用途 | 本机位置 | 镜像内位置 |
| --- | --- | --- |
| 构建上下文 | `tasks/my-task/` | Docker 构建时可读取的目录 |
| 任务代码 | `tasks/my-task/src/` | `/app/src/` |
| 业务配置 | `tasks/my-task/config/` | `/app/config/` |
| 程序入口 | `tasks/my-task/src/main.py` | `/app/src/main.py` |

代码和配置的复制位置由 Dockerfile 的 `COPY` 决定，入口由 `ENTRYPOINT` 决定。`experiment.json` 的 `command` 使用容器中的路径；`context` 和 `dockerfile` 使用本机路径。工具根目录的 `etool.json` 管理服务器与 GPU，不定义任务代码位置。

需要加入其他代码、数据或模型目录时，把它放进构建上下文，并同步修改 Dockerfile 的 `COPY` 和 `.dockerignore` 允许范围。Docker 的路径规则见 [构建上下文说明](https://docs.docker.com/build/concepts/context/)。

## 准备运行环境

默认 Dockerfile 使用 `python:3.12-slim-bookworm`，并从 `ghcr.io/astral-sh/uv:0.12.5` 复制 uv。首次构建需要下载这两个基础镜像；Python 第三方依赖从任务自己的 `pyproject.toml` 和 `uv.lock` 安装，初始依赖为空。eTool 本地的 `.venv` 不进入任务镜像。

在 eTool 根目录，用 `uv add --project tasks/my-task 包名` 增加任务依赖、`uv remove --project tasks/my-task 包名` 移除依赖。手动修改任务的依赖声明后执行 `uv lock --project tasks/my-task` 和 `uv sync --project tasks/my-task --locked`。依赖变化后重新构建镜像。Dockerfile 在构建阶段执行 `uv sync --locked --no-dev --no-cache`，运行时直接使用镜像中的 Python 环境。具体机制见 [uv 的 Docker 集成说明](https://docs.astral.sh/uv/guides/integration/docker/)。

需要 GPU 框架时，先确定框架版本及其 Python、CUDA 和驱动要求，再修改任务的环境文件。使用 PyTorch 时，按 [官方安装选项](https://pytorch.org/get-started/locally/) 选择 CUDA 构建及依赖来源；服务端驱动兼容性按 [NVIDIA CUDA 兼容性说明](https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html) 核对。需要 CUDA 编译工具链或其他系统库时，在 Dockerfile 中选择对应基础环境并安装任务要求的包。调整 Python 版本时，同步修改 Dockerfile、`pyproject.toml` 和 `.python-version`，再更新锁文件。

运行期间容器关闭网络、根文件系统只读，并以远端用户的 UID/GID 运行。Python 依赖、模型权重和输入数据需要事先准备；当前任务接口只自动挂载 `/outputs`。需要随镜像携带的文件，要同时添加到 Dockerfile 和 `.dockerignore` 的允许范围。临时缓存写入 `/tmp`，需回收的文件写入 `/outputs`。

## 先在本地运行

使用任务自己的 uv 环境执行入口：

```bash
uv sync --project tasks/my-task --locked
uv run --project tasks/my-task python tasks/my-task/src/main.py \
  --config tasks/my-task/config/task.json \
  --output tasks/my-task/.tmp/outputs
```

程序成功退出后，查看 `tasks/my-task/.tmp/outputs/result.json`。初始内容应含 `"status": "template_ready"`。

再构建并运行任务镜像：

```bash
docker build -t etool-task:my-task tasks/my-task
mkdir -p tasks/my-task/.tmp/outputs
TASK_OUTPUT="$(cd tasks/my-task/.tmp/outputs && pwd)"
docker run --rm --network=none --read-only \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,size=64m \
  --mount "type=bind,src=$TASK_OUTPUT,dst=/outputs" \
  etool-task:my-task
```

该命令使用镜像的默认配置和 `/outputs` 输出位置，结果仍写入本机 `tasks/my-task/.tmp/outputs/`。这一步检查容器中的依赖、配置路径、文件权限和入口是否可用。

模板的 `local_check` 也会调用同一业务入口。修改为依赖 GPU 的任务后，应同步配置本地可执行的检查命令，或移除 `local_check`。eTool 的本地检查没有显式请求 GPU，目前也不注入 `experiment.json` 的 `env` 和资源设置。本地检查通过后，仍需在目标 GPU 上确认业务程序的实际计算。

## 交给 eTool 打包和执行

在 eTool 根目录运行：

```bash
uv run etool pack tasks/my-task/experiment.json
```

`pack` 在本地构建镜像、执行配置中的本地检查并导出归档，完成后输出任务号。本地检查输出和构建日志保存在 `.etool/jobs/<任务号>/`。

任务逻辑和环境准备完成后，可以检查服务器并执行完整流程：

```bash
uv run etool doctor
uv run etool run tasks/my-task/experiment.json
```

`run` 会创建新的运行记录，构建、上传并实际执行任务，随后回收日志和产物并清理远端资源。回收后的业务产物位于 `.etool/jobs/<任务号>/remote/outputs/`。使用前一次 `pack` 的任务号后台提交、查询或回收，见 [命令说明](../../README.md#命令)。
