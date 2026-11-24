"""Validated server configuration and task image descriptions."""

import json
import math
import os
from pathlib import Path, PurePosixPath
import re


DEFAULTS = {
    "port": 22,
    "state_dir": ".etool",
}
REQUIRED_CONFIG_FIELDS = ("host", "user", "remote_root")
LEGACY_GPU_FIELDS = ("gpu_uuid", "gpu_index")
CONFIG_FIELDS = frozenset((*REQUIRED_CONFIG_FIELDS, *DEFAULTS, *LEGACY_GPU_FIELDS))
RESOURCE_DEFAULTS = {
    "cpus": 1,
    "memory": "2g",
    "shm_size": "256m",
}


class ConfigError(ValueError):
    """A server or task configuration is invalid."""


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"Cannot read JSON configuration {path}: {error}") from error
    if not isinstance(value, dict):
        raise ConfigError(f"Configuration must be a JSON object: {path}")
    return value


def _unknown(value, allowed, section):
    extra = set(value) - set(allowed)
    if extra:
        raise ConfigError(f"Unknown {section} fields: {', '.join(sorted(extra))}")


def _default_config_path():
    configured = os.environ.get("ETOOL_CONFIG")
    if configured:
        return Path(configured).expanduser()
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / "etool.json"
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[1] / "etool.json"


def load_config(path=None):
    """Read a server configuration; relative state paths belong to that file."""
    source = Path(path).expanduser() if path is not None else _default_config_path()
    source = source.resolve()
    value = _read_json(source)
    _unknown(value, CONFIG_FIELDS, "server configuration")
    missing = [field for field in REQUIRED_CONFIG_FIELDS if field not in value]
    if missing:
        raise ConfigError(f"Missing required server configuration fields: {', '.join(missing)}")
    config = {**DEFAULTS, **value}
    for field in ("host", "user"):
        item = config[field]
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", item):
            raise ConfigError(f"{field} must start with a letter, digit or underscore and contain "
                              "only letters, digits, underscores, periods or hyphens")
    port = config["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError("port must be an integer between 1 and 65535")
    if any(field in config for field in LEGACY_GPU_FIELDS):
        if not all(field in config for field in LEGACY_GPU_FIELDS):
            raise ConfigError("Legacy gpu_uuid and gpu_index must be provided together")
        gpu = _requirements({"gpu": {"uuid": config["gpu_uuid"], "index": config["gpu_index"]}})["gpu"]
        config["gpu_uuid"] = gpu["uuid"]
    root = config["remote_root"]
    if not isinstance(root, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", root):
        raise ConfigError("remote_root must be an absolute path using simple path characters")
    if any(part in (".", "..") for part in root.split("/")):
        raise ConfigError("remote_root cannot contain '.' or '..' segments")
    remote_root = PurePosixPath("/" + root.lstrip("/"))
    if remote_root == PurePosixPath("/"):
        raise ConfigError("remote_root must be a dedicated directory, not the filesystem root")
    config["remote_root"] = remote_root.as_posix()
    state = config["state_dir"]
    if not isinstance(state, str) or not state.strip() or "\0" in state:
        raise ConfigError("state_dir must be a nonempty local path")
    state_path = Path(state).expanduser()
    if not state_path.is_absolute():
        state_path = source.parent / state_path
    config["state_dir"] = str(state_path.resolve())
    config["config_path"] = str(source)
    return config


def _arguments(value, field):
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(arg, str) or "\0" in arg for arg in value):
        raise ConfigError(f"{field} must be an array of argument strings, or null")
    return list(value)


def _memory_bytes(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*[kmg]?", value.lower()):
        raise ConfigError(f"{field} must be a positive Docker memory value, such as '512m' or '2g'")
    suffix = value[-1].lower()
    if suffix.isdigit():
        return int(value)
    return int(value[:-1]) * {"k": 1024, "m": 1024**2, "g": 1024**3}[suffix]


def _resources(value):
    if not isinstance(value, dict):
        raise ConfigError("resources must be an object")
    _unknown(value, RESOURCE_DEFAULTS, "resources")
    resources = {**RESOURCE_DEFAULTS, **value}
    cpus = resources["cpus"]
    if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or not math.isfinite(cpus) or not 0.1 <= cpus <= 32:
        raise ConfigError("resources.cpus must be a finite number between 0.1 and 32")
    memory = _memory_bytes(resources["memory"], "resources.memory")
    shm = _memory_bytes(resources["shm_size"], "resources.shm_size")
    if not 64 * 1024**2 <= memory <= 512 * 1024**3:
        raise ConfigError("resources.memory must be between 64m and 512g")
    if not 1024**2 <= shm <= memory:
        raise ConfigError("resources.shm_size must be at least 1m and no larger than resources.memory")
    resources["memory"] = resources["memory"].lower()
    resources["shm_size"] = resources["shm_size"].lower()
    return resources


def _requirements(value):
    if not isinstance(value, dict):
        raise ConfigError("requirements must be an object")
    _unknown(value, {"gpu"}, "requirements")
    if "gpu" not in value:
        return {}
    gpu = value["gpu"]
    if not isinstance(gpu, dict):
        raise ConfigError("requirements.gpu must be an object")
    _unknown(gpu, {"uuid", "index", "min_free_mib"}, "requirements.gpu")
    uuid = gpu.get("uuid")
    if not isinstance(uuid, str) or not re.fullmatch(
        r"GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", uuid
    ):
        raise ConfigError("requirements.gpu.uuid must be a full NVIDIA GPU UUID")
    if type(gpu.get("index")) is not int or gpu["index"] < 0:
        raise ConfigError("requirements.gpu.index must be a nonnegative integer")
    result = {"uuid": "GPU-" + uuid[4:].lower(), "index": gpu["index"]}
    if "min_free_mib" in gpu:
        free = gpu["min_free_mib"]
        if type(free) is not int or not 0 <= free <= 81920:
            raise ConfigError("requirements.gpu.min_free_mib must be an integer between 0 and 81920")
        result["min_free_mib"] = free
    return {"gpu": result}


def _environment(value):
    if not isinstance(value, dict):
        raise ConfigError("env must be an object of string values")
    env = {}
    for name, item in value.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ConfigError(f"Invalid environment variable name: {name!r}")
        if name.startswith(("NVIDIA_", "CUDA_VISIBLE", "ETOOL_")) or name == "CUDA_DEVICE_ORDER":
            raise ConfigError(f"env.{name} is controlled by eTool and cannot be overridden")
        if not isinstance(item, str) or "\0" in item:
            raise ConfigError(f"env.{name} must be a string without NUL characters")
        env[name] = item
    return env


def load_project(path):
    """Normalize a task description without building or running anything."""
    source = Path(path).expanduser()
    if source.is_dir():
        source = source / "experiment.json"
    source = source.resolve()
    value = _read_json(source)
    _unknown(value, {"name", "context", "dockerfile", "image", "command", "local_check", "resources", "env", "requirements"}, "experiment")
    name = value.get("name", source.parent.name)
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise ConfigError("name must contain 1-64 letters, digits, periods, underscores or hyphens")
    context_value = value.get("context", ".")
    if not isinstance(context_value, str) or not context_value or "\0" in context_value:
        raise ConfigError("context must be a directory path")
    context = (source.parent / Path(context_value).expanduser()).resolve()
    if not context.is_dir():
        raise ConfigError(f"Build context directory does not exist: {context}")
    image = value.get("image")
    if image is not None and (not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*", image)):
        raise ConfigError("image must be a Docker image reference")
    dockerfile = None
    if image is None:
        dockerfile_value = value.get("dockerfile", "Dockerfile")
        if not isinstance(dockerfile_value, str) or not dockerfile_value or "\0" in dockerfile_value:
            raise ConfigError("dockerfile must be a file path inside the context")
        dockerfile_path = (context / dockerfile_value).resolve()
        if not dockerfile_path.is_relative_to(context):
            raise ConfigError("dockerfile must stay inside the build context")
        if not dockerfile_path.is_file():
            raise ConfigError(f"Dockerfile does not exist: {dockerfile_path}")
        dockerfile = dockerfile_path.relative_to(context).as_posix()
    elif "dockerfile" in value:
        raise ConfigError("Use image or dockerfile, not both")
    command = _arguments(value.get("command"), "command")
    local_check = _arguments(value.get("local_check"), "local_check")
    env = _environment(value.get("env", {}))
    resource_values = value.get("resources", {})
    if isinstance(resource_values, dict) and "min_free_gpu_mib" in resource_values:
        raise ConfigError("Move resources.min_free_gpu_mib to requirements.gpu.min_free_mib; "
                          "declare the GPU uuid and index in requirements.gpu")
    resources = _resources(resource_values)
    requirements = _requirements(value.get("requirements", {}))
    return {
        "name": name,
        "project_file": str(source),
        "context": str(context),
        "dockerfile": dockerfile,
        "image": image,
        "command": command,
        "local_check": local_check,
        "env": env,
        "resources": resources,
        "requirements": requirements,
        "options": {"command": command, "env": env, **resources, "requirements": requirements},
    }
