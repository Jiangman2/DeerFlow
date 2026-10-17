"""Content-free Go startup diagnostics; never export panic arguments or DSNs."""
import re


def safe_startup(raw):
    text = raw.decode('utf-8', 'replace')
    patterns = {
        'nil_pointer': r'invalid memory address|nil pointer dereference',
        'config_read': r'error reading config file',
        'config_decode': r'unable to decode config into struct|decoding failed',
        'config_validation': r'config validation errors:',
        'config_loaded': r'Using configuration file:',
        'dig_dependency': r'could not build|failed to build|could not resolve|cannot provide',
        'db_migrations_started': r'Running database migrations',
        'db_migrations_failed': r'Database migration failed',
        'db_authentication': r'password authentication failed',
        'db_missing_relation': r'relation .* does not exist',
        'dns_failure': r'no such host|server misbehaving',
        'permission_denied': r'permission denied|Permission denied',
        'readonly_filesystem': r'read-only file system',
        'missing_file': r'no such file or directory|No such file or directory',
        'missing_library': r'error while loading shared libraries|cannot open shared object',
        'unsupported_database': r'unsupported database driver:',
        'time_duration_decode': r'cannot parse .* as|invalid duration',
        'server_listening': r'Server is running at',
    }
    # Only fixed trusted namespace prefixes, and only tokens BEFORE arguments.
    # Do not print panic/error lines (which may contain passwords in nested DSNs).
    prefixes = ('github.com/Tencent/WeKnora/', 'github.com/yanyiwu/gojieba.',
                'github.com/go-viper/mapstructure/', 'github.com/spf13/viper.',
                'go.uber.org/dig.', 'runtime.', 'reflect.', 'main.')
    frames = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_./-]*(?:\(\*?[A-Za-z_][A-Za-z0-9_]*\)\.[A-Za-z_][A-Za-z0-9_.]*)?)\(', line)
        if not m or not m[1].startswith(prefixes): continue
        # The only parentheses allowed in a name are an explicit Go receiver.
        # Unrecognized generic symbols are omitted rather than copying arguments.
        name = m[1]
        if len(name) > 240: continue
        if i+1 < len(lines):
            loc = re.match(r'^\s+[^\r\n]*[/\\]([A-Za-z0-9_.-]{1,100}\.go):(\d{1,7})(?:\s|$)', lines[i+1])
            if loc: frames.append(dict(function=name, file=loc[1], line=int(loc[2])))
        if len(frames) == 40: break
    return dict(classes={k: bool(re.search(v, text)) for k, v in patterns.items()},
                stack=frames, raw_output_saved=False, arguments_exported=False)


def augment_runtime(source):
    """Patch only diagnostic projection in the hash-pinned embedded runner."""
    import inspect
    marker = 'def progress(role,raw,overflow):'
    live = 'dict(classes=diagnostics(raw),logs_truncated=overflow,raw_logs_saved=False)'
    stopped = 'save(\'STOPPED-\'+role+\'.json\',dict(exit_code=p.returncode,classes=diagnostics(tail),\n                logs_truncated=overflow,raw_logs_saved=False))'
    if any(source.count(x) != 1 for x in (marker, live, stopped)):
        raise RuntimeError('frozen_runtime_diagnostic_shape_changed')
    # inspect source is supplied explicitly by the packager in the single-file tool.
    function = SAFE_FUNCTION_SOURCE if SAFE_FUNCTION_SOURCE is not None else inspect.getsource(safe_startup)
    source = source.replace(marker, function+'\n\n'+marker, 1)
    source = source.replace(live, live[:-1]+',startup=safe_startup(raw))', 1)
    return source.replace(stopped, stopped[:-2]+',startup=safe_startup(tail)))', 1)


SAFE_FUNCTION_SOURCE = None
