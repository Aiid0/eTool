# eTool

[简体中文](README.md) | **English**

eTool packages experiment tasks, submits them to a remote server for execution, and retrieves the results locally when they finish. Task code and runtime dependencies are delivered together in a Docker image. eTool provides commands for checking status, viewing logs, and cleaning up resources, and can be used from the command line or through a Skill.

## Install as a Skill

Installation is **project-level by default**. Run this command in the application project where you want to use eTool:

```bash
npx skills add Aiid0/eTool --skill etool
```

| Installation scope | Installation flag | Default Skill directory | Available to |
| --- | --- | --- | --- |
| Project-level (default) | Without `-g` | `.agents/skills/etool/` in the current project | The project where it is installed |
| User-level global | With `-g` | `~/.agents/skills/etool/` | Multiple projects for the current user |

Follow the prompts to select your agent. The installer downloads [SKILL.md](SKILL.md), the launcher, source code, and task template together. Installation requires Node.js 22.20+, npm/npx, and Git; running the launcher also requires uv. See [runtime requirements](#runtime-requirements) for the other execution requirements. After installation, your agent can follow the eTool Skill instructions to prepare and run tasks.

For example, to install only for Codex and skip interactive selection:

```bash
npx skills add Aiid0/eTool --skill etool --agent codex -y
```

Set the actual installation directory in your application project, then check the CLI entry point:

```bash
ETOOL_SKILL="$PWD/.agents/skills/etool"
uv run "$ETOOL_SKILL/scripts/etool.py" --version
uv run "$ETOOL_SKILL/scripts/etool.py" --help
```

`scripts/etool.py` runs the bundled source in a uv script environment while preserving the current application project directory. The script's version requirement determines the Python interpreter; your project's Python dependencies are managed separately. If you use options such as copy installation, set `ETOOL_SKILL` to the actual path reported by the installer.

### User-level global installation across projects

Add `-g` to share eTool across multiple projects for the current user:

```bash
npx skills add Aiid0/eTool --skill etool -g
ETOOL_SKILL="$HOME/.agents/skills/etool"
```

For either installation scope, prepare `etool.json` and a task configuration in each application project. The default `.etool` state directory is resolved relative to each project's configuration file, keeping job records and retrieved outputs with that project. To update an installed project-level or user-level Skill:

```bash
npx skills check
npx skills update etool -p
# For a user-level installation: npx skills update etool -g
```

Without a Git tag or commit, installation uses the repository's default branch. To pin a version, append an existing version tag or full commit to the repository reference. See the [Skills CLI documentation](https://github.com/vercel-labs/skills/blob/main/docs/commands.md) for the syntax.

## Capabilities and modules

| Module | Responsibility | Current implementation |
| --- | --- | --- |
| Configuration and target checks | Parse connection and task configurations; check the base environment and declared task requirements | [config.py](etool/config.py), client and remote `doctor` |
| Local packaging | Build an image or use an existing one, run an optional check, and export an archive | `pack` in [client.py](etool/client.py) |
| Transfer and execution | Upload and load the image, then start a background container | [transport.py](etool/transport.py), [remote_agent.py](etool/remote_agent.py) |
| Run management | Status, logs, waiting, cancellation, and recovery from interruptions | Client and remote management interfaces |
| Output and resource management | Retrieve logs and outputs, clean resources after verifying retrieval, and support explicit discarding | Client and remote retrieval and cleanup interfaces |

[cli.py](etool/cli.py) provides commands, progress reporting, JSON output, and exit codes. Each run has a unique job ID and persistent records. The background container runs independently of your local terminal.

## Runtime requirements

eTool executes tasks using Docker and SSH. Tasks without a GPU declaration run as CPU tasks: they do not check GPUs or the NVIDIA runtime, or request GPU devices. NVIDIA drivers, a GPU, and the corresponding container runtime support are required only when the task declares `requirements.gpu`.

| Location | Requirements |
| --- | --- |
| Local machine | Linux or a compatible POSIX environment with uv, a working Docker daemon, SSH, rsync, gzip, and Bash |
| Server | Linux, Python 3.9+, Docker, and rsync; the SSH user must be able to run Docker commands directly. GPU tasks additionally require NVIDIA drivers and NVIDIA Container Toolkit |

The current image identity check is not yet compatible with Docker's containerd image store and may report `IMAGE_MISMATCH` during import. This backend uses the index or manifest digest as the image ID; see the [Docker implementation](https://github.com/moby/moby/blob/master/daemon/containerd/image_inspect.go).

Locally built images must have an architecture compatible with the target server. The current build interface does not expose a cross-architecture option. See the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) to install uv, and the [NVIDIA Container Toolkit guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) to configure GPU support for containers on the server.

The eTool runtime supports Python 3.11+ and uses only the standard library. The Skill launcher declares its interpreter requirement through script metadata; each task's build definition manages the interpreter and dependencies in its image. See the [uv script environment documentation](https://docs.astral.sh/uv/guides/scripts/#declaring-script-dependencies) for the mechanism.

## Getting started

### 1. Configure the server connection

Copy the example configuration from your application project root, using the actual installation path set above for `ETOOL_SKILL`:

```bash
cp "$ETOOL_SKILL/etool.example.json" ./etool.json
```

Edit `etool.json`, replacing the example host, SSH user, and remote directory with your actual values, and adjust the port if needed. The parent of `remote_root` must already exist, and the SSH user must have permission to create and write task directories. GPU requirements belong in the task configuration; the default example requires no GPU.

Confirm SSH authentication and the server's host fingerprint first. eTool uses non-interactive SSH authentication and strict host key checking. It will not prompt for a login password or ask you to confirm a new host during execution.

The default `state_dir` is `.etool`, resolved relative to the configuration file's directory. It stores job records, image archives, logs, and retrieved outputs. Your application project manages its server configuration and job records. To keep them local, add `etool.json` and `.etool/` to that project's `.gitignore`.

### 2. Create a task from the template

Run these commands from your application project root:

```bash
mkdir -p tasks
cp -R "$ETOOL_SKILL/templates/task" tasks/my-task

# Run the task's own Python entry point first
uv run --project tasks/my-task --locked python tasks/my-task/src/main.py \
  --config tasks/my-task/config/task.json \
  --output tasks/my-task/.tmp/outputs
```

The initial template creates `tasks/my-task/.tmp/outputs/result.json` containing `{"status": "template_ready"}`. This checks configuration loading, entry point invocation, and result writing. Implement the task's computation in `run_task` in `src/task.py`.

When customizing a task, place code in `src/`, application parameters in `config/task.json`, and execution parameters in `experiment.json`. The task's `pyproject.toml`, `uv.lock`, and Dockerfile manage its dependencies and system environment. Your application project manages the task directory. See the [task template guide](templates/task/README.en.md) for details.

### 3. Check the environment and run the task

Once the server configuration and task environment are ready, run the following from your application project root:

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json doctor tasks/my-task/experiment.json
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json run tasks/my-task/experiment.json
```

`doctor PROJECT` checks local tools and Docker, then checks SSH, remote Python, Docker, disk space, and the task's declared requirements. Without a task path, `doctor` checks only the base environment. The default template has `requirements: {}`, so it triggers no GPU checks. `run` builds the image, uploads it, starts the container, waits for completion, retrieves outputs, and then cleans up this run's remote resources. With the default configuration, task results are saved in `.etool/jobs/<job_id>/remote/outputs/`, and program logs are saved in `remote/logs/container.log` within the same job directory.

The template uses a Python base image by default. Each task must provide its own dependencies and input files. Inspect the task's logs and results to confirm that it completed correctly.

## Complete workflow

First configure `etool.json` in your application project, then prepare your code, environment, and task configuration using the [official task template](templates/task/README.en.md). Run all commands below from your application project root; eTool performs server operations over SSH. Set `ETOOL_SKILL` to the actual installation directory, `TASK_CONFIG` to your task configuration path, and `JOB_ID` to the job ID printed by `pack`.

```bash
TASK_CONFIG='/path/to/experiment.json'

# 1. Check the base server environment and declared task requirements
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json doctor "$TASK_CONFIG"

# 2. Build locally, run the optional check, and export the image archive
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json pack "$TASK_CONFIG"
JOB_ID='replace-with-the-job-id-from-pack'

# 3. Upload the image and start a remote background container
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json submit "$JOB_ID"

# 4. Inspect status and logs, then wait for the container to finish
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json status "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json logs "$JOB_ID" --tail 200
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json wait "$JOB_ID"

# 5. Retrieve logs and outputs and verify the local copy; keep remote resources
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json fetch "$JOB_ID"

# 6. Remove this run's remote container, dedicated image, and task directory
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json clean "$JOB_ID"

# 7. Read the final local record; successful cleanup sets the state to cleaned
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json status "$JOB_ID"
```

If the task exits with a nonzero code, you can still use `fetch` to retrieve failure logs and partial outputs, then run `clean`.

Retrieved task outputs are stored in `<state_dir>/jobs/<job_id>/remote/outputs/`, and original program logs are stored in `remote/logs/container.log` within the same job directory. These local files remain after `clean` succeeds. Cleanup results are recorded in `cleanup.json`.

### Automatic retrieval and cleanup

| Command | Cleanup behavior |
| --- | --- |
| `run "$TASK_CONFIG"` | Create a run, package, submit, wait, retrieve, and clean automatically after retrieval is verified |
| `resume "$JOB_ID"` | Continue an existing run and clean automatically after retrieval is verified |
| `fetch "$JOB_ID" --cleanup` | Retrieve an existing run's outputs and clean immediately after verification |

Invoke the commands in the table with `uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json <command>`. Running `fetch` alone keeps remote resources; use `clean` afterward to remove them. `cancel` only stops the container, so retrieval and cleanup are still required. Local job records are retained if connection, download, or retrieval verification fails. After fixing the issue, query the actual state using the original job ID, then continue with `resume`. If a response is lost during cleanup, some or all resources may already have been removed.

Cleanup is limited to this run's Docker container, dedicated image, and remote task directory. Shared source images are preserved, and no global prune is performed. `clean` also removes the task image built locally for this run, while retaining the image archive, logs, and retrieved outputs.

## Agent integration

After loading [SKILL.md](SKILL.md), agents should obtain the actual Skill directory from their discovery information and invoke the bundled entry point from the application project directory. Use `--json` for the final JSON result; build and transfer progress is written to stderr:

```bash
# Prepare the server configuration, experiment.json, and its build context here
uv run "$ETOOL_SKILL/scripts/etool.py" \
  --config ./etool.json --json pack ./experiment.json
```

Relative paths are resolved from the current application project directory. Save the `id` returned by `pack`, then manage the run using that original job ID:

```bash
JOB_ID='replace-with-the-id-from-pack'

uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json submit "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json status "$JOB_ID"
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json logs "$JOB_ID" --tail 100

# After the container exits, retrieve outputs and clean this run's remote resources
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json --json fetch "$JOB_ID" --cleanup
```

`submit` returns after uploading and loading the image and starting the container. The task continues in the background on the server. Use the stage and `exit_code` from `status` to determine the execution result. If waiting times out, SSH disconnects, or a startup response is lost, query and recover using the original job ID. Every `run` invocation creates a new run, so use it when a new execution is intended.

## Configuration

### Tool configuration

The tool configuration is a JSON object named `etool.json` by default. It stores the server connection and local state directory. Unknown fields are rejected. See [etool.example.json](etool.example.json) for a copyable example.

| Field | Required or default | Meaning and constraints |
| --- | --- | --- |
| `host` | Required | SSH host: a simple DNS name, IPv4 address, or SSH alias |
| `user` | Required | SSH login user |
| `port` | Default `22` | Integer from 1 to 65535 |
| `remote_root` | Required | Absolute, non-root remote working directory; its parent must already exist |
| `state_dir` | Default `.etool` | Local state directory; relative paths are resolved from the configuration file's directory |

For compatibility with older configurations, `gpu_uuid` and `gpu_index` may still be provided together, but they do not implicitly enable a GPU for new tasks. New tasks must declare the device in their own `requirements.gpu`.

`host` and `user` must match `[A-Za-z0-9_][A-Za-z0-9_.-]*`. `port` does not accept booleans. `remote_root` permits only ASCII letters, digits, underscores, periods, hyphens, and slashes, and rejects `.` and `..` path segments. The remote agent rejects symbolic links in the path and an existing working root that is not a directory.

Configuration lookup follows this order: `--config PATH`, `ETOOL_CONFIG`, `etool.json` in the current directory or its parents, and finally `etool.json` in the tool installation directory. You must prepare the actual configuration file yourself; the Skill distributes only an example. `config_path` is source information added after parsing, not an input field in the configuration file.

### Task configuration

The task configuration is also a JSON object. Commands accept a configuration file path; when given a directory, they read `experiment.json` inside it by default. The [official task template](templates/task/README.en.md) provides copyable code, configuration and environment files, and setup instructions.

| Field | Defaults and conventions |
| --- | --- |
| `name` | Defaults to the configuration directory's name; use letters, digits, periods, underscores, or hyphens |
| `context` | Default `.`; build context resolved relative to the task configuration file |
| `dockerfile` | Default `Dockerfile`; resolved relative to the build context and must stay within it |
| `image` | Optional existing local image reference; mutually exclusive with an explicit `dockerfile`. Packaging builds a derived image with ownership labels and preserves the source image |
| `command` | Array of argument strings or `null`; default `null` |
| `local_check` | Optional array of local check arguments; skipped when omitted or `null` |
| `env` | Object mapping environment variable names to string values; default empty object |
| `resources` | CPU, memory, and shared memory limits; defaults are listed below |
| `requirements` | Default `{}`; host environment required by the task. Currently supports only an optional `gpu` object |

A nonempty `command` replaces the image's CMD arguments and preserves its ENTRYPOINT. If omitted, `null`, or an empty array, the image's CMD is used. Arguments are passed directly to Docker and are not interpreted as a shell string. `local_check` uses the same argument composition rules.

`local_check` runs locally using the same task image. Failure prevents packaging from completing. It requests no GPU and explicitly sets `NVIDIA_VISIBLE_DEVICES=void`. It does not inject the task's `env` or apply remote resource settings, so its coverage depends on the local environment and the command you provide.

### Optional GPU requirements

Declare the required host environment in the task's `experiment.json`. When `requirements` is omitted or `{}`, the task runs as a CPU task without checking GPUs or the NVIDIA runtime. Only `gpu` requirements are currently accepted. Prepare Python packages and system dependencies through the task's `pyproject.toml`, `uv.lock`, and Dockerfile.

For a GPU task, first query the devices using your actual host, user, and port:

```bash
ssh -p 22 taskuser@task-server \
  'nvidia-smi --query-gpu=index,uuid,name --format=csv,noheader'
```

Write the selected device's full `uuid` and physical `index` to `requirements.gpu` in the task configuration, for example:

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

This shows the `requirements` field of the task configuration, not a complete replacement for `experiment.json`. The all-zero UUID is a placeholder; replace it with an actual device UUID.

| `requirements.gpu` field | Required or default | Meaning and constraints |
| --- | --- | --- |
| `uuid` | Required | Full NVIDIA GPU UUID; the hexadecimal portion is normalized to lowercase |
| `index` | Required | Physical GPU index on the host; a nonnegative integer, excluding booleans |
| `min_free_mib` | Optional, no default threshold | Integer from 0 to 81920, excluding booleans; omitting it skips the free memory threshold check |

When present, `gpu` must be an object containing both `uuid` and `index`. The memory threshold is checked only before startup; it neither reserves memory nor limits later usage. After changing requirements, run `doctor tasks/my-task/experiment.json` to check the corresponding environment. Use the program's logs and results to confirm whether the task actually uses the GPU.

### Resources and execution conventions

| `resources` field | Default | Constraints |
| --- | --- | --- |
| `cpus` | `1` | Finite number from 0.1 to 32 |
| `memory` | `2g` | Positive integer string with an optional k/m/g suffix; 64 MiB to 512 GiB |
| `shm_size` | `256m` | Same format as `memory`; at least 1 MiB and no larger than `memory` |

Provide code, dependencies, models, and input files through the task image. The remote container uses the remote user's UID/GID, with a read-only root filesystem, networking disabled, and CPU, memory, and process count limits. Only GPU tasks request the declared device; CPU tasks request no device and explicitly set `NVIDIA_VISIBLE_DEVICES=void`. Use `/tmp` for temporary files, write outputs to retrieve to `/outputs`, and send logs to stdout/stderr. Currently, only `/outputs` is mounted automatically.

eTool manages GPU visibility variables and its reserved variables in `env`; they cannot be overridden. At startup, GPU tasks check device identity, the NVIDIA runtime, the configured free memory threshold, and device use by other eTool tasks, then verify device visibility with a probe container. GPU assignment does not provide exclusive access or a GPU memory quota. The task is responsible for checking the correctness of its computation.

## Commands

Invoke commands with `uv run "$ETOOL_SKILL/scripts/etool.py" <command>` and use `--help` for their arguments. `--config PATH` and `--json` may appear before or after the subcommand. `--version` prints the version and exits.

| Command | Purpose |
| --- | --- |
| `doctor [PROJECT]` | Check local tools, Docker, SSH, remote Python, Docker, and disk space; with a task path, also check its declared environment requirements |
| `pack PROJECT` | Build locally, run the optional check, and export the image without submitting it remotely |
| `submit JOB_ID` | Prepare, upload, load, and start a packaged task in the background |
| `status [JOB_ID]` | Query a run, or list local records when the job ID is omitted |
| `list` | List local job records |
| `logs JOB_ID [--tail N] [--follow/-f]` | Read logs; N is a nonnegative integer, default 200. Follow mode continues until the container exits |
| `wait JOB_ID [--timeout SECONDS]` | Wait for the container to finish; a local wait timeout does not stop the remote task |
| `fetch JOB_ID [--cleanup]` | Retrieve and verify outputs, optionally followed by cleanup |
| `clean JOB_ID` | Verify the local copy again, then clean remote resources |
| `cancel JOB_ID` | Stop the task while preserving logs and outputs |
| `discard JOB_ID` | Explicitly discard unretrieved resources for a stopped or unstarted task |
| `resume JOB_ID [--timeout SECONDS]` | Continue submission, waiting, retrieval, and cleanup based on the job record |
| `run PROJECT [--timeout SECONDS]` | Create a run, package, submit, wait, retrieve, and clean |

`--timeout` is a finite positive number that limits local waiting time. `run` does not call `doctor` automatically.

With `--json`, successful commands output their result, failures output `{"ok": false, "error": "..."}`, and progress is written to stderr. `logs --follow` streams text and is mutually exclusive with `--json`.

When completed normally, `run`, `resume`, and `wait` return the task's exit code. Negative values are converted to `128 + signal number`, and the final value is capped at 255. Other commands return 0 on success, 2 for handled configuration, connection, or tool errors, and 130 for a user interruption.

## Job records, recovery, and cleanup

Local records are stored in `<state_dir>/jobs/<job_id>/`, and remote task directories in `<remote_root>/jobs/<job_id>/`. `job.json` stores a configuration snapshot, image and container information, stage, exit code, and errors. `events.jsonl` records progress. Subsequent operations use the configuration snapshot and ownership token from the job record.

The main local stages are `created`, `built`, `packed`, `loaded`, `running`, `exited`, `collected`, `verified`, and `cleaned`. The remote stage is `prepared` after its directory is prepared. Explicitly discarded local records have the stage `discarded`. The stage, `exit_code`, and `last_error` represent progress, task execution result, and the most recent tool error, respectively.

Task requirements are saved in the job record. Older GPU jobs can still be recovered using their original job IDs. New task configurations no longer accept `resources.min_free_gpu_mib`; move it to `requirements.gpu.min_free_mib`.

`resume` reconciles the actual Docker state and continues the tool workflow. Containers that have already started or finished are not executed again. If the startup outcome cannot be confirmed, the tool reports `START_UNCERTAIN` and retains resources for inspection and an explicit follow-up action. The task itself must implement checkpoint recovery for its application.

Logs and outputs can still be retrieved after a task exits with a nonzero code. `clean` and `fetch --cleanup` require a verified local copy first. `discard` explicitly abandons unretrieved outputs. Cleanup checks resource ownership and confirms that the container has stopped. It removes only this run's container, dedicated image, and directory, preserving shared source images.

Interrupted cleanup is recovered using the ownership journal. When resources have already been removed, a retry reports completion only after confirming their absence. Status for cleaned runs is read from local records; at the `verified` or `cleaned` stage, logs are read preferentially from the retrieved local copy.

## Source development

To modify eTool itself, clone the repository and prepare a development environment:

```bash
git clone https://github.com/Aiid0/eTool.git
cd eTool
uv sync --locked
uv run etool --help
```

The development environment defaults to Python 3.12, as specified in `.python-version`, and is managed by the root `pyproject.toml` and `uv.lock`. To update the source and environment later, run `git pull --ff-only` and `uv sync --locked` from the repository root.

## Internal interfaces

<details>
<summary>SSH protocol, run identity, and remote operations</summary>

The client temporarily executes `python3 -c <remote_agent.py source>` over SSH, sending a single JSON request on stdin and receiving a single JSON response on stdout. No persistent service is installed remotely. Requests contain `action` and `config`; all actions except `doctor` also require `job`. `doctor` accepts task requirements in top-level `requirements`; omitting it requests only base checks.

Successful responses have the form `{"ok": true, "result": {...}}`. Failures have the form `{"ok": false, "error": {"code": "...", "message": "..."}}`; unclassified exceptions return `INTERNAL_ERROR`. This differs from the CLI's JSON output wrapper.

SSH uses non-interactive authentication and strict host key checking, with a default timeout of 300 seconds per call. A communication timeout or lost response does not prove that a remote operation was not performed or that the remote task has stopped.

| `job` identity field | Meaning |
| --- | --- |
| `id` | Run identifier generated by the client from UTC time and a random suffix |
| `token` | Random 32-character hexadecimal ownership token, reused on retries |
| `image_tag`, `container_name` | `etool-job:<job_id>` and `etool-<job_id>`, respectively |
| `archive_sha256`, `archive_bytes` | Image archive digest and byte count |
| `image_config_digest` | Docker image configuration digest |

Images and containers are associated with a run through the `etool.job` and `etool.token` labels. GPU execution containers also record `etool.gpu_uuid`; CPU containers do not use this GPU label. Ownership verification combines directory metadata, UID, job tokens, and resource labels; SSH authenticates the connection.

| `action` | Responsibility and main return values |
| --- | --- |
| `doctor` | Requires no `job`; returns UID/GID, Docker information, available disk space, and working root. Checks and returns GPU information only when `requirements.gpu` is declared |
| `prepare` | Create a dedicated directory and save ownership and resource baselines; return `directory`, `state`, and `baseline` |
| `load` | Verify the uploaded archive, load and verify the image; return `image_id` and `state` |
| `start` | Use top-level `options` to check resources and start the container according to recorded requirements; GPU tasks add device checks and a probe. Reconcile state only for containers that have already started |
| `status` | Reconcile Docker state with records and return common status fields; return `NOT_FOUND` for an unknown directory |
| `logs` | Return `text`, `container_state`, and `state`; top-level `tail` defaults to 200 and accepts 0–100000, with `null` or -1 meaning all lines |
| `collect` | Require an exited or dead container; freeze complete logs and outputs, reusing them on repeated calls; return archive and manifest digests, byte counts, exit code, and stage |
| `acknowledge` | Require top-level `archive_sha256` and `manifest_sha256` to match the frozen results; return `state=verified` and `exit_code` |
| `cleanup` | Require verified results or resume cleanup that has already started; reject active containers and return cleanup checks |
| `cancel` | Stop this run's active container, preserve logs and outputs, and return common status fields |
| `discard` | Allow abandonment of unretrieved outputs while still checking ownership and stopped state; return remote cleanup results and set the local record to `discarded` |

`options` combines the task's `command`, `env`, `requirements`, and expanded `resources`. Common status fields are `state`, `container_id`, `container_state`, `exit_code`, `gpu`, `directory`, and `image_id`. Values not yet available may be `null`; `gpu` is `null` for CPU tasks. The remote `container_state` is a Docker State object.

Retrieved files are `results.tar.gz` and `RESULTS.json`. Both task types collect program outputs, `logs/container.log`, and `logs/container-inspect.json`. GPU tasks additionally require `gpu-host.json` and `logs/gpu-probe.log`; CPU tasks do not require these GPU evidence files. The manifest contains `schema_version`, `job_id`, and `files`, with `path`, `size`, and `sha256` for each entry. After verification, the client safely extracts and publishes the results to the local `remote/` directory, rejecting paths outside the destination, links, special files, and archive members absent from the manifest. It checks the local copy again before normal cleanup.

Cleanup results include `state=cleaned`, `container_absent`, `image_tag_absent`, `image_id_absent`, `preexisting_image_preserved`, `task_directory_absent`, `baseline`, and `after`. Existing shared images may be preserved; `image_id_absent=false` alone does not indicate failure. Changes to the overall resource set do not replace ownership checks for this run. The tool removes only empty working directories that it created, preserves pre-existing directories, and performs no global prune.

</details>
