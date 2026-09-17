"""Bound regex compilation and matching in an isolated, short-lived process."""
import json
import subprocess
import sys
from functools import lru_cache

MAX_PATTERN = 2048
MAX_INPUT = 20000
WORKER_TIMEOUT = 0.4
# The pattern and sample are JSON input to a fixed program, never executable code.
_WORKER = r'''
import json, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
except (ImportError, OSError, ValueError):
    pass
try:
    import regex as engine
    bounded = True
except ImportError:
    import re as engine
    bounded = False
try:
    data = json.load(sys.stdin)
    flags = 0 if data['caseSensitive'] else engine.IGNORECASE
    pattern = engine.compile(data['pattern'], flags)
    if data['sample'] is None:
        out = {'matched': True}
    else:
        match = pattern.fullmatch if data['mode'] == 'full' else pattern.search
        out = {'matched': bool(match(data['sample'], **({'timeout': 0.05} if bounded else {})))}
except TimeoutError:
    out = {'error': '正则校验超时，请简化表达式'}
except Exception as exc:
    out = {'error': '正则表达式无效：' + str(exc)[:200]}
print(json.dumps(out, ensure_ascii=False))
'''


@lru_cache(maxsize=128)
def _run(pattern, mode, case_sensitive, sample):
    try:
        result = subprocess.run(
            [sys.executable, '-I', '-c', _WORKER],
            input=json.dumps({'pattern': pattern, 'mode': mode, 'caseSensitive': case_sensitive, 'sample': sample}),
            text=True, capture_output=True, timeout=WORKER_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, '正则校验超时，请简化表达式'
    if result.returncode:
        return False, '正则校验超时或超过资源限制，请简化表达式'
    try:
        output = json.loads(result.stdout)
        return output.get('matched', False), output.get('error')
    except (ValueError, TypeError):
        return False, '正则校验未完成，请简化表达式后重试'


def check_regex(pattern, mode='full', case_sensitive=True, sample=None):
    if not isinstance(pattern, str) or not pattern:
        raise ValueError('请填写正则表达式')
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f'正则表达式最多 {MAX_PATTERN} 个字符')
    if mode not in ('full', 'search'):
        raise ValueError('正则匹配方式必须为 full 或 search')
    if not isinstance(case_sensitive, bool):
        raise ValueError('区分大小写选项必须为布尔值')
    if sample is not None and len(sample) > MAX_INPUT:
        raise ValueError(f'正则样本最多 {MAX_INPUT} 个字符')
    matched, error = _run(pattern, mode, case_sensitive, sample)
    if error:
        raise ValueError(error)
    return matched
