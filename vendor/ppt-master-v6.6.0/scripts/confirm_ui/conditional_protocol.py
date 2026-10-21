"""Enterprise PPT-only conditional confirmation boundary (opt-in protocol v1).

No choices, defaults, stages, timer, task consent or Agent launch live here.
The native server still validates and writes all business receipts. This small
transport addition serializes submissions, binds them to the served inputs and
records uncertain writes before effects. Unknown outcomes are never replayed.
External Agent edits need not cooperate with the lock: both commit and native
receipt readers check the exact input hashes. A stale receipt cannot pass wait.
"""

import contextvars
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

LIMIT = 2 * 1024 * 1024
JOURNAL = '.confirmation-transaction.json'
RECEIPT = '_confirmation_receipt'
REQUEST = '_confirmation_request'
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_CURRENT = contextvars.ContextVar('ppt_confirmation_transaction', default=None)


class ProtocolError(ValueError):
    """Fixed labels only, never raw payloads or filesystem errors."""


def require(ok, label):
    if not ok:
        raise ProtocolError(label)


def encoded(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        require(len(raw) <= LIMIT, 'confirmation_size_limit')
        return raw
    except (TypeError, ValueError, RecursionError):
        raise ProtocolError('invalid_confirmation_json') from None


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read_bytes(path):
    path = Path(path)
    require(all(not part.is_symlink() for part in (path, *path.parents)), 'unsafe_confirmation_file')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= LIMIT, 'unsafe_confirmation_file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(LIMIT + 1)
        after = os.fstat(fd)
        require(len(raw) <= LIMIT and (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'confirmation_file_changed')
        return raw
    finally:
        os.close(fd)


def file_digest(path):
    raw = read_bytes(path)
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def read_record(path):
    raw = read_bytes(path)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        require(isinstance(value, dict), 'invalid_confirmation_record')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError('invalid_confirmation_record') from None


def save_record(path, value):
    # Private control evidence, never a replacement writer of Skill artifacts.
    raw = encoded(value)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require(not path.is_symlink(), 'unsafe_confirmation_file')
        os.replace(temp, path)
        if os.name == 'posix':
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        temp.unlink(missing_ok=True)


def sync_native_file(path):
    # Preserve the native writer's bytes; make them durable before the commit
    # marker promises that waiters may act on them.
    # Windows _commit requires a writable descriptor. No bytes are changed.
    fd = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'unsafe_confirmation_file')
        os.fsync(fd)
    finally:
        os.close(fd)


def _paths(root):
    return {'stage1': root / 'recommendations.stage1.json', 'stage2': root / 'recommendations.stage2.json',
            'selection': root / 'template_selection.json', 'install': root.parent / 'template_install.json', 'result': root / 'result.json'}


def inputs(root):
    return {key: file_digest(path) for key, path in _paths(root).items()}


def validate_receipt(root, result):
    """Validate only protocol-bearing receipts; preserve legacy manual reads."""
    if RECEIPT not in result:
        require(os.environ.get('PPT_MASTER_CONFIRMATION_PROTOCOL') != '1', 'confirmation_receipt_required')
        return
    proof = result.get(RECEIPT)
    require(isinstance(proof, dict) and proof.get('protocol') == 1, 'invalid_confirmation_receipt')
    expected_keys = {'stage1', 'selection'} if result.get('stage') == 'stage1' else {'stage1', 'stage2', 'selection', 'install'}
    bound = proof.get('inputs')
    require(isinstance(bound, dict) and set(bound) == expected_keys, 'invalid_confirmation_receipt')
    paths = _paths(root)
    require(all(value is None or isinstance(value, str) and _HASH.fullmatch(value) for value in bound.values()), 'invalid_confirmation_receipt')
    require(all(file_digest(paths[key]) == value for key, value in bound.items()), 'confirmation_receipt_stale')
    body = {key: value for key, value in result.items() if key != RECEIPT}
    require(digest(body) == proof.get('result_sha256'), 'confirmation_receipt_changed')
    journal = read_record(root / JOURNAL)
    require(journal is not None and journal.get('state') == 'committed' and journal.get('receipt_sha256') == digest(proof), 'confirmation_commit_unconfirmed')


class ConditionalConfirmation:
    def __init__(self, root):
        self.root = Path(root)
        self.epoch = uuid.uuid4().hex
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self, create=True):
        # This locks only this project's native owner submissions, not Gateway.
        # O_RDWR is required on Windows and shared production lease semantics.
        require(self.lock.acquire(timeout=3), 'confirmation_busy')
        fd = None
        locked = False
        try:
            if create:
                self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / '.confirmation-protocol.lock'
            require(all(not part.is_symlink() for part in (path, *path.parents)), 'unsafe_confirmation_file')
            fd = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe_confirmation_file')
            if os.name == 'posix':
                import fcntl
            else:
                import msvcrt
            deadline = time.monotonic() + 3
            while not locked:
                try:
                    if os.name == 'posix':
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError:
                    require(time.monotonic() < deadline, 'confirmation_busy')
                    time.sleep(0.02)
            yield
        finally:
            if fd is not None:
                try:
                    if locked:
                        if os.name == 'posix':
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        else:
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                finally:
                    os.close(fd)
            self.lock.release()

    def version(self, values):
        return digest({'epoch': self.epoch, 'inputs': values})

    def receipt(self, operation_id):
        """Reconcile ONE operation without re-submitting or repairing its files.

        A missing/stale/prepared journal is never permission to retry. Only the
        currently committed native receipt is exposed; replaced older operations
        remain unconfirmed. No choice data, project path or raw error is returned.
        """
        return self._receipt('operation_id', operation_id)

    def receipt_by_version(self, version):
        """Read a manually completed native gate without guessing its operation.

        The caller must bind the version to its previously observed exact task
        and project. This never changes choices or grants permission to retry.
        """
        return self._receipt('version', version)

    def _receipt(self, key, value):
        unknown = {'protocol': 1, 'state': 'unconfirmed', 'retry_permitted': False}
        missing = {'protocol': 1, 'state': 'not_found', 'retry_permitted': False}
        try:
            pattern = _ID if key == 'operation_id' else _HASH
            require(isinstance(value, str) and pattern.fullmatch(value), 'invalid_receipt_identity')
            if read_record(self.root / JOURNAL) is None:
                return missing, 200
            with self.transaction(create=False):
                journal = read_record(self.root / JOURNAL)
                require(journal is not None, 'confirmation_commit_unconfirmed')
                if journal.get(key) != value:
                    return missing, 200
                require(journal.get('protocol') == 1 and journal.get('state') == 'committed', 'confirmation_commit_unconfirmed')
                result = read_record(self.root / 'result.json')
                require(isinstance(result, dict) and RECEIPT in result, 'confirmation_commit_unconfirmed')
                validate_receipt(self.root, result)
                proof = result[RECEIPT]
                require(all(proof.get(key) == journal.get(key) for key in ('operation_id', 'version', 'payload_sha256')),
                        'confirmation_commit_unconfirmed')
                require(result.get('stage') in {'stage1', 'final'}, 'confirmation_commit_unconfirmed')
                require(all(isinstance(proof.get(key), str) and _HASH.fullmatch(proof[key])
                            for key in ('version', 'payload_sha256')), 'confirmation_commit_unconfirmed')
                require(isinstance(proof.get('operation_id'), str) and _ID.fullmatch(proof['operation_id']), 'confirmation_commit_unconfirmed')
                return {'protocol': 1, 'state': 'committed', 'retry_permitted': False, 'operation_id': proof['operation_id'],
                        'version': proof['version'], 'payload_sha256': proof['payload_sha256'],
                        'receipt_sha256': journal['receipt_sha256'], 'stage': result['stage']}, 200
        except (OSError, ValueError, RecursionError):
            return unknown, 409

    def describe(self, reader):
        """Bind the actual official response; changed files invalidate the read."""
        try:
            with self.transaction():
                journal = read_record(self.root / JOURNAL)
                require(journal is None or journal.get('state') == 'committed', 'confirmation_commit_unconfirmed')
                before = inputs(self.root)
                response = reader()
                # Native validation errors retain their original HTTP response.
                if response.status_code != 200:
                    return response
                data = response.get_json()
                require(isinstance(data, dict) and before == inputs(self.root), 'confirmation_version_changed')
                data['confirmation_protocol'] = {'protocol': 1, 'version': self.version(before)}
                response.set_data(json.dumps(data, ensure_ascii=False))
                return response
        except (OSError, ProtocolError):
            return {'error': 'confirmation_snapshot_unavailable'}, 409

    def submit(self, payload, validator):
        try:
            require(isinstance(payload, dict), 'invalid_confirmation_request')
            meta = payload.get(REQUEST)
            require(isinstance(meta, dict) and set(meta) == {'protocol', 'version', 'operation_id'}
                    and type(meta['protocol']) is int and meta['protocol'] == 1
                    and isinstance(meta['version'], str) and _HASH.fullmatch(meta['version'])
                    and isinstance(meta['operation_id'], str) and _ID.fullmatch(meta['operation_id']), 'confirmation_version_required')
            body = {key: value for key, value in payload.items() if key != REQUEST}
            require(RECEIPT not in body, 'reserved_confirmation_field')
            payload_hash = digest(body)
            with self.transaction():
                journal = read_record(self.root / JOURNAL)
                if journal is not None:
                    require(journal.get('state') == 'committed', 'confirmation_commit_unconfirmed')
                    if journal.get('operation_id') == meta['operation_id']:
                        require(journal.get('payload_sha256') == payload_hash and journal.get('version') == meta['version'], 'confirmation_operation_conflict')
                        result = read_record(self.root / 'result.json')
                        require(isinstance(result, dict) and RECEIPT in result, 'confirmation_commit_unconfirmed')
                        validate_receipt(self.root, result)
                        return {'status': 'ok', 'duplicate': True, 'receipt_sha256': journal['receipt_sha256']}, 200
                before = inputs(self.root)
                require(meta['version'] == self.version(before), 'confirmation_version_changed')
                token = _CURRENT.set({'owner': self, 'inputs': before, 'meta': meta, 'payload_sha256': payload_hash})
                try:
                    return validator(body)
                finally:
                    _CURRENT.reset(token)
        except ProtocolError as exc:
            return {'error': str(exc)}, 409
        except (OSError, ValueError, RecursionError):
            return {'error': 'confirmation_commit_unconfirmed'}, 409

    def commit(self, result, selection, writer):
        context = _CURRENT.get()
        require(context and context['owner'] is self, 'confirmation_transaction_required')
        expected = context['inputs'].copy()
        require(inputs(self.root) == expected, 'confirmation_version_changed')
        meta = context['meta']
        journal = {'protocol': 1, 'state': 'prepared', 'operation_id': meta['operation_id'],
                   'payload_sha256': context['payload_sha256'], 'version': meta['version']}
        save_record(self.root / JOURNAL, journal)  # No receipt writes before durable intent.
        if selection is not None:
            writer(self.root / 'template_selection.json', selection)
            sync_native_file(self.root / 'template_selection.json')
            expected['selection'] = file_digest(self.root / 'template_selection.json')
        require(inputs(self.root) == expected, 'confirmation_version_changed')
        keys = {'stage1', 'selection'} if result['stage'] == 'stage1' else {'stage1', 'stage2', 'selection', 'install'}
        proof = {'protocol': 1, 'operation_id': meta['operation_id'], 'version': meta['version'],
                 'payload_sha256': context['payload_sha256'], 'result_sha256': digest(result),
                 'inputs': {key: expected[key] for key in sorted(keys)}}
        result[RECEIPT] = proof
        writer(self.root / 'result.json', result)
        sync_native_file(self.root / 'result.json')
        # A file edit at any point after the native validator must not release waiters.
        require(all(file_digest(_paths(self.root)[key]) == value for key, value in proof['inputs'].items()), 'confirmation_version_changed')
        save_record(self.root / JOURNAL, {**journal, 'state': 'committed', 'receipt_sha256': digest(proof)})
