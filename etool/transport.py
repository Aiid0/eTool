"""Subprocess and SSH transport; remote execution never installs a service."""

import json
from pathlib import Path
import shlex
import subprocess
import time


class TransportError(RuntimeError):
    def __init__(self, message, code="TRANSPORT"):
        super().__init__(message)
        self.code = code


class Transport:
    def __init__(self, config, progress=None):
        self.config = config
        self.progress = progress or (lambda message: None)

    @property
    def ssh(self):
        return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3", "-p", str(self.config["port"])]

    @property
    def host(self):
        return f"{self.config['user']}@{self.config['host']}"

    def run(self, args, log=None, timeout=None):
        """Write long-running command output to disk immediately, with heartbeats."""
        if log is None:
            try:
                process = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TransportError(str(exc)) from exc
            if process.returncode:
                raise TransportError(f"{Path(args[0]).name} failed ({process.returncode}): "
                                     + process.stderr[-4000:] + process.stdout[-1000:])
            return process.stdout
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        started = last_notice = time.monotonic()
        with log.open("wb") as stream:
            try:
                process = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT)
            except OSError as exc:
                raise TransportError(str(exc)) from exc
            try:
                while process.poll() is None:
                    now = time.monotonic()
                    if timeout is not None and now - started >= timeout:
                        process.terminate()
                        try:
                            process.wait(5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise TransportError(f"Command timed out; log: {log}", "TIMEOUT")
                    if now - last_notice >= 10:
                        self.progress(f"仍在执行，已等待 {now - started:.0f}s；日志 {log}")
                        last_notice = now
                    time.sleep(0.1)
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                raise
        if process.returncode:
            tail = log.read_text(errors="replace")[-4000:]
            raise TransportError(f"{Path(args[0]).name} failed ({process.returncode}); {log}\n{tail}")
        return log.read_text(errors="replace")

    def remote(self, action, job=None, log=None, timeout=300, **extra):
        source = Path(__file__).with_name("remote_agent.py").read_text()
        request = {"action": action, "config": self.config, **extra}
        if job is not None:
            request["job"] = job
        args = self.ssh + [self.host, shlex.join(["python3", "-c", source])]
        try:
            process = subprocess.run(args, input=json.dumps(request), text=True,
                                     capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError(f"SSH {action}: {exc}", "DISCONNECTED") from exc
        if log:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            Path(log).write_text(process.stdout + process.stderr)
        try:
            reply = json.loads(process.stdout)
        except (ValueError, TypeError) as exc:
            raise TransportError(f"SSH {action} failed ({process.returncode}): "
                                 + process.stderr[-2000:] + process.stdout[-1000:],
                                 "DISCONNECTED") from exc
        if not reply.get("ok"):
            error = reply.get("error", {})
            raise TransportError(error.get("message", "Remote operation failed"),
                                 error.get("code", "REMOTE"))
        if process.returncode:
            raise TransportError(f"SSH {action} disconnected after response", "DISCONNECTED")
        return reply["result"]

    def upload(self, source, directory, log):
        destination = f"{self.host}:{directory}/image.tar.gz.partial"
        self.run(["rsync", "-a", "--partial", "--checksum", "--info=progress2",
                  "--protect-args", "-e", shlex.join(self.ssh), str(source), destination], log=log)

    def download(self, directory, filename, destination, log):
        self.run(["rsync", "-a", "--partial", "--checksum", "--protect-args",
                  "-e", shlex.join(self.ssh), f"{self.host}:{directory}/{filename}",
                  str(destination)], log=log)
