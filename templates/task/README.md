# 官方任务模板

**简体中文** | [English](README.en.md)

本目录随 eTool Skill 一起安装。复制它建立自己的任务，按下面的位置修改代码、配置和运行环境。先按 [安装说明](https://github.com/Aiid0/eTool#通过-skill-安装) 设置 `ETOOL_SKILL` 为实际安装目录，以下命令均在业务项目根目录执行。

## 创建任务目录

将 `my-task` 替换为新的任务目录名：

```bash
mkdir -p tasks
cp -R "$ETOOL_SKILL/templates/task" tasks/my-task
```

任务也可以放在其他项目目录，提交时把配置路径指向那个目录即可。日常修改复制后的任务目录。

```text
tasks/my-task/
├── src/
│   ├── main.py          # 参数解析、配置读取、日志和结果写入
│   └── task.py          # 在 run_task 中实现业务逻辑
├── config/task.json     # 业务参数
├── experiment.json      # eTool 执行参数、环境需求、资源和本地检查
├── Dockerfile           # 镜像基础环境和系统依赖
├── pyproject.toml       # 任务 Python 依赖
├── uv.lock              # 任务依赖锁文件
├── .python-version      # 本地任务解释器版本
├── .dockerignore        # 进入镜像构建上下文的文件
├── README.md
└── README.en.md
```

## 修改代码与配置

在 `src/task.py` 的 `run_task(config, output_dir)` 中实现任务逻辑，其他业务模块也放在 `src/`。函数接收 `config/task.json` 的 JSON 对象和输出目录，返回一个可序列化为 JSON 的字典。`main.py` 将返回值写入输出目录的 `result.json`；其他产物由业务代码写入 `output_dir`。

初始入口返回 `{"status": "template_ready"}`，用于确认配置、调用入口和结果写入能够衔接。替换为业务逻辑后，业务结果的判断也由该任务实现。

三个配置文件分别负责：

| 文件 | 负责的内容 |
| --- | --- |
| 任务的 `config/task.json` | 模型、输入、算法参数等业务配置，初始为 `{"parameters": {}}` |
| 任务的 `experiment.json` | 任务名称、镜像来源、启动参数、宿主环境需求、资源和可选本地检查 |
| 业务项目的 `etool.json` | 服务器连接、SSH 和本地状态目录 |

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

代码和配置的复制位置由 Dockerfile 的 `COPY` 决定，入口由 `ENTRYPOINT` 决定。`experiment.json` 的 `command` 使用容器中的路径；`context` 和 `dockerfile` 使用本机路径。业务项目的 `etool.json` 管理服务器连接，任务的 GPU 需求写在 `experiment.json` 中；服务器配置不定义任务代码位置。

需要加入其他代码、数据或模型目录时，把它放进构建上下文，并同步修改 Dockerfile 的 `COPY` 和 `.dockerignore` 允许范围。Docker 的路径规则见 [构建上下文说明](https://docs.docker.com/build/concepts/context/)。

## 准备运行环境

默认 Dockerfile 使用 `python:3.12-slim-bookworm`，并从 `ghcr.io/astral-sh/uv:0.12.5` 复制 uv。首次构建需要下载这两个基础镜像；Python 第三方依赖从任务自己的 `pyproject.toml` 和 `uv.lock` 安装，初始依赖为空。eTool 的运行环境与任务镜像分别管理。

在业务项目根目录，用 `uv add --project tasks/my-task 包名` 增加任务依赖、`uv remove --project tasks/my-task 包名` 移除依赖。手动修改任务的依赖声明后执行 `uv lock --project tasks/my-task` 和 `uv sync --project tasks/my-task --locked`。依赖变化后重新构建镜像。Dockerfile 在构建阶段执行 `uv sync --locked --no-dev --no-cache`，运行时直接使用镜像中的 Python 环境。具体机制见 [uv 的 Docker 集成说明](https://docs.astral.sh/uv/guides/integration/docker/)。

需要 GPU 框架时，先确定框架版本及其 Python、CUDA 和驱动要求，再修改任务的环境文件。使用 PyTorch 时，按 [官方安装选项](https://pytorch.org/get-started/locally/) 选择 CUDA 构建及依赖来源；服务端驱动兼容性按 [NVIDIA CUDA 兼容性说明](https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html) 核对。需要 CUDA 编译工具链或其他系统库时，在 Dockerfile 中选择对应基础环境并安装任务要求的包。调整 Python 版本时，同步修改 Dockerfile、`pyproject.toml` 和 `.python-version`，再更新锁文件。

运行期间容器关闭网络、根文件系统只读，并以远端用户的 UID/GID 运行。Python 依赖、模型权重和输入数据需要事先准备；当前任务接口只自动挂载 `/outputs`。需要随镜像携带的文件，要同时添加到 Dockerfile 和 `.dockerignore` 的允许范围。临时缓存写入 `/tmp`，需回收的文件写入 `/outputs`。

## 声明宿主环境需求

模板的 `experiment.json` 默认使用 `"requirements": {}`，按 CPU 任务执行，不检查 GPU 或 NVIDIA runtime，也不请求 GPU 设备。CPU、内存和共享内存限制由 `resources.cpus`、`resources.memory` 和 `resources.shm_size` 指定。

需要 GPU 时，在任务的 `experiment.json` 中把 `requirements` 改成下面的对象，其他字段保持任务所需的设置：

```json
{
  "gpu": {
    "uuid": "GPU-00000000-0000-0000-0000-000000000000",
    "index": 0,
    "min_free_mib": 2048
  }
}
```

完整 GPU UUID 和宿主物理编号必须同时提供；全零 UUID 仅为占位。`min_free_mib` 可省略，省略时不检查空闲显存门槛；填写时须为 0～81920 的整数。查询设备和字段约束见 [GPU 需求说明](https://github.com/Aiid0/eTool#可选的-gpu-需求)。设备选择和显存门槛都属于该任务，不从服务器配置隐式继承。

当前 `requirements` 仅支持可选的 `gpu` 对象。Python 包、系统库、模型和数据仍按前文通过任务镜像准备。GPU 任务启动时会检查设备身份、NVIDIA runtime、配置的显存门槛及其他 eTool 任务的占用，并用探测容器核对设备可见性；这些检查不保证 GPU 独占或业务计算正确。

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
  --env NVIDIA_VISIBLE_DEVICES=void \
  --tmpfs /tmp:rw,size=64m \
  --mount "type=bind,src=$TASK_OUTPUT,dst=/outputs" \
  etool-task:my-task
```

该命令使用镜像的默认配置和 `/outputs` 输出位置，结果仍写入本机 `tasks/my-task/.tmp/outputs/`。这一步检查容器中的依赖、配置路径、文件权限和入口是否可用。

模板的 `local_check` 也会调用同一业务入口。修改为依赖 GPU 的任务后，应同步配置本地可执行的检查命令，或移除 `local_check`。eTool 的本地检查不请求 GPU，并显式设置 `NVIDIA_VISIBLE_DEVICES=void`；也不注入 `experiment.json` 的 `env` 或应用远端资源设置。本地检查通过后，仍需在目标服务器上确认任务结果。

## 交给 eTool 打包和执行

在业务项目根目录运行，使用该项目的服务器配置：

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json pack tasks/my-task/experiment.json
```

`pack` 在本地构建镜像、执行配置中的本地检查并导出归档，完成后输出任务号。本地检查输出和构建日志保存在 `.etool/jobs/<任务号>/`。

任务逻辑和环境准备完成后，可以检查服务器并执行完整流程：

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json doctor tasks/my-task/experiment.json
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json run tasks/my-task/experiment.json
```

`doctor PROJECT` 检查基础环境和任务声明的需求；省略任务路径时只检查基础工具、SSH、远端 Python、Docker 和磁盘。`run` 不自动调用 `doctor`，但启动时仍会核查声明的 GPU 需求。

`run` 会创建新的运行记录，构建、上传并实际执行任务，随后回收日志和产物并清理远端资源。回收后的业务产物位于 `.etool/jobs/<任务号>/remote/outputs/`。使用前一次 `pack` 的任务号后台提交、查询或回收，见 [命令说明](https://github.com/Aiid0/eTool#命令)。
