---
name: etool
description: Package tasks with their runtime dependencies, run them on a server, and retrieve the results locally. Use when a project needs remote task execution through eTool, including status, logs, recovery, and cleanup.
---

# eTool

Use the bundled CLI from the user's business project directory. Set `ETOOL_SKILL` to the absolute directory containing this `SKILL.md`, as supplied by the agent's skill discovery. The CLI source, configuration example, and task template are in the same directory.

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --help
```

The launcher declares its Python requirement and uses uv's script environment. It preserves the caller's working directory. The local machine needs uv, a POSIX environment, Docker, SSH, rsync, gzip, and Bash. The server needs Python 3.9+, Docker, and rsync. Only tasks that declare `requirements.gpu` need an NVIDIA GPU, drivers, and NVIDIA Container Toolkit. SSH authentication and the server's host key must already be configured for noninteractive access.

## Prepare a project

1. Reuse the project's server configuration, or copy `etool.example.json` from the skill directory into the project as `etool.json`. Fill in the actual SSH target and writable remote directory. Obtain missing connection information from the user; the example values are placeholders. GPU selection belongs in the task configuration, not the server configuration.
2. Keep a separate `etool.json` in each project when its runs should have separate local records. Relative `state_dir` is resolved from that configuration file, and defaults to `.etool`.
3. Reuse the project's task configuration and Dockerfile or image. To create a task, copy `templates/task/` from the skill directory into the project. See [the task template](templates/task/README.md) for its code, dependency, and image layout.
4. Inspect the task's entry point, configuration, dependencies, and build definition, then configure its environment using the rules below. Preserve the task's programming language and requested execution mode.
5. Run `doctor PROJECT` after preparing a target and task. It checks local tools, SSH, remote Python, Docker, disk space, and the task's declared requirements. `doctor` without a task path checks only the base environment. Success confirms those reported checks; the task must still establish that its image can execute and its computation is correct.

Pass an explicit project configuration path on job commands:

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json doctor ./experiment.json
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json pack ./experiment.json
```

## Configure the task environment

Determine requirements from the current task and the user's request before collecting device information or changing configuration. Use the following locations:

| Need | Where to configure it | Decision rule |
| --- | --- | --- |
| Server connection | Project `etool.json` | Configure SSH host, user, port, remote directory, and local state directory. |
| GPU | Task `experiment.json`, under `requirements.gpu` | Declare it when the task requires GPU execution or the user requests GPU acceleration. |
| Runtime, framework, and software dependencies | Task image, Dockerfile, and dependency/lock files | Follow the task's language and versions. For the Python template, use its `pyproject.toml` and `uv.lock`; use the corresponding build files for other languages. Prepare dependencies during image construction. |
| CPU, memory, and shared memory | Task `resources.cpus`, `resources.memory`, and `resources.shm_size` | Set limits to suit the task and the user's budget. These are container limits, not host capacity checks or resource reservations. |
| Application settings | Task `env` and `command` | Use string environment values and an argument array. GPU visibility and `ETOOL_` variables are managed by eTool. |
| Input data, model weights, and other files | Build context, Dockerfile `COPY`, and `.dockerignore` | Include files the task needs before execution; runtime networking is disabled and only `/outputs` is mounted automatically. |

### Choose CPU or GPU

- Select the mode required by the task's code, effective configuration, and the user's request. If GPU acceleration is optional and no GPU mode is requested or configured, use the CPU default. Clarify an uncertain choice when it materially affects feasibility or the user's constraints. A server having GPUs, a framework supporting GPUs, or an image containing CUDA does not establish a GPU requirement.
- For CPU execution, leave `requirements` absent or set it to `{}`. Do not collect GPU UUIDs or run GPU/NVIDIA checks for that task.
- For GPU execution, declare `requirements.gpu` before submission. Query the configured server for the actual device's full UUID and physical index. Resolve missing access or device selection information before submitting; use the selected device's actual values. If GPU checks fail, diagnose the declared requirement rather than silently switching execution modes.

For a GPU task, replace the `requirements` field in its `experiment.json` with this structure, using the actual device values:

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

This is a field example, not a complete task configuration. The all-zero UUID is a placeholder. Both `uuid` and a nonnegative integer `index` are required when `gpu` is present. Set `min_free_mib` only when a startup free-memory threshold is required; otherwise omit it. When provided, it must be an integer from 0 to 81920. There is no default threshold, and this field does not reserve memory or provide exclusive GPU access.

### Apply other environment requirements

`requirements` currently accepts only `gpu`. For software requirements, choose and build the appropriate task image using the task's dependency declarations and upstream installation guidance. For GPU frameworks, also establish compatibility between the framework/CUDA build and the target driver; `doctor` does not validate arbitrary package versions or application compatibility. Do not invent additional `requirements` keys for Python, CUDA, other accelerators, or system libraries.

Check the task against the supported execution conditions: the image architecture must match the server, the root filesystem is read-only, networking is disabled, and only `/outputs` is mounted automatically. Use `/tmp` for temporary files, `/outputs` for results to retrieve, and stdout/stderr for logs. Resolve requirements for additional devices, runtime network access, writable paths, or extra mounts before submission; the current configuration does not expose those capabilities. Prepare task dependencies in the image rather than installing them directly on the server.

After preparing the image definition and task configuration, run `doctor PROJECT`. A successful result establishes only the checks it reports. Use a suitable `local_check` and inspect the actual task's exit code, logs, and outputs to establish that its environment and computation work. The template's `template_ready` output only confirms the initial configuration, entry point, and result-writing path.

CPU execution and local checks request no GPU and explicitly set `NVIDIA_VISIBLE_DEVICES=void`. `local_check` does not inject the task's `env` or apply remote resource limits. Use a local check that can run without a GPU, or omit it. For GPU tasks, startup additionally checks the declared device identity, NVIDIA runtime, configured free memory threshold, use by other eTool tasks, and visibility in a probe container. `run` does not call `doctor` automatically.

The server configuration still accepts paired legacy `gpu_uuid`/`gpu_index` for compatibility, but these do not enable a GPU for a new task. Move new task memory thresholds from the former `resources.min_free_gpu_mib` to `requirements.gpu.min_free_mib`. Requirements are saved when a task is packed: editing `experiment.json` affects the next pack, while existing jobs continue with their saved configuration. Recover older jobs with their original IDs and saved requirements.

## Manage a run

Save the `id` returned by `pack`. Use that job ID for subsequent operations; progress is written to stderr when `--json` is used.

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json submit "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json status "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json logs "$JOB_ID" --tail 100
```

`submit` uploads and loads the image, starts the container, then returns while the remote task continues. Poll status or use `wait` with a suitable timeout. Evaluate `state` and `exit_code` together: collection and cleanup can succeed after the task itself exits nonzero.

When the container has stopped, retrieve its logs and outputs:

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json fetch "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json clean "$JOB_ID"
```

`fetch --cleanup` combines retrieval and cleanup. Outputs are retained under `<state_dir>/jobs/<job_id>/remote/outputs/`, and the original container log is `remote/logs/container.log`. Use the returned `local_dir` to locate the run.

## Recovery and lifecycle choices

- `run PROJECT` creates a new job and completes execution, retrieval, and cleanup. Use it when a new execution is intended.
- After a wait timeout, connection failure, or `START_UNCERTAIN`, keep the original job ID and inspect its actual status. Use `resume JOB_ID` to continue that job through retrieval and cleanup. A lost response does not establish whether the remote action took effect.
- `GPU_BUSY` applies to GPU tasks: startup was rejected because another eTool task is using the declared GPU; automatic queuing is not provided.
- `cancel JOB_ID` stops the container while preserving outputs for retrieval. `discard JOB_ID` deletes stopped or unstarted resources without retrieving their outputs; use it only when the user has chosen to abandon those outputs.
- `--json` errors currently contain a message string. Inspect the command exit status and the saved run record when diagnosing failures. `logs --follow` streams text and cannot be combined with `--json`.

For resource fields, command parameters, and protocol details, read [README.md](README.md) as needed.
