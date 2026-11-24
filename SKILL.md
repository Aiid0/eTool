---
name: etool
description: Package tasks into Docker images, execute them on a configured remote NVIDIA GPU server over SSH, and manage job status, logs, outputs, recovery, and cleanup. Use when a project needs remote GPU execution through eTool.
---

# eTool

Use the bundled CLI from the user's business project directory. Set `ETOOL_SKILL` to the absolute directory containing this `SKILL.md`, as supplied by the agent's skill discovery. The CLI source, configuration example, and task template are in the same directory.

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --help
```

The launcher declares its Python requirement and uses uv's script environment. It preserves the caller's working directory. The local machine needs uv, a POSIX environment, Docker, SSH, rsync, gzip, and Bash. The server needs Python 3.9+, Docker with NVIDIA GPU support, and rsync. SSH authentication and the server's host key must already be configured for noninteractive access.

## Prepare a project

1. Reuse the project's server configuration, or copy `etool.example.json` from the skill directory into the project as `etool.json`. Fill in the actual SSH target, writable remote directory, GPU UUID, and physical index. Obtain missing connection or device information from the user; the example values are placeholders.
2. Keep a separate `etool.json` in each project when its runs should have separate local records. Relative `state_dir` is resolved from that configuration file, and defaults to `.etool`.
3. Reuse the project's task configuration and Dockerfile or image. To create a task, copy `templates/task/` from the skill directory into the project. See [the task template](templates/task/README.md) for its code, dependency, and image layout.
4. Run `doctor` after configuring a new target. It checks local tools before the SSH/GPU/Docker checks. Success confirms those reported checks; the task must still establish that its image can execute and its computation is correct.

Pass an explicit project configuration path on job commands:

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json doctor
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json pack ./experiment.json
```

Code, dependencies, model weights, and inputs must be prepared in the task image. Execution containers have networking disabled and a read-only root filesystem. Temporary files can use `/tmp`; outputs to retrieve must be written to `/outputs`, with logs on stdout/stderr. Match the image CPU architecture to the server. The template initially returns `template_ready`; implement the task's actual computation before treating that output as a business result.

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
- `GPU_BUSY` means startup was rejected because another eTool task is using the configured GPU; automatic queuing is not provided.
- `cancel JOB_ID` stops the container while preserving outputs for retrieval. `discard JOB_ID` deletes stopped or unstarted resources without retrieving their outputs; use it only when the user has chosen to abandon those outputs.
- `--json` errors currently contain a message string. Inspect the command exit status and the saved run record when diagnosing failures. `logs --follow` streams text and cannot be combined with `--json`.

For resource fields, command parameters, and protocol details, read [README.md](README.md) as needed.
