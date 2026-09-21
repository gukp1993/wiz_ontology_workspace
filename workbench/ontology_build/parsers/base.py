"""解析器统一契约：Fact 结构、定位器、覆盖报告与调度入口。

所有格式/框架适配器只产出 `Fact`（带精确定位的事实记录），不直接产生候选定义；
候选由生成管线（llm/alignment）在证据之上抽象。适配器不得执行材料内容
（不 pip install、不运行代码、不执行 SQL/宏、不访问外链）。

解析结果结构：
    ParseResult(facts=[Fact...], coverage={...}, warnings=[...], partial=bool, error='')
coverage 至少包含：
    {'modules': [...], 'notes': [...], 'failedSegments': [...], 'factCount': n}
失败必须显式报告（partial/failed），绝不能静默当空文件继续。
"""
import hashlib

from workbench.ontology_build import protocol


class Fact:
    """一条带定位的事实记录（任务侧数据，不进本体协议）。

    locator 必须来自真实解析：code{file,line,symbol} / ddl{file,line,table,column} /
    docx{file,section,block} / pdf{file,page} / xlsx{file,sheet,cell} / md{file,section,line}。
    """

    __slots__ = ('id', 'material_id', 'module', 'locator', 'snippet', 'kind', 'data', 'quality')

    def __init__(self, material_id, module, locator, snippet, kind, data=None, quality='high'):
        self.material_id = material_id
        self.module = str(module or '')
        self.locator = locator if isinstance(locator, dict) else {}
        self.snippet = protocol.clamp_snippet(snippet)
        self.kind = str(kind or '')
        self.data = data if isinstance(data, dict) else {}
        self.quality = quality if quality in ('high', 'medium', 'low') else 'medium'
        self.id = 'bf-' + _stable_suffix(material_id, self.locator, self.snippet)

    def to_dict(self):
        return {'id': self.id, 'materialId': self.material_id, 'module': self.module,
                'locator': self.locator, 'snippet': self.snippet, 'kind': self.kind,
                'data': self.data, 'quality': self.quality}


def _stable_suffix(material_id, locator, snippet):
    raw = '%s|%s|%s' % (material_id, protocol_json(locator), snippet[:120])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]


def protocol_json(value):
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ParseResult:
    __slots__ = ('facts', 'coverage', 'warnings', 'partial', 'error', 'excluded_paths', 'kind')

    def __init__(self, facts=None, coverage=None, warnings=None, partial=False, error='',
                 excluded_paths=None, kind='other'):
        self.facts = list(facts or [])
        self.coverage = dict(coverage or {})
        self.warnings = list(warnings or [])
        self.partial = bool(partial)
        self.error = str(error or '')
        self.excluded_paths = list(excluded_paths or [])
        self.kind = kind

    def coverage_payload(self):
        payload = {
            'modules': self.coverage.get('modules') or [],
            'notes': (self.coverage.get('notes') or []) + self.warnings,
            'failedSegments': self.coverage.get('failedSegments') or [],
            'factCount': len(self.facts),
            'parseState': 'failed' if self.error and not self.facts else ('partial' if self.partial or self.error else 'success'),
        }
        if self.excluded_paths:
            payload['excludedPaths'] = self.excluded_paths
        if self.error:
            payload['error'] = self.error
        return payload


def failure(error, coverage=None, warnings=None):
    return ParseResult(coverage=coverage, warnings=warnings, partial=False, error=error)


# 适配器登记表由 parsers/__init__.py 填充：kind → callable(path, material_id, kind) -> ParseResult
REGISTRY = {}


def register(kind, func):
    REGISTRY[kind] = func
    return func


def parse(kind, path, material_id, rel_path=''):
    """按材料类型调度解析；未知类型走文本线索降级（明确标注覆盖不足）。"""
    handler = REGISTRY.get(kind) or REGISTRY.get('other')
    if handler is None:
        return failure('没有可用的解析器')
    return handler(str(path), material_id, rel_path or '')
