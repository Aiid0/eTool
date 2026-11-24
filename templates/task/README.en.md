# Official task template

[简体中文](README.md) | **English**

This directory is installed with the eTool Skill. Copy it to create your own task, then update the code, configuration, and runtime environment in the locations described below. First follow the [installation instructions](https://github.com/Aiid0/eTool/blob/main/README.en.md#install-as-a-skill) to set `ETOOL_SKILL` to the actual installation directory. Run all commands below from the root of your application project.

## Create a task directory

Replace `my-task` with the name of your task directory:

```bash
mkdir -p tasks
cp -R "$ETOOL_SKILL/templates/task" tasks/my-task
```

Tasks can also live in another project directory; pass the configuration path in that directory when submitting a task. Make day-to-day changes in the copied task directory.

```text
tasks/my-task/
├── src/
│   ├── main.py          # Argument parsing, configuration loading, logging, and result writing
│   └── task.py          # Implement task logic in run_task
├── config/task.json     # Task-specific parameters
├── experiment.json      # eTool execution parameters, environment requirements, resources, and local check
├── Dockerfile           # Base image and system dependencies
├── pyproject.toml       # Task Python dependencies
├── uv.lock              # Task dependency lockfile
├── .python-version      # Local task interpreter version
├── .dockerignore        # Files allowed in the image build context
├── README.md
└── README.en.md
```

## Update code and configuration

Implement task logic in `run_task(config, output_dir)` in `src/task.py`, and put other task modules in `src/`. The function receives the JSON object from `config/task.json` and an output directory, and returns a JSON-serializable dictionary. `main.py` writes the return value to `result.json` in the output directory; task code writes any other artifacts to `output_dir`.

The initial entry point returns `{"status": "template_ready"}` to confirm that configuration loading, entry point invocation, and result writing work together. After replacing it with task logic, the task itself must also determine whether its results are correct.

The three configuration files serve different purposes:

| File | Contents |
| --- | --- |
| Task `config/task.json` | Task-specific settings such as models, inputs, and algorithm parameters; initially `{"parameters": {}}` |
| Task `experiment.json` | Task name, image source, startup arguments, host environment requirements, resources, and optional local check |
| Application project `etool.json` | Server connection, SSH, and local state directory |

The template image fixes its ENTRYPOINT to Python and `src/main.py`, so `command` and `local_check` in `experiment.json` contain only arguments such as `--config` and `--output`; do not repeat Python or the script path. This follows Docker's [ENTRYPOINT/CMD rules](https://docs.docker.com/reference/dockerfile/#entrypoint).

## Packaging paths and container paths

The packaging command accepts a task configuration file path. The template's `experiment.json` sets `"context": "."`, making the configuration file's directory the build context. A relative `context` is resolved from the configuration file's location, and `dockerfile` is resolved from the build context. Both can also be absolute paths, but the Dockerfile must be inside the build context.

For a copied task in `tasks/my-task/`, the paths are:

| Purpose | Local path | Path inside the image |
| --- | --- | --- |
| Build context | `tasks/my-task/` | Directory Docker can read during the build |
| Task code | `tasks/my-task/src/` | `/app/src/` |
| Task-specific configuration | `tasks/my-task/config/` | `/app/config/` |
| Program entry point | `tasks/my-task/src/main.py` | `/app/src/main.py` |

Dockerfile `COPY` instructions determine where code and configuration are copied, and `ENTRYPOINT` determines the entry point. `command` in `experiment.json` uses container paths; `context` and `dockerfile` use local paths. The application project's `etool.json` manages the server connection. Declare task GPU requirements in `experiment.json`; the server configuration does not define task code locations.

To include other code, data, or model directories, put them in the build context and update both the Dockerfile's `COPY` instructions and the allowed paths in `.dockerignore`. See Docker's [build context documentation](https://docs.docker.com/build/concepts/context/) for path rules.

## Prepare the runtime environment

The default Dockerfile uses `python:3.12-slim-bookworm` and copies uv from `ghcr.io/astral-sh/uv:0.12.5`. The first build needs to download both base images. Third-party Python dependencies are installed from the task's own `pyproject.toml` and `uv.lock`; the initial dependency list is empty. eTool's runtime environment and the task image are managed separately.

From the application project root, use `uv add --project tasks/my-task PACKAGE` to add task dependencies and `uv remove --project tasks/my-task PACKAGE` to remove them. After editing dependency declarations manually, run `uv lock --project tasks/my-task` and `uv sync --project tasks/my-task --locked`. Rebuild the image after changing dependencies. The Dockerfile runs `uv sync --locked --no-dev --no-cache` during the build, and the container directly uses the image's Python environment at runtime. See [uv's Docker integration guide](https://docs.astral.sh/uv/guides/integration/docker/) for details.

For a GPU framework, first determine the framework version and its Python, CUDA, and driver requirements, then update the task's environment files. For PyTorch, select the CUDA build and dependency source using the [official installation options](https://pytorch.org/get-started/locally/). Check server driver compatibility against [NVIDIA's CUDA compatibility documentation](https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html). If the task needs a CUDA compiler toolchain or other system libraries, choose a suitable base environment in the Dockerfile and install the required packages. When changing the Python version, update the Dockerfile, `pyproject.toml`, and `.python-version` together, then update the lockfile.

At runtime, the container has networking disabled and a read-only root filesystem, and runs with the remote user's UID/GID. Prepare Python dependencies, model weights, and input data in advance; the current task interface automatically mounts only `/outputs`. For files that must be included in the image, update both the Dockerfile and the allowed paths in `.dockerignore`. Write temporary caches to `/tmp` and artifacts to retrieve to `/outputs`.

## Declare host environment requirements

The template's `experiment.json` defaults to `"requirements": {}`. It runs as a CPU task without checking GPUs or the NVIDIA runtime, or requesting GPU devices. Set CPU, memory, and shared memory limits through `resources.cpus`, `resources.memory`, and `resources.shm_size`.

For a GPU task, replace `requirements` in the task's `experiment.json` with the following object, leaving the other fields configured for your task:

```json
{
  "gpu": {
    "uuid": "GPU-00000000-0000-0000-0000-000000000000",
    "index": 0,
    "min_free_mib": 2048
  }
}
```

Provide both the full GPU UUID and its physical index on the host. The all-zero UUID is only a placeholder. `min_free_mib` is optional: omitting it skips the free memory threshold check; when provided, it must be an integer from 0 to 81920. See [GPU requirements](https://github.com/Aiid0/eTool/blob/main/README.en.md#optional-gpu-requirements) for device queries and field constraints. Device selection and the memory threshold belong to the task and are not implicitly inherited from the server configuration.

Currently, `requirements` supports only an optional `gpu` object. Prepare Python packages, system libraries, models, and data through the task image as described above. At startup, GPU tasks check device identity, the NVIDIA runtime, the configured memory threshold, and use by other eTool tasks, then verify device visibility in a probe container. These checks do not guarantee exclusive GPU access or correct task computation.

## Run locally first

Run the entry point in the task's own uv environment:

```bash
uv sync --project tasks/my-task --locked
uv run --project tasks/my-task python tasks/my-task/src/main.py \
  --config tasks/my-task/config/task.json \
  --output tasks/my-task/.tmp/outputs
```

After the program exits successfully, inspect `tasks/my-task/.tmp/outputs/result.json`. Its initial contents should include `"status": "template_ready"`.

Then build and run the task image:

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

This command uses the image's default configuration and `/outputs` output location. Results are still written to the local `tasks/my-task/.tmp/outputs/` directory. This step checks that dependencies, configuration paths, file permissions, and the entry point work inside the container.

The template's `local_check` also invokes the same task entry point. After changing the task to require a GPU, configure a check command that can run locally, or remove `local_check`. eTool's local check requests no GPU and explicitly sets `NVIDIA_VISIBLE_DEVICES=void`; it does not inject `env` from `experiment.json` or apply remote resource settings. Even after the local check passes, verify the task's results on the target server.

## Package and execute with eTool

Run from the application project root, using that project's server configuration:

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json pack tasks/my-task/experiment.json
```

`pack` builds the image locally, runs the configured local check, exports an archive, and prints the job ID when complete. Local check output and build logs are saved under `.etool/jobs/<job_id>/`.

Once the task logic and environment are ready, check the server and execute the complete workflow:

```bash
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json doctor tasks/my-task/experiment.json
uv run "$ETOOL_SKILL/scripts/etool.py" --config ./etool.json run tasks/my-task/experiment.json
```

`doctor PROJECT` checks the base environment and declared task requirements. Without a task path, it checks only base tools, SSH, remote Python, Docker, and disk space. `run` does not call `doctor` automatically, but startup still checks declared GPU requirements.

`run` creates a new job record, builds and uploads the image, executes the task, retrieves logs and artifacts, and cleans up remote resources. Retrieved task artifacts are saved under `.etool/jobs/<job_id>/remote/outputs/`. To submit in the background, inspect, or retrieve a job using the ID from an earlier `pack`, see the [command reference](https://github.com/Aiid0/eTool/blob/main/README.en.md#commands).
