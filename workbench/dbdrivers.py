"""Real connection probes and schema metadata reads for MySQL and Redis.

Only fixed, allow-listed operations are executed: MySQL connect + auth +
SELECT 1 (probe) or parameterized information_schema reads (catalog); Redis
connect + AUTH/SELECT + PING. No user-supplied SQL, commands or scripts ever
run through this module. Probes never write business data.

All probes are pure network calls: callers must not hold the global write lock
while running them. Error reporting is sanitized — categories plus actionable
Chinese messages, no stack traces, no credentials.
"""
import re
import socket
import ssl
import time
from datetime import datetime, timezone

ENGINES = ('mysql', 'redis')
TLS_MODES = ('none', 'encrypted', 'verify')
CONVERSIONS = ('number', 'integer', 'text')

_HOST = re.compile(r'^[A-Za-z0-9._-]{1,253}$')
_NAME = re.compile(r'^[^\x00\r\n]{1,128}$')


def _text(value, label, limit=128, required=False):
    out = str(value if value is not None else '').strip()
    if len(out) > limit or '\x00' in out:
        raise ValueError(f'{label}内容无效')
    if required and not out:
        raise ValueError(f'请填写{label}')
    return out


def _port(value, default):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    if not 1 <= port <= 65535:
        raise ValueError('端口需要在 1～65535 之间')
    return port


def normalize_config(payload):
    """Validate and normalize a connection form; raises ValueError (sanitized)."""
    if not isinstance(payload, dict):
        raise ValueError('连接配置无效')
    engine = payload.get('engine')
    if engine not in ENGINES:
        raise ValueError('连接引擎仅支持 MySQL 与 Redis')
    out = {
        'engine': engine,
        'id': _text(payload.get('id'), '连接标识', 64) if payload.get('id') else '',
        'name': _text(payload.get('name'), '连接名称', 80, required=True),
        'host': _text(payload.get('host'), '主机地址', 253, required=True),
    }
    if not _HOST.match(out['host']):
        raise ValueError('主机地址格式无效（仅支持域名或 IP）')
    out['port'] = _port(payload.get('port'), 3306 if engine == 'mysql' else 6379)
    out['username'] = _text(payload.get('username'), '用户名', 128)
    out['tls'] = payload.get('tls') or 'none'
    if out['tls'] not in TLS_MODES:
        raise ValueError('TLS 设置无效')
    out['caPath'] = _text(payload.get('caPath'), 'CA 证书路径', 253) if out['tls'] == 'verify' else ''
    if engine == 'mysql':
        out['database'] = _text(payload.get('database'), '数据库名称', 64, required=True)
        if not _NAME.match(out['database']):
            raise ValueError('数据库名称无效')
    else:
        try:
            out['dbIndex'] = int(payload.get('dbIndex', 0))
        except (TypeError, ValueError):
            raise ValueError('Redis DB 索引需要是整数') from None
        if not 0 <= out['dbIndex'] < 2**31:
            raise ValueError('Redis DB 索引超出范围')
    return out


def _fail(category, message, started=None):
    out = {'ok': False, 'category': category, 'message': message}
    if started is not None:
        out['latencyMs'] = int((time.monotonic() - started) * 1000)
    return out


def _classify(exc, started=None):
    """Map a raw driver/socket exception to a sanitized category + message."""
    import socket as _socket
    name = type(exc).__name__
    text = str(exc)
    if isinstance(exc, ImportError):
        which = 'PyMySQL（pip install PyMySQL）' if 'pymysql' in text else 'redis（pip install redis）'
        return _fail('driver', f'驱动未安装：{which}；安装后重启工作台即可使用真实测试')
    if isinstance(exc, (_socket.gaierror,)) or 'nodename nor servname' in text or 'Name or service not known' in text:
        return _fail('dns', '地址解析失败：请检查主机名拼写或本机 DNS 设置', started)
    if isinstance(exc, ssl.SSLError) or isinstance(exc, ssl.SSLCertVerificationError) or 'certificate' in text.lower() and 'verify' in text.lower():
        return _fail('tls', 'TLS 握手或证书校验失败：请核对 TLS 设置与证书', started)
    if isinstance(exc, ConnectionRefusedError) or 'Connection refused' in text:
        return _fail('refused', '连接被拒绝：服务可能未启动，或端口／防火墙配置不正确', started)
    if name in ('TimeoutError',) or isinstance(exc, (TimeoutError, socket.timeout)) or 'timed out' in text.lower() or 'timeout' in text.lower():
        return _fail('timeout', '连接超时：请检查网络可达性、端口与数据库白名单', started)
    if name in ('AuthenticationError',) or 'authentication' in text.lower() or 'access denied' in text.lower() or 'using password' in text.lower() or 'wrongpass' in text.lower() or 'invalid password' in text.lower():
        return _fail('auth', '认证失败：请核对用户名、密码或受保护凭据是否有效', started)
    if 'unknown database' in text.lower() or 'db index is out of range' in text.lower() or 'no such database' in text.lower():
        return _fail('database', '数据库不存在或不可访问：请核对库名／Redis DB 索引', started)
    if 'command denied' in text.lower() or 'permission' in text.lower() or 'privilege' in text.lower() or 'read-only' in text.lower() and name == 'OperationalError':
        return _fail('permission', '权限不足：该账号无权访问目标数据库', started)
    if isinstance(exc, (ConnectionError, OSError)) or name in ('OperationalError', 'InterfaceError', 'ConnectionError', 'DatabaseError'):
        return _fail('unreachable', '连接失败：服务不可达或驱动报错，请核对地址与端口', started)
    return _fail('unknown', '测试失败：' + name, started)


def _mysql_connect(cfg, secret, connect_timeout=5, read_timeout=8):
    import pymysql
    tls = cfg.get('tls', 'none')
    kwargs = {}
    if tls == 'none':
        kwargs['ssl_disabled'] = True
    elif tls == 'verify':
        kwargs['ssl'] = {'ca': cfg.get('caPath') or None, 'cert_reqs': ssl.CERT_REQUIRED}
    else:
        kwargs['ssl_disabled'] = False
    return pymysql.connect(host=cfg['host'], port=cfg['port'], user=cfg.get('username') or '',
                           password=secret or '', database=cfg['database'],
                           connect_timeout=connect_timeout, read_timeout=read_timeout,
                           write_timeout=read_timeout, charset='utf8mb4', autocommit=True, **kwargs)


def _redis_connect(cfg, secret, connect_timeout=5, socket_timeout=8):
    import redis
    tls = cfg.get('tls', 'none')
    kwargs = {}
    if tls != 'none':
        kwargs['ssl'] = True
        kwargs['ssl_cert_reqs'] = 'required' if tls == 'verify' else 'none'
        if tls == 'verify' and cfg.get('caPath'):
            kwargs['ssl_ca_certs'] = cfg['caPath']
    return redis.Redis(host=cfg['host'], port=cfg['port'], db=cfg.get('dbIndex', 0),
                       username=cfg.get('username') or None, password=secret or None,
                       socket_connect_timeout=connect_timeout, socket_timeout=socket_timeout, **kwargs)


def probe(cfg, secret):
    """Run the engine-specific health probe with the given secret."""
    started = time.monotonic()
    try:
        if cfg['engine'] == 'mysql':
            connection = _mysql_connect(cfg, secret)
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1')
                    cursor.fetchall()
            finally:
                connection.close()
        else:
            client = _redis_connect(cfg, secret)
            try:
                client.ping()
            finally:
                client.close()
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — all driver errors funnel into classification
        return _classify(exc, started)
    return {'ok': True, 'category': 'ok',
            'message': '连接成功：服务可达、认证与基本命令可用（不代表表权限或业务数据已验证）',
            'latencyMs': int((time.monotonic() - started) * 1000)}


def catalog(cfg, secret):
    """Read table/view and column metadata for a MySQL connection."""
    if cfg['engine'] != 'mysql':
        return _fail('config', 'Redis 连接没有表结构目录；请选择 MySQL 连接')
    started = time.monotonic()
    try:
        connection = _mysql_connect(cfg, secret)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        return _classify(exc, started)
    try:
        tables = {}
        with connection.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW') "
                           "ORDER BY TABLE_NAME", (cfg['database'],))
            for name, kind in cursor.fetchall():
                tables[name] = {'name': name, 'kind': 'view' if kind == 'VIEW' else 'table', 'fields': []}
            cursor.execute("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY, COLUMN_COMMENT "
                           "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s "
                           "ORDER BY TABLE_NAME, ORDINAL_POSITION", (cfg['database'],))
            for table, column, data_type, column_key, comment in cursor.fetchall():
                entry = tables.get(table)
                if entry is None:
                    continue
                entry['fields'].append({'name': column, 'dataType': data_type or '',
                                        'key': {'PRI': 'pri', 'UNI': 'uni'}.get(column_key or '', ''),
                                        'comment': (comment or '')[:200]})
    except Exception as exc:  # noqa: BLE001
        return _classify(exc, started)
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 — closing a broken socket must not mask the result
            pass
    if not tables:
        return _fail('permission', '未读取到任何表或视图：请核对数据库名称与账号权限')
    return {'ok': True, 'database': cfg['database'],
            'tables': list(tables.values()),
            'refreshedAt': datetime.now(timezone.utc).isoformat(),
            'message': f'已读取 {len(tables)} 张表／视图的结构目录（不含数据内容）'}
