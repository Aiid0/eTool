"""Standalone, transient SSH-side executor for owned eTool Docker jobs."""

import contextlib
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time


JOB_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{5,100}\Z')
TOKEN_PATTERN = re.compile(r'[0-9a-f]{32}\Z')
META_NAME = '.etool-job.json'
ROOT_MARKER = '.etool-root.json'
JOBS_MARKER = '.etool-jobs.json'


class RemoteError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code, message):
    raise RemoteError(code, message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    data = json.dumps(value, indent=2, sort_keys=True) + '\n'
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checked_json(path):
    ensure_regular(path)
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as error:
        fail('INVALID_STATE', 'Cannot read valid task state: ' + str(path.name))


def ensure_regular(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        fail('NOT_FOUND', 'Required task file is missing: ' + path.name)
    if not stat.S_ISREG(mode):
        fail('UNSAFE_PATH', 'Task file is not a regular file: ' + str(path))


def no_symlinks(path):
    path = Path(path)
    for item in [*reversed(path.parents), path]:
        if item.is_symlink():
            fail('UNSAFE_PATH', 'Symbolic links are not allowed in task paths')


def docker(args, check=True, timeout=180):
    try:
        result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        fail('DOCKER_ERROR', 'Docker operation unavailable or timed out: ' + str(args[0]))
    if check and result.returncode:
        # Do not expose a docker create command or env values in errors.
        detail = result.stderr.strip()[-2000:] or result.stdout.strip()[-2000:]
        fail('DOCKER_ERROR', 'Docker ' + args[0] + ' failed: ' + detail)
    return result


def inspect_container(name):
    result = docker(['inspect', '--type', 'container', name], check=False)
    if not result.returncode:
        return json.loads(result.stdout)[0]
    if 'No such' in result.stderr or 'not found' in result.stderr.lower():
        return None
    fail('DOCKER_ERROR', 'Cannot inspect task container')


def inspect_image(name):
    result = docker(['image', 'inspect', name], check=False)
    if not result.returncode:
        return json.loads(result.stdout)[0]
    if 'No such' in result.stderr or 'not found' in result.stderr.lower():
        return None
    fail('DOCKER_ERROR', 'Cannot inspect task image')


def snapshot():
    return {
        'image_ids': sorted(set(docker(['image', 'ls', '-aq', '--no-trunc']).stdout.split())),
        'container_ids': sorted(set(docker(['ps', '-aq', '--no-trunc']).stdout.split())),
    }


def gpu_identity(requirement):
    try:
        result = subprocess.run([
            'nvidia-smi', '-i', requirement['uuid'],
            '--query-gpu=index,uuid,name,pci.bus_id,driver_version,memory.free',
            '--format=csv,noheader,nounits',
        ], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        fail('GPU_ERROR', 'GPU query unavailable or timed out: ' + str(error))
    if result.returncode:
        fail('GPU_ERROR', 'Cannot query the configured NVIDIA GPU')
    fields = [part.strip() for part in result.stdout.strip().split(',')]
    if len(fields) != 6:
        fail('GPU_ERROR', 'Unexpected GPU query response')
    try:
        index, free = int(fields[0]), int(fields[5])
    except ValueError:
        fail('GPU_ERROR', 'Invalid GPU query response')
    if index != requirement['index'] or fields[1] != requirement['uuid']:
        fail('GPU_MISMATCH', 'GPU UUID and physical index do not match the configuration')
    return {
        'physical_index': index, 'uuid': fields[1], 'name': fields[2],
        'pci_bus_id': fields[3], 'driver_version': fields[4], 'memory_free_mib': free,
    }


def validate_requirements(raw):
    if not isinstance(raw, dict) or set(raw) - {'gpu'}:
        fail('INVALID_REQUIREMENTS', 'requirements must be an object containing only gpu')
    if 'gpu' not in raw:
        return {}
    gpu = raw['gpu']
    if not isinstance(gpu, dict) or set(gpu) - {'uuid', 'index', 'min_free_mib'}:
        fail('INVALID_REQUIREMENTS', 'requirements.gpu must contain uuid, index and optional min_free_mib')
    uuid = gpu.get('uuid')
    if not isinstance(uuid, str) or not re.fullmatch(
            r'GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}', uuid):
        fail('INVALID_REQUIREMENTS', 'requirements.gpu.uuid must be a complete NVIDIA GPU UUID')
    if type(gpu.get('index')) is not int or gpu['index'] < 0:
        fail('INVALID_REQUIREMENTS', 'requirements.gpu.index must be a nonnegative integer')
    normalized = {'uuid': 'GPU-' + uuid[4:].lower(), 'index': gpu['index']}
    if 'min_free_mib' in gpu:
        free = gpu['min_free_mib']
        if type(free) is not int or not 0 <= free <= 81920:
            fail('INVALID_REQUIREMENTS', 'requirements.gpu.min_free_mib must be an integer between 0 and 81920')
        normalized['min_free_mib'] = free
    return {'gpu': normalized}


def job_requirements(config, job):
    project = job.get('project', {})
    if not isinstance(project, dict):
        fail('INVALID_JOB', 'Saved task project must be an object')
    options = project.get('options', {})
    if not isinstance(options, dict):
        fail('INVALID_JOB', 'Saved task options must be an object')
    if 'requirements' in project or 'requirements' in options:
        requirements = validate_requirements(project.get('requirements', options.get('requirements')))
        if 'requirements' in options and validate_requirements(options['requirements']) != requirements:
            fail('INVALID_JOB', 'Task requirements and saved options do not match')
        return requirements
    # Old runs recorded GPU selection in their server configuration. New runs
    # always carry requirements, including an empty object for CPU tasks.
    saved_config = job.get('config', config)
    if not isinstance(saved_config, dict):
        fail('INVALID_JOB', 'Saved task configuration must be an object')
    if 'gpu_uuid' not in saved_config and 'gpu_index' not in saved_config:
        return {}
    return validate_requirements({'gpu': {
        'uuid': saved_config.get('gpu_uuid'), 'index': saved_config.get('gpu_index'),
        'min_free_mib': options.get('min_free_gpu_mib', 256),
    }})


def check_gpu(requirement):
    gpu = gpu_identity(requirement)
    runtimes = json.loads(docker(['info', '--format', '{{json .Runtimes}}']).stdout)
    if 'nvidia' not in runtimes:
        fail('NVIDIA_RUNTIME_MISSING', 'Docker NVIDIA runtime is not available')
    if 'min_free_mib' in requirement and gpu['memory_free_mib'] < requirement['min_free_mib']:
        fail('GPU_MEMORY', 'Available GPU memory is below this task requirement')
    return gpu


def validate_config(raw):
    if not isinstance(raw, dict):
        fail('INVALID_CONFIG', 'Configuration must be an object')
    config = {'port': 22, **raw}
    for field in ('host', 'user'):
        value = config.get(field)
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', value):
            fail('INVALID_CONFIG', field + ' must use simple SSH host or user characters')
    if type(config['port']) is not int or not 1 <= config['port'] <= 65535:
        fail('INVALID_CONFIG', 'port must be an integer between 1 and 65535')
    if ('gpu_uuid' in config) != ('gpu_index' in config):
        fail('INVALID_CONFIG', 'Legacy gpu_uuid and gpu_index must be provided together')
    if 'gpu_uuid' in config:
        if type(config['gpu_index']) is not int or config['gpu_index'] < 0:
            fail('INVALID_CONFIG', 'gpu_index must be a nonnegative integer')
        gpu_uuid = config['gpu_uuid']
        if not isinstance(gpu_uuid, str) or not re.fullmatch(
                r'GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}', gpu_uuid):
            fail('INVALID_CONFIG', 'gpu_uuid must be a complete NVIDIA GPU UUID')
        config['gpu_uuid'] = 'GPU-' + gpu_uuid[4:].lower()
    root_value = config.get('remote_root')
    if not isinstance(root_value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', root_value):
        fail('UNSAFE_PATH', 'remote_root must be an absolute path using simple path characters')
    if any(part in ('.', '..') for part in root_value.split('/')):
        fail('UNSAFE_PATH', "remote_root cannot contain '.' or '..' segments")
    root = Path('/' + '/'.join(part for part in root_value.split('/') if part))
    if root == Path('/'):
        fail('UNSAFE_PATH', 'remote_root cannot be the filesystem root')
    if not root.parent.is_dir():
        fail('INVALID_CONFIG', 'The parent of remote_root must already exist')
    no_symlinks(root)
    if root.exists() and not root.is_dir():
        fail('INVALID_CONFIG', 'remote_root must be a directory when it already exists')
    config['remote_root'] = str(root)
    return config


def validate_job(raw):
    if not isinstance(raw, dict):
        fail('INVALID_JOB', 'Missing task identity')
    job = dict(raw)
    identifier, token = job.get('id', ''), job.get('token', '')
    if not isinstance(identifier, str) or not JOB_PATTERN.fullmatch(identifier):
        fail('INVALID_JOB', 'Invalid task identifier')
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        fail('INVALID_JOB', 'Invalid task token')
    if job.get('image_tag') != 'etool-job:' + identifier:
        fail('INVALID_JOB', 'Task image tag must be uniquely derived from its identifier')
    if job.get('container_name', 'etool-' + identifier) != 'etool-' + identifier:
        fail('INVALID_JOB', 'Invalid task container name')
    job['container_name'] = 'etool-' + identifier
    for key in ('archive_sha256', 'image_config_digest'):
        value = job.get(key)
        if value is not None and not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', str(value)):
            fail('INVALID_JOB', 'Invalid image digest')
    if 'archive_bytes' in job and (not isinstance(job['archive_bytes'], int) or job['archive_bytes'] <= 0):
        fail('INVALID_JOB', 'Invalid image archive size')
    return job


@contextlib.contextmanager
def lock():
    # The SSH user's existing home inode is shared by all configured work roots.
    try:
        descriptor = os.open(Path.home(), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        fail('LOCK_ERROR', 'Cannot lock the remote user home directory: ' + str(error))
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    fail('BUSY', 'Another eTool operation is in progress; retry shortly')
                time.sleep(.1)
        yield
    finally:
        os.close(descriptor)


def paths(config, job):
    root = Path(config['remote_root'])
    directory = root / 'jobs' / job['id']
    no_symlinks(directory)
    return root, directory


def owned(config, job):
    _, directory = paths(config, job)
    if not directory.is_dir():
        fail('NOT_FOUND', 'Task directory does not exist')
    meta = checked_json(directory / META_NAME)
    check_meta(meta, config, job)
    return directory, meta


def check_meta(meta, config, job):
    saved = meta.get('job', {})
    if meta.get('uid') != os.getuid() or saved.get('id') != job['id']:
        fail('OWNERSHIP', 'Task does not belong to this user and identifier')
    if not hmac.compare_digest(str(saved.get('token', '')), job['token']):
        fail('OWNERSHIP', 'Task token does not match')
    for key in ('image_tag', 'archive_sha256', 'archive_bytes', 'image_config_digest'):
        if key in job and key in saved and job[key] != saved[key]:
            fail('OWNERSHIP', 'Task identity or artifact digest does not match')
    requirements = job_requirements(config, job)
    saved_requirements = (validate_requirements(meta['requirements']) if 'requirements' in meta
                          else job_requirements(config, saved))
    if saved_requirements != requirements:
        fail('OWNERSHIP', 'Task requirements do not match the saved run')
    if 'gpu_uuid' in meta and meta['gpu_uuid'] != requirements.get('gpu', {}).get('uuid'):
        fail('OWNERSHIP', 'Task GPU identity does not match')


def write_meta(directory, meta):
    meta['updated_at'] = time.time()
    atomic_json(directory / META_NAME, meta)


def assert_container_owned(info, job):
    labels = info.get('Config', {}).get('Labels') or {}
    if labels.get('etool.job') != job['id'] or not hmac.compare_digest(str(labels.get('etool.token', '')), job['token']):
        fail('OWNERSHIP', 'Container ownership labels do not match')


def assert_image_owned(info, job, meta=None):
    labels = info.get('Config', {}).get('Labels') or {}
    if labels.get('etool.job') != job['id'] or not hmac.compare_digest(str(labels.get('etool.token', '')), job['token']):
        fail('OWNERSHIP', 'Image ownership labels do not match')
    expected = job.get('image_config_digest')
    if expected and info.get('Id') != ('sha256:' + expected.removeprefix('sha256:')):
        fail('IMAGE_MISMATCH', 'Loaded image configuration digest does not match')
    if meta and meta.get('image_id') and info.get('Id') != meta['image_id']:
        fail('IMAGE_MISMATCH', 'Task image tag changed after loading')


def owned_containers(job):
    identifiers = docker(['ps', '-aq', '--filter', 'label=etool.job=' + job['id']]).stdout.split()
    result = []
    for identifier in identifiers:
        info = inspect_container(identifier)
        if info:
            assert_container_owned(info, job)
            result.append(info)
    return result


def state_result(directory, meta, info=None):
    if info is None:
        info = inspect_container(meta['job']['container_name'])
    if info:
        assert_container_owned(info, meta['job'])
        state = info.get('State', {})
        if state.get('Running') or state.get('Status') in ('restarting', 'paused'):
            meta['state'] = 'running'
        elif state.get('Status') in ('exited', 'dead'):
            if meta['state'] not in ('collected', 'verified'):
                meta['state'] = 'exited'
            meta['exit_code'] = int(state.get('ExitCode', 1))
        meta['container_id'] = info['Id']
        write_meta(directory, meta)
    elif meta.get('container_id') and meta['state'] not in ('collected', 'verified'):
        fail('CONTAINER_MISSING', 'Recorded task container disappeared before collection')
    return {
        'state': meta['state'], 'container_id': meta.get('container_id'),
        'container_state': info.get('State') if info else None,
        'exit_code': meta.get('exit_code'), 'gpu': meta.get('gpu'),
        'directory': str(directory), 'image_id': meta.get('image_id'),
    }


def doctor(config, requirements=None):
    requirements = validate_requirements({} if requirements is None else requirements)
    gpu = check_gpu(requirements['gpu']) if 'gpu' in requirements else None
    usage = shutil.disk_usage(Path(config['remote_root']).parent)
    return {'gpu': gpu, 'uid': os.getuid(), 'gid': os.getgid(),
            'docker_version': docker(['version', '--format', '{{.Server.Version}}']).stdout.strip(),
            'docker_root': docker(['info', '--format', '{{.DockerRootDir}}']).stdout.strip(),
            'disk_free_bytes': usage.free, 'remote_root': config['remote_root']}


def prepare(config, job):
    requirements = job_requirements(config, job)
    root, directory = paths(config, job)
    if directory.exists():
        _, meta = owned(config, job)
        return {'directory': str(directory), 'state': meta['state'], 'baseline': meta['baseline']}
    baseline = snapshot()
    root_existed, jobs_existed = root.exists(), (root / 'jobs').exists()
    root.mkdir(exist_ok=True)
    if not root_existed:
        atomic_json(root / ROOT_MARKER, {'tool': 'etool', 'uid': os.getuid(), 'created_at': time.time(), 'jobs_existed': False})
    (root / 'jobs').mkdir(exist_ok=True)
    if not jobs_existed:
        atomic_json(root / 'jobs' / JOBS_MARKER,
                    {'tool': 'etool', 'uid': os.getuid(), 'created_at': time.time()})
    directory.mkdir(mode=0o700)
    (directory / 'outputs').mkdir()
    (directory / 'logs').mkdir()
    meta = {'schema_version': 1, 'uid': os.getuid(), 'gid': os.getgid(), 'job': job,
            'requirements': requirements, 'state': 'prepared', 'baseline': baseline,
            'root_existed': root_existed, 'jobs_existed': jobs_existed, 'created_at': time.time()}
    write_meta(directory, meta)
    return {'directory': str(directory), 'state': 'prepared', 'baseline': baseline}


def validate_image_archive(path, job):
    with tarfile.open(path, 'r:gz') as archive:
        try:
            member = archive.getmember('manifest.json')
        except KeyError:
            fail('INVALID_ARCHIVE', 'Docker archive does not contain manifest.json')
        if not member.isfile() or member.size > 1024 * 1024:
            fail('INVALID_ARCHIVE', 'Invalid Docker archive manifest')
        manifest = json.load(archive.extractfile(member))
        if len(manifest) != 1 or manifest[0].get('RepoTags') != [job['image_tag']]:
            fail('INVALID_ARCHIVE', 'Archive must contain exactly this task image tag')
        config_name = manifest[0].get('Config')
        config = archive.getmember(config_name)
        if not config.isfile() or config.size > 10 * 1024 * 1024:
            fail('INVALID_ARCHIVE', 'Invalid Docker image configuration')
        data = archive.extractfile(config).read()
        if hashlib.sha256(data).hexdigest() != job['image_config_digest'].removeprefix('sha256:'):
            fail('IMAGE_MISMATCH', 'Archive image configuration digest does not match')
        labels = json.loads(data).get('config', {}).get('Labels') or {}
        if labels.get('etool.job') != job['id'] or labels.get('etool.token') != job['token']:
            fail('OWNERSHIP', 'Archive image ownership labels do not match')


def load(config, job):
    directory, meta = owned(config, job)
    existing = inspect_image(job['image_tag'])
    if existing:
        assert_image_owned(existing, job, meta)
        meta['image_id'] = existing['Id']
        if meta['state'] == 'prepared':
            meta['state'] = 'loaded'
        write_meta(directory, meta)
        return {'image_id': existing['Id'], 'state': meta['state']}
    if meta.get('container_id'):
        fail('IMAGE_MISSING', 'Task image disappeared after task submission')
    partial, archive = directory / 'image.tar.gz.partial', directory / 'image.tar.gz'
    source = partial if partial.exists() else archive
    ensure_regular(source)
    if source.stat().st_size != job.get('archive_bytes') or sha256(source) != job.get('archive_sha256'):
        fail('ARCHIVE_CHECKSUM', 'Uploaded image archive size or SHA-256 does not match')
    validate_image_archive(source, job)
    if source != archive:
        os.replace(source, archive)
    result = docker(['image', 'load', '-i', str(archive)], timeout=1800)
    (directory / 'logs/load.log').write_text(result.stdout + result.stderr)
    info = inspect_image(job['image_tag'])
    if not info:
        fail('IMAGE_MISSING', 'Task image missing after successful import')
    assert_image_owned(info, job)
    meta.update(image_id=info['Id'], state='loaded')
    write_meta(directory, meta)
    return {'image_id': info['Id'], 'state': meta['state']}


def validate_options(raw):
    if raw is not None and not isinstance(raw, dict):
        fail('INVALID_OPTIONS', 'Task options must be an object')
    options = dict(raw or {})
    command = options.get('command')
    if command is not None and (not isinstance(command, list) or any(not isinstance(v, str) or '\0' in v for v in command)):
        fail('INVALID_OPTIONS', 'command must be an array of strings or null')
    env = options.get('env', {})
    if not isinstance(env, dict):
        fail('INVALID_OPTIONS', 'env must be an object')
    for key, value in env.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str) or '\0' in value:
            fail('INVALID_OPTIONS', 'Invalid environment entry')
        if key.startswith(('NVIDIA_', 'CUDA_VISIBLE', 'ETOOL_')) or key == 'CUDA_DEVICE_ORDER':
            fail('GPU_POLICY', 'GPU and eTool environment variables are reserved')
    cpus = options.get('cpus', 1)
    if not isinstance(cpus, (int, float)) or isinstance(cpus, bool) or not math.isfinite(cpus) or not .1 <= cpus <= 32:
        fail('INVALID_OPTIONS', 'CPU limit must be between 0.1 and 32')
    for key, default in [('memory', '2g'), ('shm_size', '256m')]:
        value = options.get(key, default)
        if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*(?:[kKmMgG])?', value):
            fail('INVALID_OPTIONS', 'Invalid memory or shared-memory limit')
        options[key] = value
    options.update(command=command, env=env, cpus=cpus)
    if 'requirements' in options:
        options['requirements'] = validate_requirements(options['requirements'])
    else:
        # Preserve the original normalized options when resuming old runs.
        free = options.get('min_free_gpu_mib', 256)
        if type(free) is not int or not 0 <= free <= 81920:
            fail('INVALID_OPTIONS', 'Invalid free GPU memory requirement')
        options['min_free_gpu_mib'] = free
    return options


def start(config, job, raw_options):
    directory, meta = owned(config, job)
    existing = inspect_container(job['container_name'])
    if existing:
        assert_container_owned(existing, job)
        if meta.get('container_id') and existing['Id'] != meta['container_id']:
            fail('CONTAINER_REPLACED', 'Recorded task container was replaced; refusing to start another container')
    # Save repair intent before removing a never-started invalid container. If
    # the connection drops after rm, a retry can finish updating the metadata.
    recreate = meta.get('recreate_container_id')
    if existing and existing.get('State', {}).get('Status') == 'created':
        # Repair an owned container that was created but never ran with an
        # unsupported Docker log option. No experiment output can be lost here.
        log_config = existing.get('HostConfig', {}).get('LogConfig', {}).get('Config') or {}
        if log_config.get('max-size') == '-1' and not recreate:
            recreate = existing['Id']
            meta['recreate_container_id'] = recreate
            write_meta(directory, meta)
    if recreate:
        if existing:
            if existing['Id'] != recreate or existing.get('State', {}).get('Status') != 'created':
                fail('CONTAINER_CHANGED', 'Container changed while a never-started repair was pending')
            docker(['rm', existing['Id']])
            existing = None
        meta.pop('container_id', None)
        meta.pop('start_requested', None)
        meta.pop('recreate_container_id', None)
        meta['state'] = 'loaded'
        write_meta(directory, meta)
    if existing and existing.get('State', {}).get('Status') != 'created':
        return state_result(directory, meta, existing)
    if meta.get('start_requested'):
        # A request can be committed by Docker even when its SSH response is
        # lost. There is no exactly-once transaction across those two services.
        # Never repeat an uncertain start: an already finished experiment must
        # not execute a second time. The user can inspect/cancel/discard it.
        fail('START_UNCERTAIN', 'A start was already requested but its outcome is unknown; '
             'inspect the retained task, then cancel/discard it rather than restarting it')
    if meta.get('container_id') and not existing:
        fail('CONTAINER_MISSING', 'Previously submitted task container disappeared')
    image = inspect_image(job['image_tag'])
    if not image:
        fail('NOT_LOADED', 'Load the task image before starting')
    assert_image_owned(image, job, meta)
    options = validate_options(raw_options)
    requirements = job_requirements(config, job)
    if 'requirements' in options and options['requirements'] != requirements:
        fail('OPTIONS_MISMATCH', 'Startup requirements do not match the saved task')
    if meta.get('options') and meta['options'] != options:
        fail('OPTIONS_MISMATCH', 'A resumed task must use the originally recorded options')
    gpu_requirement = requirements.get('gpu')
    gpu = None
    if gpu_requirement is not None:
        # The user home flock keeps this check + start atomic across this user's clients.
        gpu_uuid = gpu_requirement['uuid']
        contenders = docker(['ps', '-aq', '--filter', 'label=etool.gpu_uuid=' + gpu_uuid]).stdout.split()
        for identifier in contenders:
            other = inspect_container(identifier)
            if not other:
                continue
            if other.get('Config', {}).get('Labels', {}).get('etool.job') == job['id']:
                continue
            if other.get('State', {}).get('Status') in ('running', 'created', 'restarting', 'paused'):
                fail('GPU_BUSY', 'Another eTool task is using the configured GPU; retry when it finishes')
        gpu = check_gpu(gpu_requirement)
        atomic_json(directory / 'gpu-host.json', gpu)
    meta.update(gpu=gpu, options=options, requirements=requirements)
    write_meta(directory, meta)
    labels = ['--label', 'etool.job=' + job['id'], '--label', 'etool.token=' + job['token']]
    # An image may set NVIDIA_VISIBLE_DEVICES=all, and the daemon may use the
    # NVIDIA runtime by default. Explicitly disable its device injection for CPU tasks.
    device_options = ['--env', 'NVIDIA_VISIBLE_DEVICES=void']
    if gpu_requirement is not None:
        labels += ['--label', 'etool.gpu_uuid=' + gpu_uuid]
        device_options = ['--runtime=nvidia', '--gpus', 'device=' + gpu_uuid,
                          '--env', 'NVIDIA_VISIBLE_DEVICES=' + gpu_uuid,
                          '--env', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility']
        probe_name = 'etool-probe-' + job['id']
        stale_probe = inspect_container(probe_name)
        if stale_probe:
            assert_container_owned(stale_probe, job)
            if stale_probe.get('State', {}).get('Running'):
                fail('PROBE_RUNNING', 'An earlier GPU probe is still running; retry shortly')
            docker(['rm', probe_name])
        probe = docker(['run', '--rm', '--name', probe_name, *labels, *device_options,
                        '--network=none', '--read-only', '--cap-drop=ALL',
                        '--security-opt=no-new-privileges:true', '--entrypoint', 'nvidia-smi',
                        job['image_tag'], '--query-gpu=uuid,name,pci.bus_id', '--format=csv,noheader'],
                       check=False, timeout=60)
        (directory / 'logs/gpu-probe.log').write_text(probe.stdout + probe.stderr)
        if probe.returncode:
            fail('GPU_PROBE', 'GPU probe failed; inspect the task GPU probe log')
        visible = [line.strip().split(',')[0].strip() for line in probe.stdout.splitlines() if line.strip()]
        if visible != [gpu_uuid]:
            fail('GPU_MISMATCH', 'The probe container did not see exactly the configured GPU UUID')
        device_options += ['--env', 'ETOOL_EXPECTED_GPU_UUID=' + gpu_uuid,
                           '--env', 'ETOOL_HOST_GPU_INDEX=' + str(gpu_requirement['index']),
                           '--env', 'CUDA_VISIBLE_DEVICES=' + gpu_uuid]
    if not existing:
        create = ['create', '--name', job['container_name'], *labels, *device_options,
                  '--env', 'XDG_CACHE_HOME=/tmp/cache', '--env', 'TMPDIR=/tmp',
                  '--network=none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=512m',
                  '--cpus', str(options['cpus']), '--memory', options['memory'],
                  '--shm-size', options['shm_size'], '--pids-limit=256', '--cap-drop=ALL',
                  '--security-opt=no-new-privileges:true', '--log-driver=json-file',
                  '--user', str(os.getuid()) + ':' + str(os.getgid()),
                  '--mount', 'type=bind,src=' + str(directory / 'outputs') + ',dst=/outputs']
        for key, value in sorted(options['env'].items()):
            create += ['--env', key + '=' + value]
        create.append(job['image_tag'])
        if options['command'] is not None:
            create += options['command']
        docker(create)
        existing = inspect_container(job['container_name'])
        if not existing:
            fail('CONTAINER_MISSING', 'Task container not found after creation')
        meta['container_id'] = existing['Id']
        write_meta(directory, meta)
    # Reconcile again after the probe: an earlier in-flight Docker request may
    # have started and completed this container while this call was probing.
    fresh = inspect_container(job['container_name'])
    if not fresh:
        fail('CONTAINER_MISSING', 'Task container disappeared before startup')
    assert_container_owned(fresh, job)
    if fresh['Id'] != meta.get('container_id', fresh['Id']):
        fail('CONTAINER_REPLACED', 'Recorded task container was replaced before startup')
    if fresh.get('State', {}).get('Status') != 'created':
        return state_result(directory, meta, fresh)
    meta['container_id'] = fresh['Id']
    meta['start_requested'] = {'container_id': fresh['Id'], 'at': time.time()}
    write_meta(directory, meta)
    try:
        docker(['start', job['container_name']])
    except RemoteError:
        # A Docker/SSH error does not prove whether startup committed. An
        # observable running/exited state is conclusive; created is uncertain.
        observed = inspect_container(job['container_name'])
        if observed:
            assert_container_owned(observed, job)
            if observed['Id'] != meta['container_id']:
                fail('CONTAINER_REPLACED', 'Task container changed during startup')
            if observed.get('State', {}).get('Status') in ('running', 'exited', 'dead', 'paused', 'restarting'):
                return state_result(directory, meta, observed)
        fail('START_UNCERTAIN', 'Docker did not confirm startup; task resources are retained. '
             'Inspect/cancel/discard this task; the tool will not repeat its start request')
    meta['state'] = 'running'
    write_meta(directory, meta)
    return state_result(directory, meta)


def logs(config, job, tail):
    directory, meta = owned(config, job)
    info = inspect_container(job['container_name'])
    if not info:
        fail('NOT_STARTED', 'Task container has not been created')
    assert_container_owned(info, job)
    if tail is not None and (not isinstance(tail, int) or not -1 <= tail <= 100000):
        fail('INVALID_OPTIONS', 'Log tail must be -1 (all) or between 0 and 100000 lines')
    if tail == 0:
        return {'text': '', 'container_state': info.get('State'),
                'state': state_result(directory, meta, info)['state']}
    if tail == -1:
        tail = None
    args = ['logs', '--timestamps']
    if tail is not None:
        args += ['--tail', str(tail)]
    args.append(job['container_name'])
    # Keep the Docker CLI's two streams on the same pipe, just as collection
    # does. Concatenating separately captured stdout/stderr would reorder old
    # lines as stderr grows, breaking prefix-based follow across snapshots.
    try:
        result = subprocess.run(['docker', *args], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        fail('DOCKER_ERROR', 'Could not read container logs')
    if result.returncode:
        fail('DOCKER_ERROR', 'Could not read container logs')
    return {'text': result.stdout, 'container_state': info.get('State'),
            'state': state_result(directory, meta, info)['state']}


def payload_files(directory, requirements):
    selected = [directory / 'logs/container.log', directory / 'logs/container-inspect.json']
    if 'gpu' in requirements:
        selected += [directory / 'gpu-host.json', directory / 'logs/gpu-probe.log']
    outputs = directory / 'outputs'
    no_symlinks(outputs)
    for path in outputs.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            fail('UNSAFE_OUTPUT', 'Experiment outputs contain a symlink or special file')
        if stat.S_ISREG(mode):
            selected.append(path)
    for path in selected:
        no_symlinks(path)
        ensure_regular(path)
    return sorted(selected, key=lambda path: path.relative_to(directory).as_posix())


def frozen_result(directory, meta):
    frozen = meta.get('result')
    if not frozen:
        fail('NOT_COLLECTED', 'Task has no frozen results')
    archive, manifest = directory / 'results.tar.gz', directory / 'RESULTS.json'
    ensure_regular(archive)
    ensure_regular(manifest)
    if sha256(archive) != frozen['archive_sha256'] or sha256(manifest) != frozen['manifest_sha256']:
        fail('RESULT_CHANGED', 'Frozen result archive or manifest changed')
    return frozen


def collect(config, job):
    directory, meta = owned(config, job)
    if meta.get('result'):
        return dict(frozen_result(directory, meta), state=meta['state'], exit_code=meta.get('exit_code'))
    status = state_result(directory, meta)
    if status['state'] == 'running':
        fail('RUNNING', 'Stop or wait for the experiment before collecting outputs')
    if not meta.get('container_id'):
        fail('NOT_STARTED', 'Task has not run; use discard to remove an unused task')
    info = inspect_container(job['container_name'])
    if not info or info.get('State', {}).get('Status') not in ('exited', 'dead'):
        fail('NOT_FINISHED', 'Task container has not exited')
    assert_container_owned(info, job)
    log_path = directory / 'logs/container.log'
    # Stream stdout and stderr into one file without buffering a long experiment in RAM.
    with log_path.open('wb') as stream:
        result = subprocess.run(['docker', 'logs', '--timestamps', job['container_name']],
                                stdout=stream, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        fail('DOCKER_ERROR', 'Could not collect the complete container log')
    atomic_json(directory / 'logs/container-inspect.json', info)
    files = payload_files(directory, job_requirements(config, job))
    manifest = {'schema_version': 1, 'job_id': job['id'], 'files': [
        {'path': path.relative_to(directory).as_posix(), 'size': path.stat().st_size,
         'sha256': sha256(path)} for path in files]}
    manifest_path = directory / 'RESULTS.json'
    atomic_json(manifest_path, manifest)
    temporary = directory / 'results.tar.gz.partial'
    with tarfile.open(temporary, 'w:gz', compresslevel=1) as archive:
        for path in files:
            archive.add(path, arcname=path.relative_to(directory).as_posix(), recursive=False)
    os.replace(temporary, directory / 'results.tar.gz')
    frozen = {'archive_sha256': sha256(directory / 'results.tar.gz'),
              'archive_bytes': (directory / 'results.tar.gz').stat().st_size,
              'manifest_sha256': sha256(manifest_path)}
    meta.update(result=frozen, state='collected', exit_code=int(info['State']['ExitCode']))
    write_meta(directory, meta)
    return dict(frozen, state='collected', exit_code=meta['exit_code'])


def acknowledge(config, job, request):
    directory, meta = owned(config, job)
    frozen = frozen_result(directory, meta)
    for field in ('manifest_sha256', 'archive_sha256'):
        if not hmac.compare_digest(str(request.get(field, '')), frozen[field]):
            fail('CHECKSUM_MISMATCH', 'Client result verification digest does not match')
    meta['verified'] = dict(frozen)
    meta['state'] = 'verified'
    write_meta(directory, meta)
    return {'state': 'verified', 'exit_code': meta.get('exit_code')}


def remove_empty_root(root):
    jobs = root / 'jobs'
    jobs_marker = jobs / JOBS_MARKER
    if jobs_marker.exists():
        no_symlinks(jobs)
        jobs_record = checked_json(jobs_marker)
        if (jobs_record.get('tool') == 'etool' and jobs_record.get('uid') == os.getuid()
                and set(item.name for item in jobs.iterdir()) == {JOBS_MARKER}):
            jobs_marker.unlink()
            jobs.rmdir()
    marker = root / ROOT_MARKER
    if not marker.exists():
        return
    no_symlinks(root)
    record = checked_json(marker)
    if record.get('tool') != 'etool' or record.get('uid') != os.getuid():
        return
    if jobs.exists():
        if not jobs.is_dir() or any(jobs.iterdir()):
            return
        jobs.rmdir()
    if set(item.name for item in root.iterdir()) == {ROOT_MARKER}:
        marker.unlink()
        root.rmdir()


def cleanup_result(job, directory, image_id, baseline):
    after = snapshot()
    container_absent = not owned_containers(job) and inspect_container(job['container_name']) is None
    image_tag_absent = inspect_image(job['image_tag']) is None
    preserved = bool(image_id and image_id in (baseline or {}).get('image_ids', []))
    image_id_absent = inspect_image(image_id) is None if image_id else True
    result = {'state': 'cleaned', 'container_absent': container_absent,
              'image_tag_absent': image_tag_absent, 'image_id_absent': image_id_absent,
              'preexisting_image_preserved': preserved,
              'task_directory_absent': not directory.exists(), 'baseline': baseline, 'after': after}
    if baseline:
        result['new_image_ids_remaining'] = sorted(set(after['image_ids']) - set(baseline['image_ids']))
        result['new_container_ids_remaining'] = sorted(set(after['container_ids']) - set(baseline['container_ids']))
        result['image_id_set_restored'] = set(after['image_ids']) == set(baseline['image_ids'])
        result['container_id_set_restored'] = set(after['container_ids']) == set(baseline['container_ids'])
    if not container_absent or not image_tag_absent or directory.exists() or (not image_id_absent and not preserved):
        fail('CLEANUP_INCOMPLETE', 'One or more owned task resources remain after cleanup')
    return result


def cleanup(config, job, discard=False):
    root, directory = paths(config, job)
    journal = root / ('.etool-cleanup-' + job['id'] + '.json')
    journal_meta = None
    if journal.exists():
        journal_meta = checked_json(journal)
        check_meta(journal_meta, config, job)
        if not journal_meta.get('cleanup_started'):
            fail('INVALID_STATE', 'Cleanup journal is incomplete')
    if not directory.exists() and journal_meta is None:
        # A retry after directory removal can verify absence but cannot delete any resources.
        if inspect_container(job['container_name']) or inspect_image(job['image_tag']) or owned_containers(job):
            fail('OWNERSHIP', 'Task state missing while resources remain; refusing deletion')
        remove_empty_root(root) if root.exists() else None
        return cleanup_result(job, directory, None, None)
    if journal_meta is not None:
        meta = journal_meta
    else:
        directory, meta = owned(config, job)
    containers = owned_containers(job)
    direct = inspect_container(job['container_name'])
    if direct and all(item['Id'] != direct['Id'] for item in containers):
        assert_container_owned(direct, job)
        containers.append(direct)
    for info in containers:
        if info.get('State', {}).get('Running') or info.get('State', {}).get('Status') in ('restarting', 'paused'):
            fail('RUNNING', 'A task container is active; cancel it before cleanup')
    if not discard and journal_meta is None:
        if not meta.get('verified'):
            fail('UNVERIFIED', 'Results must be fetched and verified before cleanup')
        frozen = frozen_result(directory, meta)
        if meta['verified'] != frozen:
            fail('UNVERIFIED', 'Verified receipt no longer matches frozen results')
    image = inspect_image(job['image_tag'])
    if image:
        assert_image_owned(image, job, meta)
        if not meta.get('image_id'):
            meta['image_id'] = image['Id']
    # Keep the meta file until all Docker mutations complete, so retries retain ownership proof.
    meta['cleanup_started'] = True
    if directory.exists() and (directory / META_NAME).exists():
        write_meta(directory, meta)
    # Persist ownership + verification outside the tree before removing its files.
    # An interrupted rmtree can delete the inner metadata and archive in any order.
    atomic_json(journal, meta)
    for info in containers:
        docker(['rm', info['Id']])
    if image:
        docker(['image', 'rm', '--no-prune', job['image_tag']])
    image_id = meta.get('image_id')
    baseline = meta.get('baseline')
    if image_id and image_id not in baseline.get('image_ids', []):
        remaining = inspect_image(image_id)
        if remaining:
            assert_image_owned(remaining, job, meta)
            fail('IMAGE_REFERENCED', 'Task image remains referenced; metadata retained for retry')
    no_symlinks(directory)
    # rmtree does not follow output symlinks, but never traverse a symlink task root.
    if directory.exists():
        shutil.rmtree(directory)
    journal.unlink()
    remove_empty_root(root)
    return cleanup_result(job, directory, image_id, baseline)


def cancel(config, job):
    directory, meta = owned(config, job)
    for info in owned_containers(job):
        if info.get('State', {}).get('Running'):
            docker(['stop', '--time', '10', info['Id']], timeout=30)
    return state_result(directory, meta)


def handle(request):
    if not isinstance(request, dict):
        fail('INVALID_REQUEST', 'Request must be an object')
    config = validate_config(request.get('config'))
    action = request.get('action')
    if action == 'doctor':
        return doctor(config, validate_requirements(request.get('requirements', {})))
    job = validate_job(request.get('job'))
    with lock():
        if action == 'prepare':
            return prepare(config, job)
        if action == 'load':
            return load(config, job)
        if action == 'start':
            return start(config, job, request.get('options'))
        if action == 'status':
            directory, meta = owned(config, job)
            return state_result(directory, meta)
        if action == 'logs':
            return logs(config, job, request.get('tail', 200))
        if action == 'collect':
            return collect(config, job)
        if action == 'acknowledge':
            return acknowledge(config, job, request)
        if action == 'cleanup':
            return cleanup(config, job)
        if action == 'discard':
            return cleanup(config, job, discard=True)
        if action == 'cancel':
            return cancel(config, job)
        fail('INVALID_ACTION', 'Unknown remote action')


def main():
    try:
        request = json.load(sys.stdin)
        response = {'ok': True, 'result': handle(request)}
    except RemoteError as error:
        response = {'ok': False, 'error': {'code': error.code, 'message': error.message}}
    except Exception as error:
        response = {'ok': False, 'error': {'code': 'INTERNAL_ERROR',
                                         'message': type(error).__name__ + ': ' + str(error)}}
    print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
