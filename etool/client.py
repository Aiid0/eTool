"""Durable task lifecycle and verified artifact collection."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import tarfile
import tempfile
import time

from .config import load_project
from .transport import Transport, TransportError


class ToolError(RuntimeError):
    pass


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    with temp.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or "\0" in name:
        raise ToolError("Invalid result path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ToolError(f"Unsafe result path: {name!r}")
    return path.as_posix()


def verify_and_extract(archive, manifest, destination, expected_job_id):
    """Validate every tar member and hash before publishing an extraction."""
    destination = Path(destination)
    try:
        if manifest.get("schema_version") != 1 or manifest.get("job_id") != expected_job_id:
            raise ToolError("Result manifest belongs to another task or unsupported schema")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ToolError("Invalid manifest files list")
        expected = {}
        for entry in entries:
            name = safe_name(entry["path"])
            if name in expected or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ToolError("Duplicate result path or invalid SHA-256")
            if type(entry["size"]) is not int or entry["size"] < 0:
                raise ToolError("Invalid result size")
            expected[name] = entry
        if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
            raise ToolError("Extraction destination must be an empty ordinary directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=destination.parent) as temporary:
            stage = Path(temporary) / "payload"
            stage.mkdir()
            with tarfile.open(archive, "r:gz") as bundle:
                seen = set()
                payload = {}
                for member in bundle.getmembers():
                    name = safe_name(member.name.rstrip("/") if member.isdir() else member.name)
                    if name in seen:
                        raise ToolError(f"Duplicate archive member: {name}")
                    seen.add(name)
                    if member.isdir():
                        continue
                    if not member.isfile() or member.issym() or member.islnk():
                        raise ToolError(f"Special archive member rejected: {name}")
                    if name not in expected or member.size != expected[name]["size"]:
                        raise ToolError(f"Unexpected archive member or size: {name}")
                    payload[name] = member
                if set(payload) != set(expected):
                    raise ToolError("Archive and manifest file lists differ")
                for name, member in payload.items():
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = bundle.extractfile(member)
                    if stream is None:
                        raise ToolError(f"Cannot read archive member: {name}")
                    with stream, target.open("xb") as output:
                        shutil.copyfileobj(stream, output)
                    if digest(target) != expected[name]["sha256"]:
                        raise ToolError(f"Result checksum mismatch: {name}")
            if destination.exists():
                destination.rmdir()
            stage.replace(destination)
        return {"files": list(expected.values()), "file_count": len(expected)}
    except ToolError:
        raise
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, AttributeError) as exc:
        raise ToolError(f"Result verification failed: {exc}") from exc


class Client:
    def __init__(self, config, progress=None):
        self.config = config
        self.progress = progress or (lambda message: None)
        self.transport = Transport(config, progress=self.progress)
        self.jobs = Path(config["state_dir"]) / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{7,90}", job_id):
            raise ToolError("Invalid task ID")
        path = self.jobs / job_id
        if path.is_symlink():
            raise ToolError("Task directory cannot be a symlink")
        return path

    def _read(self, job_id):
        path = self._path(job_id)
        try:
            value = json.loads((path / "job.json").read_text())
        except (OSError, ValueError) as exc:
            raise ToolError(f"Task not found or invalid: {job_id}") from exc
        if value.get("id") != job_id:
            raise ToolError("Task metadata identity mismatch")
        return value

    def _save(self, job, state=None, **fields):
        if state:
            job["state"] = state
        job.update(fields)
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(self._path(job["id"]) / "job.json", job)
        return job

    def _emit(self, job, message):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        event = {"at": stamp, "message": message, "state": job["state"]}
        with (self._path(job["id"]) / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.progress(f"[{job['id']}] {message}")

    @contextmanager
    def _lock(self, job_id):
        directory = self._path(job_id)
        if not directory.is_dir():
            raise ToolError(f"Unknown task: {job_id}")
        with (directory / ".client.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ToolError("Another local operation is using this task") from exc
            yield

    def _remote(self, job, action, **extra):
        work = self._path(job["id"])
        try:
            transport = Transport(job["config"], progress=self.progress)
            return transport.remote(action, job=job, log=work / "logs" / f"{action}.log", **extra)
        except TransportError as exc:
            latest = self._read(job["id"])
            self._save(latest, last_error={"code": exc.code, "message": str(exc), "action": action})
            raise ToolError(f"{action}: {exc} (task retained; use resume {job['id']})") from exc

    def doctor(self, project_path=None):
        requirements = load_project(project_path)["requirements"] if project_path is not None else {}
        for binary in ("docker", "ssh", "rsync", "gzip"):
            if not shutil.which(binary):
                raise ToolError(f"Missing local dependency: {binary}")
        self.transport.run(["docker", "version", "--format", "{{.Server.Version}}"])
        try:
            return self.transport.remote("doctor", requirements=requirements)
        except TransportError as exc:
            raise ToolError(str(exc)) from exc

    def pack(self, project_path):
        project = load_project(project_path)
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        work = self._path(job_id)
        work.mkdir()
        (work / "logs").mkdir()
        job = {"schema_version": 1, "id": job_id, "name": project["name"],
               "token": secrets.token_hex(16), "image_tag": "etool-job:" + job_id,
               "container_name": "etool-" + job_id, "project": project,
               "config": self.config, "local_dir": str(work), "state": "created",
               "created_at": datetime.now(timezone.utc).isoformat()}
        self._save(job)
        with self._lock(job_id):
            try:
                self._pack(job)
            except (TransportError, ToolError, OSError) as exc:
                self._save(job, last_error={"code": "PACK_FAILED", "message": str(exc)})
                raise ToolError(f"Packaging failed; task {job_id} can be inspected or resumed: {exc}") from exc
        return job

    def _pack(self, job):
        work = self._path(job["id"])
        project = job["project"]
        labels = ["--label", f"etool.job={job['id']}", "--label", f"etool.token={job['token']}"]
        self._emit(job, "构建任务专属 Docker 镜像")
        build = ["docker", "build", "--pull=false", *labels, "-t", job["image_tag"]]
        if project.get("image"):
            build_context = work / "image-context"
            build_context.mkdir(exist_ok=True)
            (build_context / "Dockerfile").write_text("FROM " + project["image"] + "\n")
            build += [str(build_context)]
        else:
            build += ["-f", str(Path(project["context"]) / project["dockerfile"]), project["context"]]
        self.transport.run(build, log=work / "logs/build.log")
        self._save(job, "built")
        if project.get("local_check") is not None:
            self._emit(job, "在本机容器中执行 local_check")
            outputs = work / "local-check"
            outputs.mkdir(exist_ok=True)
            self.transport.run(["docker", "run", "--rm", "--network=none", "--read-only",
                                "--env", "NVIDIA_VISIBLE_DEVICES=void",
                                "--tmpfs", "/tmp:rw,size=64m", "--user", f"{os.getuid()}:{os.getgid()}",
                                "--mount", f"type=bind,src={outputs},dst=/outputs", job["image_tag"],
                                *project["local_check"]], log=work / "logs/local-check.log")
        self._emit(job, "导出并压缩镜像；日志与归档实时写入本地任务目录")
        archive = work / "image.tar.gz"
        self.transport.run(["bash", "-o", "pipefail", "-c",
                            'docker image save "$1" | gzip -1 > "$2"', "etool-pack",
                            job["image_tag"], str(archive)], log=work / "logs/pack.log")
        with tarfile.open(archive, "r:gz") as bundle:
            manifest = json.load(bundle.extractfile("manifest.json"))
            if len(manifest) != 1:
                raise ToolError("Expected a single task image in Docker archive")
            config = bundle.extractfile(manifest[0]["Config"])
            if config is None:
                raise ToolError("Docker archive is missing image config")
            config_digest = "sha256:" + hashlib.sha256(config.read()).hexdigest()
        self._save(job, "packed", archive_sha256=digest(archive), archive_bytes=archive.stat().st_size,
                   image_config_digest=config_digest, last_error=None)
        self._emit(job, f"打包完成：{archive.stat().st_size / 1024**2:.1f} MiB")

    def submit(self, job_id):
        with self._lock(job_id):
            job = self._read(job_id)
            self._submit(job)
            return job

    def _submit(self, job):
        if job["state"] == "cleaned":
            return job
        if job["state"] == "discarded":
            raise ToolError("This task was discarded; create a new task")
        work = self._path(job["id"])
        if job["state"] in ("created", "built"):
            self._pack(job)
        archive = work / "image.tar.gz"
        if not archive.is_file() or digest(archive) != job.get("archive_sha256"):
            raise ToolError("Local image archive is missing or changed; remote is preserved")
        self._emit(job, "准备远端专属目录并核对任务归属")
        prepared = self._remote(job, "prepare")
        directory = prepared["directory"]
        self._save(job, remote_directory=directory, baseline=prepared.get("baseline"))
        state = self._remote(job, "status")
        if state["state"] == "prepared":
            self._emit(job, "上传镜像（支持断点续传）")
            Transport(job["config"], progress=self.progress).upload(archive, directory, work / "logs/upload.log")
            loaded = self._remote(job, "load")
            self._save(job, "loaded", remote_image_id=loaded.get("image_id"))
        requirements = job["project"].get("requirements")
        gpu = requirements.get("gpu") if requirements is not None else None
        if gpu is None and requirements is None and "gpu_uuid" in job["config"]:
            gpu = {"index": job["config"]["gpu_index"]}
        if gpu is not None:
            self._emit(job, f"核对任务要求的物理 GPU {gpu['index']}，提交独立后台容器")
        else:
            self._emit(job, "提交独立后台容器")
        started = self._remote(job, "start", options=job["project"]["options"])
        self._save(job, started["state"], container_id=started.get("container_id"),
                   gpu=started.get("gpu"), exit_code=started.get("exit_code"), last_error=None)
        self._emit(job, "任务已提交；本地可退出，使用 status/logs/fetch/resume 继续")

    def status(self, job_id):
        job = self._read(job_id)
        if job["state"] == "cleaned":
            return job
        if not job.get("remote_directory"):
            return job
        remote_state = self._remote(job, "status")
        # Local verified/collected receipts remain valid when observing exited containers.
        state = job["state"] if job["state"] in ("verified", "collected") else remote_state["state"]
        # Inspection is read-only locally: it must not overwrite a concurrent fetch/cleanup.
        return {**job, "state": state, "container_state": remote_state.get("container_state"),
                "exit_code": remote_state.get("exit_code"), "gpu": remote_state.get("gpu", job.get("gpu"))}

    def logs(self, job_id, tail=200):
        job = self._read(job_id)
        local = self._path(job_id) / "remote/logs/container.log"
        if job["state"] in ("verified", "cleaned") and local.exists():
            text = local.read_text(errors="replace")
            if tail >= 0:
                text = "\n".join(text.splitlines()[-tail:]) + "\n" if tail else ""
            return {"text": text, "state": job["state"], "container_state": "exited"}
        return self._remote(job, "logs", tail=tail)

    def wait(self, job_id, timeout=None):
        start = last_notice = time.monotonic()
        while True:
            job = self.status(job_id)
            if job["state"] in ("exited", "collected", "verified", "cleaned"):
                return job
            if job["state"] not in ("running", "created", "loaded"):
                raise ToolError(f"Task is not running: {job['state']}")
            if timeout is not None and time.monotonic() - start >= timeout:
                raise ToolError(f"Wait timeout; remote task continues. Use resume {job_id}")
            if time.monotonic() - last_notice >= 10:
                self._emit(job, f"远端仍在运行，已等待 {time.monotonic() - start:.0f}s")
                last_notice = time.monotonic()
            time.sleep(1)

    def fetch(self, job_id, cleanup=False):
        with self._lock(job_id):
            job = self._read(job_id)
            if job["state"] != "cleaned":
                self._fetch(job)
                if cleanup:
                    self._clean(job)
            return job

    def _fetch(self, job):
        work = self._path(job["id"])
        self._emit(job, "在远端冻结结果和完整容器日志")
        collected = self._remote(job, "collect")
        self._save(job, "collected", result_archive_sha256=collected["archive_sha256"],
                   result_manifest_sha256=collected["manifest_sha256"], exit_code=collected.get("exit_code"))
        staging = work / "download"
        staging.mkdir(exist_ok=True)
        for filename in ("RESULTS.json", "results.tar.gz"):
            Transport(job["config"], progress=self.progress).download(
                job["remote_directory"], filename, staging / filename,
                work / "logs" / ("fetch-" + filename + ".log"))
        if digest(staging / "RESULTS.json") != collected["manifest_sha256"]:
            raise ToolError("Downloaded result manifest checksum mismatch; remote data preserved")
        if digest(staging / "results.tar.gz") != collected["archive_sha256"]:
            raise ToolError("Downloaded result archive checksum mismatch; remote data preserved")
        manifest = json.loads((staging / "RESULTS.json").read_text())
        stage = work / (".receipt-" + secrets.token_hex(4))
        self._emit(job, "核对全部结果文件 SHA-256，并安全解包")
        verified = verify_and_extract(staging / "results.tar.gz", manifest, stage, job["id"])
        shutil.copyfile(staging / "RESULTS.json", stage / "RESULTS.json")
        receipt = work / "remote"
        old = work / ".receipt-previous"
        if old.exists():
            shutil.rmtree(old)
        if receipt.is_symlink():
            raise ToolError("Receipt directory cannot be a symlink")
        if receipt.exists():
            receipt.replace(old)
        try:
            stage.replace(receipt)
        except OSError:
            if old.exists():
                old.replace(receipt)
            raise
        if old.exists():
            shutil.rmtree(old)
        atomic_json(work / "verification.json", {"status": "verified", "job_id": job["id"],
                    "manifest_sha256": collected["manifest_sha256"],
                    "archive_sha256": collected["archive_sha256"], **verified})
        self._verify_receipt(job)
        self._remote(job, "acknowledge", manifest_sha256=collected["manifest_sha256"],
                     archive_sha256=collected["archive_sha256"])
        self._save(job, "verified", verified_file_count=verified["file_count"], last_error=None)
        self._emit(job, f"回收校验通过：{verified['file_count']} 个文件，原始日志已保存")

    def _verify_receipt(self, job):
        work = self._path(job["id"])
        receipt = work / "remote"
        try:
            archive = work / "download/results.tar.gz"
            if digest(archive) != job["result_archive_sha256"]:
                raise ToolError("Local result archive changed; fetch again before cleanup")
            if receipt.is_symlink() or digest(receipt / "RESULTS.json") != job["result_manifest_sha256"]:
                raise ToolError("Local receipt manifest was changed; cleanup refused")
            manifest = json.loads((receipt / "RESULTS.json").read_text())
            if manifest["job_id"] != job["id"]:
                raise ToolError("Local receipt belongs to another task")
            for entry in manifest["files"]:
                name = safe_name(entry["path"])
                path = receipt / name
                if path.is_symlink() or not path.resolve().is_relative_to(receipt.resolve()):
                    raise ToolError("Local receipt contains unsafe paths")
                if path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
                    raise ToolError(f"Local receipt changed: {name}; fetch again before cleanup")
        except (OSError, ValueError, KeyError) as exc:
            raise ToolError(f"Cannot verify local receipt; cleanup refused: {exc}") from exc

    def clean(self, job_id):
        with self._lock(job_id):
            job = self._read(job_id)
            self._clean(job)
            return job

    def _clean(self, job):
        self._verify_receipt(job)
        self._emit(job, "本地结果仍完整；精确清理本次远端容器、镜像和目录")
        result = self._remote(job, "cleanup")
        work = self._path(job["id"])
        if job.get("cleanup", {}).get("baseline") and not result.get("baseline"):
            result = {**job["cleanup"], "last_check": result}
        atomic_json(work / "cleanup.json", result)
        if not all(result.get(key) for key in ("container_absent", "image_tag_absent", "task_directory_absent")):
            raise ToolError("Remote cleanup did not pass all absence checks; see cleanup.json")
        self._remove_local_image(job)
        self._save(job, "cleaned", cleanup=result, last_error=None)
        self._emit(job, "清理检查通过；本地保留镜像归档、结果、原始日志和验收记录")

    def _remove_local_image(self, job):
        work = self._path(job["id"])
        # The locally built derivative carries a task token. Never remove a source image.
        try:
            image = json.loads(self.transport.run(["docker", "image", "inspect", job["image_tag"]]))[0]
        except TransportError:
            image = None
        if image:
            labels = image.get("Config", {}).get("Labels", {}) or {}
            if labels.get("etool.token") != job["token"] or labels.get("etool.job") != job["id"]:
                raise ToolError("Local image ownership changed; refusing deletion")
            self.transport.run(["docker", "image", "rm", "--no-prune", job["image_tag"]],
                               log=work / "logs/local-cleanup.log")

    def cancel(self, job_id):
        with self._lock(job_id):
            job = self._read(job_id)
            state = self._remote(job, "cancel")
            return self._save(job, state["state"], exit_code=state.get("exit_code"),
                              container_state=state.get("container_state"))

    def discard(self, job_id):
        with self._lock(job_id):
            job = self._read(job_id)
            self._emit(job, "明确丢弃本次未回收远端资源")
            result = self._remote(job, "discard")
            atomic_json(self._path(job_id) / "discard.json", result)
            self._remove_local_image(job)
            return self._save(job, "discarded", cleanup=result)

    def resume(self, job_id, timeout=None):
        job = self._read(job_id)
        if job["state"] == "cleaned":
            return job
        if job["state"] == "discarded":
            raise ToolError("This task was discarded; create a new task")
        if job["state"] == "verified":
            # Also reconciles a cleanup whose response was lost after directory removal.
            return self.clean(job_id)
        if not job.get("remote_directory") or job["state"] in ("created", "built", "packed", "loaded"):
            self.submit(job_id)
        self.wait(job_id, timeout)
        return self.fetch(job_id, cleanup=True)

    def run(self, project_path, timeout=None):
        job = self.pack(project_path)
        self.submit(job["id"])
        return self.resume(job["id"], timeout)

    def list_jobs(self):
        jobs = []
        for path in sorted(self.jobs.iterdir()):
            if path.is_dir() and not path.is_symlink() and (path / "job.json").is_file():
                jobs.append(self._read(path.name))
        return jobs
