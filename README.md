# eTool

eTool 是一个远程运行实验的工具，用于打包实验任务、提交到服务器运行，并在结束后将结果取回本地。任务代码和运行依赖随 Docker 镜像一起交付。eTool 提供状态查询、日志查看和资源清理命令，可通过命令行或 Skill 调用。

## 通过 Skill 安装

默认采用**项目级安装**。在需要使用 eTool 的业务项目目录中执行：

```bash
npx skills add Aiid0/eTool --skill etool
```

| 安装范围 | 安装参数 | 默认 Skill 目录 | 使用范围 |
| --- | --- | --- | --- |
| 项目级（默认） | 不加 `-g` | 当前项目的 `.agents/skills/etool/` | 安装所在项目 |
| 用户级全局 | 加 `-g` | `~/.agents/skills/etool/` | 当前用户的多个项目 |

按提示选择使用的智能体。安装器会一起下载 [SKILL.md](SKILL.md)、启动入口、程序源码和任务模板。安装需要 Node.js 22.20+、npm/npx 和 Git；运行入口还需要 uv，其他执行条件见 [运行环境](#运行环境)。安装后可让智能体按 eTool Skill 的说明准备和执行任务。

例如，只为 Codex 安装且跳过交互选择：

```bash
npx skills add Aiid0/eTool --skill etool --agent codex -y
```

在当前业务项目中设置实际安装目录，再检查 CLI 入口：

```bash
ETOOL_SKILL="$PWD/.agents/skills/etool"
uv run "$ETOOL_SKILL/scripts/etool.py" --version
uv run "$ETOOL_SKILL/scripts/etool.py" --help
```

`scripts/etool.py` 使用 uv 的脚本环境运行随附源码，并保留当前业务项目目录。Python 解释器由脚本的版本要求选择，业务项目的 Python 依赖分别管理。使用复制安装等选项时，以安装器输出的实际路径设置 `ETOOL_SKILL`。

### 用户级全局安装（跨项目共用）

希望当前用户的多个项目共用 eTool 时，在安装命令中加 `-g`：

```bash
npx skills add Aiid0/eTool --skill etool -g
ETOOL_SKILL="$HOME/.agents/skills/etool"
```

两种安装范围下，都在各业务项目中分别准备 `etool.json` 和任务配置。默认 `.etool` 状态目录跟随各项目的配置文件，任务记录和回收产物按项目保存。更新已安装的项目级或用户级 Skill：

```bash
npx skills check
npx skills update etool -p
# 用户级安装使用：npx skills update etool -g
```

未指定 Git 标签或 commit 时，安装来源是仓库默认分支。需要固定版本时，可在仓库地址后指定已存在的版本标签或完整 commit；具体语法见 [Skills CLI 文档](https://github.com/vercel-labs/skills/blob/main/docs/commands.md)。

## 能力与模块

| 模块 | 职责 | 当前实现 |
| --- | --- | --- |
| 配置与目标检查 | 解析连接与任务配置，检查基础环境及任务声明的需求 | [config.py](etool/config.py)、客户端与远端 `doctor` |
| 本地打包 | 构建镜像或使用已有镜像，执行可选检查并导出归档 | [client.py](etool/client.py) 的 `pack` |
| 传输与执行 | 上传、导入镜像并启动后台容器 | [transport.py](etool/transport.py)、[remote_agent.py](etool/remote_agent.py) |
| 运行管理 | 状态、日志、等待、取消和中断恢复 | 客户端与远端管理接口 |
| 产物与资源管理 | 回收日志和输出，确认回收后清理资源，支持显式丢弃 | 客户端与远端回收、清理接口 |

[cli.py](etool/cli.py) 统一提供命令、进度显示、JSON 输出和退出码。每次运行有独立任务号和持久化记录，后台容器独立于本地终端运行。

## 运行环境

eTool 使用 Docker 和 SSH 执行任务。未声明 GPU 的任务按 CPU 任务运行，不检查 GPU 或 NVIDIA runtime，也不请求 GPU 设备。只有在任务的 `requirements.gpu` 中声明 GPU 时，才要求服务器具备 NVIDIA 驱动、GPU 和相应的容器运行支持。

| 位置 | 运行条件 |
| --- | --- |
| 本机 | Linux 或兼容的 POSIX 环境，具备 uv、可用的 Docker daemon、SSH、rsync、gzip 和 Bash |
| 服务器 | Linux、Python 3.9+、Docker 和 rsync；SSH 登录用户能直接执行 Docker 命令。GPU 任务另需 NVIDIA 驱动和 NVIDIA Container Toolkit |

当前镜像身份核对尚未兼容 Docker 的 containerd 镜像存储，可能在导入阶段报 `IMAGE_MISMATCH`。该存储后端的镜像 ID 使用索引或 manifest 摘要，见 [Docker 实现](https://github.com/moby/moby/blob/master/daemon/containerd/image_inspect.go)。

本地构建的镜像架构须与目标服务器兼容，当前构建接口没有提供跨架构选项。uv 的安装方式见 [官方文档](https://docs.astral.sh/uv/getting-started/installation/)，服务器的容器 GPU 支持按 [NVIDIA Container Toolkit 文档](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) 配置。

eTool 的 Python 运行时支持 Python 3.11+，只依赖标准库。Skill 启动器通过脚本元数据声明解释器要求；任务镜像的解释器和依赖由任务自己的构建定义管理。机制见 [uv 脚本环境说明](https://docs.astral.sh/uv/guides/scripts/#declaring-script-dependencies)。

## 首次使用

### 1. 配置服务器连接

在业务项目根目录复制示例配置，`ETOOL_SKILL` 使用上面的实际安装路径：

```bash
cp "$ETOOL_SKILL/etool.example.json" ./etool.json
```

编辑 `etool.json`，把示例中的主机、SSH 用户和远端目录替换为实际值，按需设置端口。`remote_root` 的父目录须已存在，登录用户须有创建和写入任务目录的权限。GPU 需求在任务配置中声明，默认示例不需要 GPU。

先确认 SSH 认证和服务器主机指纹。eTool 使用非交互 SSH 认证和严格主机密钥检查，运行时不会提示输入登录密码或确认新主机。

默认 `state_dir` 为 `.etool`，相对配置文件所在目录定位，用于保存任务记录、镜像归档、日志和回收产物。服务器配置和任务记录由业务项目管理；需要保持本地使用时，将 `etool.json` 和 `.etool/` 加入该项目的 `.gitignore`。

### 2. 从模板创建任务

以下命令在业务项目根目录执行：

```bash
mkdir -p tasks
cp -R "$ETOOL_SKILL/templates/task" tasks/my-task

# 先运行任务自己的 Python 入口
uv run --project tasks/my-task --locked python tasks/my-task/src/main.py \
  --config tasks/my-task/config/task.json \
  --output tasks/my-task/.tmp/outputs
```

初始模板会生成 `tasks/my-task/.tmp/outputs/result.json`，内容为 `{"status": "template_ready"}`。它用于确认配置读取、入口调用和结果写入，业务计算在 `src/task.py` 的 `run_task` 中实现。

修改任务时，代码放在 `src/`，业务参数放在 `config/task.json`，执行参数放在 `experiment.json`，依赖和系统环境由任务的 `pyproject.toml`、`uv.lock` 与 Dockerfile 管理。任务目录由业务项目管理。详细接入方式见 [任务模板说明](templates/task/README.md)。

### 3. 检查环境并执行

服务器配置和任务环境准备完成后，在业务项目根目录执行：

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json doctor tasks/my-task/experiment.json
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json run tasks/my-task/experiment.json
```

`doctor PROJECT` 先检查本地工具与 Docker，再检查 SSH、远端 Python、Docker、磁盘及任务声明的需求；不带任务路径的 `doctor` 只检查基础环境。默认模板的 `requirements` 为 `{}`，不会触发 GPU 检查。`run` 会实际构建镜像、上传并启动容器，等待结束后回收产物，再清理本次远端资源。默认配置下，业务结果保存在 `.etool/jobs/<任务号>/remote/outputs/`，程序日志保存在该运行目录的 `remote/logs/container.log`。

模板默认使用 Python 基础镜像；任务所需的依赖和输入文件由任务自身准备。确认任务是否正确完成，需要查看任务自己的日志和结果。

## 完整使用流程

先填写业务项目中的 `etool.json`，并按 [官方任务模板](templates/task/README.md) 准备代码、运行环境和任务配置。以下命令均在业务项目根目录执行，服务器上的操作由 eTool 通过 SSH 完成。`ETOOL_SKILL` 指向实际安装目录；将 `TASK_CONFIG` 替换为任务配置路径，将 `JOB_ID` 替换为 `pack` 输出的任务号。

```bash
TASK_CONFIG='/path/to/experiment.json'

# 1. 检查服务器基础环境和任务声明的需求
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json doctor "$TASK_CONFIG"

# 2. 本地构建镜像、执行可选检查并导出归档
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json pack "$TASK_CONFIG"
JOB_ID='替换为 pack 输出的任务号'

# 3. 上传镜像并启动远端后台容器
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json submit "$JOB_ID"

# 4. 查看状态、日志，并等待容器结束
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json status "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json logs "$JOB_ID" --tail 200
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json wait "$JOB_ID"

# 5. 回收日志和产物，确认本地副本；此时保留远端资源
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json fetch "$JOB_ID"

# 6. 删除本次运行的远端容器、专属镜像和任务目录
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json clean "$JOB_ID"

# 7. 读取本地最终记录，成功清理后的状态为 cleaned
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json status "$JOB_ID"
```

任务非零退出后仍可继续 `fetch` 回收失败日志和部分产物，再执行 `clean`。

回收后的业务产物位于 `<state_dir>/jobs/<任务号>/remote/outputs/`，原始程序日志位于同一运行目录的 `remote/logs/container.log`。`clean` 成功后，这些本地文件仍保留；清理结果写入 `cleanup.json`。

### 自动完成回收和清理

| 命令 | 清理行为 |
| --- | --- |
| `run "$TASK_CONFIG"` | 新建运行，完成打包、提交、等待，在回收确认后自动清理 |
| `resume "$JOB_ID"` | 继续已有运行，在回收确认后自动清理 |
| `fetch "$JOB_ID" --cleanup` | 回收已有运行的产物，确认后立即清理 |

表中命令通过 `uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json <命令>` 调用。单独执行 `fetch` 会保留远端资源，随后用 `clean` 删除。`cancel` 只停止容器，仍需回收和清理。连接、下载或回收检查失败时保留本地运行记录；修复后先用原任务号查询实际状态，再用 `resume` 继续。清理期间响应丢失时，部分或全部资源可能已经移除。

清理范围限于本次运行的 Docker 容器、专属镜像和远端任务目录，保留共享源镜像，不执行全局 prune。`clean` 也会删除本地本次构建的任务镜像，但保留镜像归档、日志和已回收产物。

## 智能体调用

智能体加载 [SKILL.md](SKILL.md) 后，应从发现信息中取得实际 Skill 目录，在业务项目目录中调用随附入口。使用 `--json` 获取最终 JSON 结果，构建和传输进度会写入 stderr：

```bash
# 当前目录已准备好服务器配置、experiment.json 及其构建上下文
uv run "$ETOOL_SKILL/scripts/etool.py" \
  --config ./etool.json --json pack ./experiment.json
```

相对路径按当前业务项目目录解析。保存 `pack` 输出的 `id`，然后按原任务号管理运行：

```bash
JOB_ID='替换为 pack 输出的 id'

uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json submit "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json status "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json logs "$JOB_ID" --tail 100

# 容器结束后回收产物，并清理本次远端资源
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json fetch "$JOB_ID" --cleanup
```

`submit` 完成镜像上传、导入和容器启动后返回，任务在服务器后台继续运行。根据 `status` 的阶段和 `exit_code` 判断执行结果；等待超时、SSH 断连或启动响应丢失时，沿用原任务号查询和恢复。`run` 每次都会创建新的运行，适用于确实需要新执行的情况。

## 配置

### 工具配置

工具配置为 JSON 对象，默认文件名为 `etool.json`，用于保存服务器连接和本地状态目录。未知字段会被拒绝。可复制的字段示例见 [etool.example.json](etool.example.json)。

| 字段 | 必填或默认值 | 含义与约束 |
| --- | --- | --- |
| `host` | 必填 | SSH 主机，支持简单 DNS 名、IPv4 地址或 SSH 别名 |
| `user` | 必填 | SSH 登录用户 |
| `port` | 默认 `22` | 1～65535 的整数 |
| `remote_root` | 必填 | 远端工作根目录，绝对且非根路径；父目录须已存在 |
| `state_dir` | 默认 `.etool` | 本地状态目录，相对路径按配置文件所在目录解析 |

为兼容旧配置，`gpu_uuid` 和 `gpu_index` 仍可成对提供，但不会为新任务隐式启用 GPU。新任务须在自己的 `requirements.gpu` 中声明设备。

`host` 和 `user` 按 `[A-Za-z0-9_][A-Za-z0-9_.-]*` 检查。`port` 不接受布尔值。`remote_root` 仅允许 ASCII 字母、数字、下划线、句点、短横线和斜杠，不允许 `.`、`..` 路径段；远端拒绝符号链接路径和已存在的非目录工作根。

配置查找顺序为 `--config PATH`、`ETOOL_CONFIG`、当前目录及父目录中的 `etool.json`，最后回退到工具安装目录中的 `etool.json`。实际配置文件需要自行准备，Skill 只分发示例配置。`config_path` 是解析后添加的来源信息，不是配置文件输入字段。

### 任务配置

任务配置也是 JSON 对象。命令接收配置文件路径；传入目录时，默认读取其中的 `experiment.json`。[官方任务模板](templates/task/README.md) 提供可复制的代码、配置和环境文件，以及接入步骤。

| 字段 | 默认值与约定 |
| --- | --- |
| `name` | 默认配置所在目录名；使用字母、数字、句点、下划线或短横线 |
| `context` | 默认 `.`，相对任务配置文件解析的构建上下文 |
| `dockerfile` | 默认 `Dockerfile`，相对构建上下文解析，必须位于其内部 |
| `image` | 可选的已有本地镜像引用，与显式 `dockerfile` 互斥；打包时构建带归属标记的派生镜像，保留源镜像 |
| `command` | 字符串参数数组或 `null`，默认 `null` |
| `local_check` | 可选的本地检查参数数组；省略或 `null` 时不执行 |
| `env` | 字符串到字符串的环境变量对象，默认空对象 |
| `resources` | CPU、内存和共享内存限制，默认值见下表 |
| `requirements` | 默认 `{}`；任务所需的宿主环境，当前仅支持可选的 `gpu` 对象 |

非空 `command` 替换镜像 CMD 参数并保留 ENTRYPOINT；省略、`null` 或空数组时沿用镜像 CMD。参数直接传给 Docker，不按 shell 字符串解释。`local_check` 使用相同的参数拼接方式。

`local_check` 在本地使用同一任务镜像运行，失败会阻止打包完成。它不请求 GPU，并显式设置 `NVIDIA_VISIBLE_DEVICES=void`；不注入任务 `env` 或应用远端资源设置，因此检查范围取决于本地条件和使用者提供的命令。

### 可选的 GPU 需求

在任务的 `experiment.json` 中声明所需宿主环境。`requirements` 省略或为 `{}` 时按 CPU 任务运行，不检查 GPU 或 NVIDIA runtime。当前只接受 `gpu` 需求；Python 包和系统依赖仍通过任务的 `pyproject.toml`、`uv.lock` 和 Dockerfile 准备。

需要 GPU 时，先用实际主机、用户和端口查询设备：

```bash
ssh -p 22 taskuser@task-server \
  'nvidia-smi --query-gpu=index,uuid,name --format=csv,noheader'
```

将选中设备的完整 `uuid` 和物理 `index` 写入任务配置的 `requirements.gpu`，例如：

```json
{
  "requirements": {
    "gpu": {
      "uuid": "GPU-00000000-0000-0000-0000-000000000000",
      "index": 0,
      "min_free_mib": 2048
    }
  }
}
```

以上展示任务配置中的 `requirements` 字段，不替代完整的 `experiment.json`。全零 UUID 是占位，须替换为实际设备 UUID。

| `requirements.gpu` 字段 | 必填或默认值 | 含义与约束 |
| --- | --- | --- |
| `uuid` | 必填 | 完整 NVIDIA GPU UUID，十六进制部分规范化为小写 |
| `index` | 必填 | 宿主物理 GPU 编号，非负整数，不接受布尔值 |
| `min_free_mib` | 可选，无默认门槛 | 0～81920 的整数，不接受布尔值；省略时不检查空闲显存门槛 |

`gpu` 存在时必须是对象，并同时提供 `uuid` 和 `index`。显存门槛仅用于启动前检查，不预留显存，也不限制任务后续使用量。修改需求后，用 `doctor tasks/my-task/experiment.json` 检查对应环境；任务是否实际使用 GPU 仍需根据程序日志和结果确认。

### 资源与执行约定

| `resources` 字段 | 默认值 | 约束 |
| --- | --- | --- |
| `cpus` | `1` | 有限数值，范围 0.1～32 |
| `memory` | `2g` | 正整数字符串，可带 k/m/g 后缀，范围 64 MiB～512 GiB |
| `shm_size` | `256m` | 与 `memory` 同格式，至少 1 MiB，不超过 `memory` |

代码、依赖、模型和输入文件通过任务镜像准备。远端容器使用远端用户 UID/GID、只读根文件系统、关闭网络，以及 CPU、内存和进程数限制。仅 GPU 任务请求声明的设备；CPU 任务不请求设备，并显式设置 `NVIDIA_VISIBLE_DEVICES=void`。临时文件使用 `/tmp`，需回收的产物写入 `/outputs`，日志写入 stdout/stderr。当前只自动挂载 `/outputs`。

`env` 中的 GPU 可见性和工具保留变量由 eTool 管理，不允许覆盖。对 GPU 任务，启动时检查设备身份、NVIDIA runtime、配置的空闲显存门槛及其他 eTool 任务的设备占用，并通过探测容器核对设备可见性。GPU 分配不代表独占或显存配额，业务计算是否正确由任务自身判断。

## 命令

通过 `uv run "$ETOOL_SKILL/scripts/etool.py" <命令>` 调用，具体参数可用 `--help` 查看。`--config PATH`、`--json` 可放在子命令前或后，`--version` 输出版本后退出。

| 命令 | 用途 |
| --- | --- |
| `doctor [PROJECT]` | 检查本地工具、Docker、SSH、远端 Python、Docker 和磁盘；传入任务路径时，追加任务声明的环境需求检查 |
| `pack PROJECT` | 本地构建、可选检查并导出镜像，不提交远端 |
| `submit JOB_ID` | 准备、上传、导入并后台启动已打包任务 |
| `status [JOB_ID]` | 查询指定运行；省略任务号时列出本地记录 |
| `list` | 列出本地运行记录 |
| `logs JOB_ID [--tail N] [--follow/-f]` | 查看日志；N 为非负整数，默认 200，跟随模式持续到容器退出 |
| `wait JOB_ID [--timeout SECONDS]` | 等待容器结束，本地等待超时不停止远端任务 |
| `fetch JOB_ID [--cleanup]` | 回收并核对产物，可选随后清理 |
| `clean JOB_ID` | 重新核对本地副本后清理远端资源 |
| `cancel JOB_ID` | 停止任务，保留日志和输出 |
| `discard JOB_ID` | 显式丢弃停止或未启动任务的未回收资源 |
| `resume JOB_ID [--timeout SECONDS]` | 根据运行记录继续提交、等待、回收和清理 |
| `run PROJECT [--timeout SECONDS]` | 新建运行，完成打包、提交、等待、回收和清理 |

`--timeout` 为有限正数，限制本地等待时间。`run` 不会自动调用 `doctor`。

`--json` 成功时输出命令结果，失败时输出 `{"ok": false, "error": "..."}`，进度写入 stderr。`logs --follow` 输出文本流，与 `--json` 互斥。

正常完成的 `run`、`resume`、`wait` 返回任务退出码；负值按 `128 + 信号号` 转换，最终上限为 255。其他命令成功返回 0，已处理的配置、连接和工具错误返回 2，用户中断返回 130。

## 运行记录、恢复与清理

本地记录位于 `<state_dir>/jobs/<job_id>/`，远端任务目录位于 `<remote_root>/jobs/<job_id>/`。`job.json` 保存配置快照、镜像和容器信息、阶段、退出码及错误，`events.jsonl` 保存进度。后续操作沿用运行记录中的配置快照和归属标记。

本地主要阶段包括 `created`、`built`、`packed`、`loaded`、`running`、`exited`、`collected`、`verified`、`cleaned`；远端目录准备后为 `prepared`，显式丢弃的本地记录为 `discarded`。阶段、`exit_code` 和 `last_error` 分别表示进度、任务执行结果和最近工具错误。

任务需求保存在运行记录中。旧版 GPU 运行可继续按原任务号恢复；新任务配置不再接受 `resources.min_free_gpu_mib`，应迁移到 `requirements.gpu.min_free_mib`。

`resume` 协调 Docker 实际状态并继续工具流程，已启动或结束的容器不会重复执行。启动结果无法确认时报告 `START_UNCERTAIN`，保留资源，供查询和明确操作处理。业务程序的 checkpoint 恢复由任务自身实现。

任务非零退出后仍可回收日志和输出。`clean` 和 `fetch --cleanup` 需要先确认本地副本；`discard` 表示明确放弃未回收产物。清理均检查资源归属和容器停止状态，只删除本次运行的容器、专属镜像及目录，保留共享源镜像。

清理中断通过归属日志恢复。资源已经移除后，重试只在确认资源不存在时返回完成。已清理运行的状态读取本地记录；日志在 `verified` 或 `cleaned` 阶段优先读取本地回收副本。

## 源码开发

修改 eTool 自身时，可以克隆仓库并准备开发环境：

```bash
git clone https://github.com/Aiid0/eTool.git
cd eTool
uv sync --locked
uv run etool --help
```

开发环境默认使用 `.python-version` 指定的 Python 3.12，由仓库根目录的 `pyproject.toml`、`uv.lock` 管理。后续在仓库根目录执行 `git pull --ff-only` 和 `uv sync --locked` 更新源码与环境。

## 内部接口

<details>
<summary>SSH 协议、运行身份与远端操作</summary>

客户端通过 SSH 临时执行 `python3 -c <remote_agent.py source>`，stdin 输入单个 JSON 请求，stdout 返回单个 JSON 响应。远端不安装常驻服务。请求包含 `action`、`config`；除 `doctor` 外还需要 `job`。`doctor` 可通过顶层 `requirements` 传入任务需求，省略时只做基础检查。

成功响应为 `{"ok": true, "result": {...}}`，失败响应为 `{"ok": false, "error": {"code": "...", "message": "..."}}`；未分类异常返回 `INTERNAL_ERROR`。它与 CLI 的 JSON 输出封装不同。

SSH 使用非交互认证和严格主机密钥检查，单次调用默认等待上限为 300 秒。通信超时或响应丢失不能证明远端操作未执行，也不代表远端任务被停止。

| `job` 身份字段 | 含义 |
| --- | --- |
| `id` | 客户端按 UTC 时间与随机后缀生成的运行标识 |
| `token` | 32 位随机十六进制归属标记，重试沿用 |
| `image_tag`、`container_name` | 分别为 `etool-job:<job_id>`、`etool-<job_id>` |
| `archive_sha256`、`archive_bytes` | 镜像归档摘要与字节数 |
| `image_config_digest` | Docker 镜像配置摘要 |

镜像和容器通过 `etool.job`、`etool.token` labels 关联运行身份，GPU 执行容器另记录 `etool.gpu_uuid`，CPU 容器不使用该 GPU 标签。归属核对结合目录元数据、UID、任务标记和资源标签；SSH 负责连接身份验证。

| `action` | 职责与主要返回值 |
| --- | --- |
| `doctor` | 无需 `job`；返回 UID/GID、Docker 信息、可用磁盘及工作根目录，仅在 `requirements.gpu` 已声明时检查并返回 GPU 信息 |
| `prepare` | 创建专属目录并保存归属和资源基线；返回 `directory`、`state`、`baseline` |
| `load` | 校验已上传归档、导入并核对镜像；返回 `image_id`、`state` |
| `start` | 使用顶层 `options` 检查资源并按记录中的需求启动容器；GPU 任务追加设备检查及探测；已启动容器只协调状态 |
| `status` | 协调 Docker 与记录，返回通用状态；未知目录返回 `NOT_FOUND` |
| `logs` | 返回 `text`、`container_state`、`state`；顶层 `tail` 默认 200，接受 0～100000，`null` 或 -1 表示全部 |
| `collect` | 容器须已退出或为 dead；冻结完整日志和输出，重复调用复用结果；返回归档及清单摘要、字节数、退出码和阶段 |
| `acknowledge` | 顶层 `archive_sha256`、`manifest_sha256` 须匹配冻结结果；返回 `state=verified` 和 `exit_code` |
| `cleanup` | 结果已确认或恢复已开始的清理；拒绝活动容器，返回清理检查结果 |
| `cancel` | 停止本运行的活动容器，保留日志和输出，返回通用状态 |
| `discard` | 允许放弃未回收产物，仍核对归属和停止状态；远端返回清理结果，本地记录为 `discarded` |

`options` 由任务的 `command`、`env`、`requirements` 和展开后的 `resources` 合并。通用状态字段为 `state`、`container_id`、`container_state`、`exit_code`、`gpu`、`directory`、`image_id`，尚未产生的值可以为 `null`，CPU 任务的 `gpu` 为 `null`；远端的 `container_state` 是 Docker State 对象。

回收文件为 `results.tar.gz` 和 `RESULTS.json`。两类任务都回收程序输出、`logs/container.log` 和 `logs/container-inspect.json`；GPU 任务另需回收 `gpu-host.json` 和 `logs/gpu-probe.log`，CPU 任务不要求这些 GPU 证据文件。清单包含 `schema_version`、`job_id`、`files`，每项记录 `path`、`size`、`sha256`。客户端校验后安全解包并发布到本地 `remote/`，拒绝越界路径、链接、特殊文件和清单外成员；正常清理前再次检查本地副本。

清理结果含 `state=cleaned`、`container_absent`、`image_tag_absent`、`image_id_absent`、`preexisting_image_preserved`、`task_directory_absent`、`baseline`、`after`。已有共享镜像可保留，`image_id_absent=false` 不单独表示失败。整体资源集合的变化也不能替代本运行的归属检查。工具只移除自己创建的空工作目录，原有目录保留，不执行全局 prune。

</details>
