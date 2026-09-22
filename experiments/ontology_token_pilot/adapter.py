# -*- coding: utf-8 -*-
"""ontology_token_pilot 试验适配层：模型适配 / 配置加载 / A/B/C/D 四臂运行（D14）。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§8 D14 行、§11（独立试验与共享
核心）、§12（G2 判定口径）；CLI 用法冻结见本包 ``__main__.py`` 与 README.md。

铁律（违反即架构回归）：
* **调度唯一核心** = workbench/ontology_build/batch_execution.py（D09 共享执行器）。
  本模块不复制任何状态机/装箱/拆分/重试/事务逻辑：计划输入只经
  batch_state.create_plan/apply_event 与 batch_plan.plan_initial/job_definition 组装，
  运行只走 batch_execution.make_context/run_plan；持久化只经 D12 ExperimentState。
  A/B 对照臂与共享核心的**唯一**差异是注入固定批 planner（batch_execution._plan_more
  的既有注入点），差异点全部登记在结果 meta 的 ``adapterDiffs`` 字段——不重写任何
  "声称等价"的旧调度器（§11）。
* **真实 key 只留在内存 provider dict**：RealModel 的 provider 由调用方构造
  （real 模式经 workbench.llm_providers 解析），绝不进 CLI 参数/配置文件/报告/日志/
  结果文件；结果落盘前统一过 D13 redact_report。
* **默认 fake，真实调用需显式 --mode real 授权**；fake 模式不 import workbench 存储
  层/LLM 客户端（本模块顶部只有 ontology_build 纯函数与 D12/D13 实验模块；
  llm_client/llm_providers 的 import 收敛在 RealModel/resolve_real_provider 内部）。
* campaign 预算跨臂/样本/重复/续跑共享：``<output-dir>/states/<campaign>/campaign.sqlite3``
  存累计（物理请求 attempts / 已知 completion / 活跃 ms），余量经 run 级
  budget（maxAttempts/maxWallMs/completionSoftLimit）传给共享执行器，达线不再派发；
  模型响应暖缓存也放同一 campaign 库（cache_put 跨 repeat 共用）。

四臂定义（§11 冻结）：
* A = legacy-v1 + 固定批对照 planner；B = compact-v1 + 同 A 的固定批 planner；
* C = legacy-v1 + v3 共享核心（plan_initial + 缺省 pack_next）；D = compact-v1 + 同 C。
* 并发全臂 1（串行执行，避免并发成为混杂变量）。
"""
import hashlib
import json
import os
import re

from workbench.ontology_build import batch_contracts as contracts
from workbench.ontology_build import batch_execution as executor
from workbench.ontology_build import batch_plan
from workbench.ontology_build import batch_state
from workbench.ontology_build import budget
from workbench.ontology_build import protocol
from workbench.ontology_build import semantic_units
from experiments.ontology_token_pilot import evaluate
from experiments.ontology_token_pilot import state as exp_state

__all__ = [
    'ConfigError', 'FakeModel', 'RealModel', 'resolve_real_provider',
    'load_config', 'build_budget_profile', 'load_sample_facts', 'goldens_for_sample',
    'run_arm', 'ledger_from_result', 'ARMS', 'ARM_CODEC', 'CAMPAIGN_COUNTERS_KEY',
]

# --- 臂定义（§11 冻结：A/B 对照调度、C/D v3 共享核心；codec 与 planner 正交组合） ------

ARMS = ('A', 'B', 'C', 'D')
ARM_CODEC = {
    'A': contracts.CODEC_LEGACY,
    'B': contracts.CODEC_COMPACT,
    'C': contracts.CODEC_LEGACY,
    'D': contracts.CODEC_COMPACT,
}
ARM_PLANNER = {'A': 'fixed-batch', 'B': 'fixed-batch', 'C': 'v3-pack-next', 'D': 'v3-pack-next'}

# A/B 与共享核心的全部差异点（登记进结果 meta.adapterDiffs；§11「所有适配差异登记」）
FIXED_BATCH_ADAPTER_DIFFS = [
    '固定20批 planner：每 job 固定 %d 事实（protocol.LLM_BATCH_FACTS），不经 '
    'batch_plan.pack_next 的语义单元聚合/硬预算/软目标装箱与冷启动估算' % protocol.LLM_BATCH_FACTS,
    'A/B 初始计划不含 plan_initial 初始作业：全部目标保持 pending，由注入 planner 一次性分批',
    '调度/持久化/恢复/重试/拆分仍全部走 batch_execution 共享核心（无第二套调度器）',
]

RESULT_SCHEMA_VERSION = 1
RESULT_KIND = 'ontology-token-pilot-run'
# 终态结果不再重跑（续跑只补缺口；blocked/failed 视已定案，重试须显式清理该 run 的
# 结果与状态文件——README 说明）。stalled/error 视为可重试。
_TERMINAL_RUN_STATES = ('succeeded', 'failed', 'blocked')

CAMPAIGN_COUNTERS_KEY = 'campaign:budget:v1'      # campaign.sqlite3 里的累计预算计数
CAMPAIGN_DEFAULTS = {'maxAttempts': 128, 'maxCompletionTokens': 200000, 'maxWallMs': 7200000}
DEFAULT_SAMPLE_IDS = ('simple', 'amplified', 'schema_conflict')
_DEFAULT_SAMPLES_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tests',
    'fixtures', 'ontology_token_pilot', 'samples'))


class ConfigError(ValueError):
    """试验配置缺失/非法（CLI 退出码 1：参数错误）。"""


# ---------------------------------------------------------------------------
# 配置加载（非敏感字段；key 永不出现在配置里）
# ---------------------------------------------------------------------------

def _resolve_path(path, base_dir):
    text = str(path or '').strip()
    if not text:
        return ''
    if not os.path.isabs(text):
        text = os.path.join(base_dir, text)
    return os.path.normpath(text)


def _positive_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError('%s 需要正整数，实际 %r' % (field, value))
    return value


def load_config(path):
    """读取并规整试验配置 JSON（缺省项补默认；路径相对配置文件所在目录解析）。

    冻结字段：providerId / profile{contextTokens,outputLimitTokens,requestOutputTokens,
    targetRatio} / scope / sampleIds / goldenDir / campaignId /
    campaignBudget{maxAttempts,maxCompletionTokens,maxWallMs}。
    可选扩展：samplesDir（默认仓库 fixture 样本目录）、fakeScript（fake 模式事件序列）、
    model（fake 模式的模型名标记；真实模型名以 provider 为准）。
    **配置不含 api key**——真实 key 只经 workbench.llm_providers 在内存解析。
    """
    if not path:
        raise ConfigError('缺少 --config 配置路径')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            raw = json.load(handle)
    except OSError as exc:
        raise ConfigError('配置文件不可读：%s（%s）' % (path, exc)) from None
    except ValueError as exc:
        raise ConfigError('配置不是合法 JSON：%s（%s）' % (path, exc)) from None
    if not isinstance(raw, dict):
        raise ConfigError('配置顶层必须是 JSON 对象：%s' % path)
    base_dir = os.path.dirname(os.path.abspath(path))

    provider_id = str(raw.get('providerId') or '').strip()
    if not provider_id:
        raise ConfigError('配置缺少 providerId（非敏感标识；真实 key 不入配置）')
    campaign_id = str(raw.get('campaignId') or '').strip()
    if not campaign_id:
        raise ConfigError('配置缺少 campaignId')
    golden_dir = _resolve_path(raw.get('goldenDir'), base_dir)
    if not golden_dir or not os.path.isdir(golden_dir):
        raise ConfigError('配置 goldenDir 不存在：%r' % (raw.get('goldenDir'),))

    profile_raw = raw.get('profile') if isinstance(raw.get('profile'), dict) else {}
    profile = {
        'contextTokens': _positive_int(profile_raw.get('contextTokens', 131072),
                                       'profile.contextTokens'),
        'outputLimitTokens': _positive_int(profile_raw.get('outputLimitTokens', 32000),
                                           'profile.outputLimitTokens'),
        'requestOutputTokens': _positive_int(profile_raw.get('requestOutputTokens',
                                                             contracts.DEFAULT_REQUEST_OUTPUT_TOKENS),
                                             'profile.requestOutputTokens'),
        'targetRatio': float(profile_raw.get('targetRatio', contracts.DEFAULT_TARGET_RATIO)),
    }
    if not (contracts.TARGET_RATIO_MIN <= profile['targetRatio'] <= contracts.TARGET_RATIO_MAX):
        raise ConfigError('profile.targetRatio 越界（%.2f–%.2f）：%r'
                          % (contracts.TARGET_RATIO_MIN, contracts.TARGET_RATIO_MAX,
                             profile['targetRatio']))

    sample_ids = raw.get('sampleIds')
    if not isinstance(sample_ids, list) or not sample_ids:
        sample_ids = list(DEFAULT_SAMPLE_IDS)
    sample_ids = [str(item).strip() for item in sample_ids if str(item).strip()]
    if not sample_ids:
        raise ConfigError('配置 sampleIds 为空')

    budget_raw = raw.get('campaignBudget') if isinstance(raw.get('campaignBudget'), dict) else {}
    campaign_budget = {
        'maxAttempts': _positive_int(budget_raw.get('maxAttempts', CAMPAIGN_DEFAULTS['maxAttempts']),
                                     'campaignBudget.maxAttempts'),
        'maxCompletionTokens': _positive_int(
            budget_raw.get('maxCompletionTokens', CAMPAIGN_DEFAULTS['maxCompletionTokens']),
            'campaignBudget.maxCompletionTokens'),
        'maxWallMs': _positive_int(budget_raw.get('maxWallMs', CAMPAIGN_DEFAULTS['maxWallMs']),
                                   'campaignBudget.maxWallMs'),
    }

    scope = raw.get('scope')
    scope = scope if isinstance(scope, dict) else {}
    fake_script = raw.get('fakeScript')
    if fake_script is not None and not isinstance(fake_script, list):
        raise ConfigError('fakeScript 需要事件数组（如 ["length","bad_json"]）')

    samples_dir = _resolve_path(raw.get('samplesDir') or _DEFAULT_SAMPLES_DIR, base_dir)
    if not os.path.isdir(samples_dir):
        raise ConfigError('样本目录不存在：%r（可用 samplesDir 指定）' % samples_dir)

    return {
        'providerId': provider_id,
        'model': str(raw.get('model') or '').strip(),
        'campaignId': campaign_id,
        'profile': profile,
        'scope': scope,
        'sampleIds': sample_ids,
        'goldenDir': golden_dir,
        'samplesDir': samples_dir,
        'campaignBudget': campaign_budget,
        'fakeScript': [item for item in (fake_script or [])],
    }


def build_budget_profile(config, model_name):
    """config.profile → 冻结 BudgetProfile（复用 D01 contracts.build_profile，不另立口径）。

    model_name：fake 模式用配置标记名，real 模式用 provider 实际模型；进计划指纹的
    非密部分。profile 非法（缺冻结值/绑定缺失）抛 ConfigError（CLI 退出码 1）。
    """
    profile_cfg = config['profile']
    env = {
        'WIZ_BUILD_ADAPTIVE_BATCHING': '1',
        'WIZ_BUILD_PROFILE_PROVIDER_ID': config['providerId'],
        'WIZ_BUILD_PROFILE_MODEL': str(model_name or 'unknown-model'),
        'WIZ_BUILD_CONTEXT_TOKENS': str(profile_cfg['contextTokens']),
        'WIZ_BUILD_OUTPUT_LIMIT_TOKENS': str(profile_cfg['outputLimitTokens']),
        'WIZ_BUILD_REQUEST_OUTPUT_TOKENS': str(profile_cfg['requestOutputTokens']),
        'WIZ_BUILD_OUTPUT_TARGET_RATIO': str(profile_cfg['targetRatio']),
    }
    profile = contracts.build_profile(env=env)
    if profile.get('errors') or not (profile.get('effective') or {}).get('outputCap'):
        raise ConfigError('预算 profile 非法，无法装箱：%s'
                          % json.dumps(profile.get('errors') or ['effective 缺 outputCap'],
                                       ensure_ascii=False))
    return profile


# ---------------------------------------------------------------------------
# 模型适配（CallResult 形状 = batch_contracts「单次调用结果」冻结契约）
# ---------------------------------------------------------------------------

_DATA_TYPE_OF_VALUE = (
    (bool, 'boolean'), (int, 'number'), (float, 'number'),
    (list, 'array'), (dict, 'struct'),
)


class FakeModelCrash(RuntimeError):
    """fake 脚本 'crash' 事件的内部异常（call 包装层转 HTTP_UNKNOWN，不上抛执行器）。"""


def _data_type_of(value):
    for vtype, name in _DATA_TYPE_OF_VALUE:
        if isinstance(value, vtype):
            return name
    return 'text'


def _user_payload(messages):
    """messages → user 载荷 dict（解析失败返回 {}，Fake 按空目标处理）。"""
    for message in messages or []:
        if isinstance(message, dict) and message.get('role') == 'user':
            try:
                data = json.loads(str(message.get('content') or ''))
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def _fake_usage(user_content, response_text):
    prompt_bytes = len(str(user_content or '').encode('utf-8'))
    completion_bytes = len(str(response_text or '').encode('utf-8'))
    return {'prompt_tokens': max(1, prompt_bytes),
            'completion_tokens': max(1, completion_bytes),
            'total_tokens': prompt_bytes + completion_bytes,
            'usageSource': 'api'}


def _call_result(content, finish_reason, usage, error_code=None, retryable=False):
    response_bytes = len(str(content or '').encode('utf-8')) if content is not None else 0
    return {'ok': error_code is None, 'content': content, 'finishReason': finish_reason,
            'errorCode': error_code, 'retryable': bool(retryable), 'usage': usage,
            'requestBytes': 0, 'responseBytes': response_bytes, 'durationMs': 5}


def _uniq_key(base, used):
    key = re.sub(r'[^0-9a-z]+', '_', str(base or '').strip().lower()).strip('_')[:50] or 'cand'
    candidate, index = key, 1
    while candidate in used:
        index += 1
        candidate = '%s_%d' % (key, index)
    used.add(candidate)
    return candidate


def _subject_of(data, unit):
    for field in ('name', 'title', 'table', 'id', 'key'):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = str(unit.get('subjectKey') or unit.get('unit') or '')
    return text.rsplit('#', 1)[-1] or 'object'


def _fake_candidates(units, ref_of):
    """按请求单元造结构合法候选：每单元一个对象候选 + 至多 4 个带 dataType 的属性。

    仅用于 fake 冒烟（结构/覆盖/证据引用合法），**不代表真实模型质量**——G2 质量判定
    属 D18 真实试验。jsonld 节点的 attributes 字典展开为属性字段。
    """
    candidates = []
    used = set()
    for unit in units:
        ref = ref_of(unit)
        if not ref:
            continue
        data = unit.get('data') if isinstance(unit.get('data'), dict) else {}
        subject = _subject_of(data, unit)
        obj_key = _uniq_key(subject, used)
        candidates.append({
            'key': obj_key, 'type': 'object', 'name': subject[:60],
            'definition': 'fake 合成候选：%s' % subject,
            'fields': {}, 'ownerKey': '',
            'evidence': {'name': [ref]}, 'evidenceStatus': 'supported', 'conflicts': [],
        })
        fields = dict(data)
        attributes = data.get('attributes')
        if isinstance(attributes, dict):
            fields.update(attributes)
        for name in sorted(fields):
            if name.startswith('@') or name in ('name', 'title'):
                continue
            value = fields[name]
            prop_key = _uniq_key('%s_%s' % (obj_key, name), used)
            candidates.append({
                'key': prop_key, 'type': 'property', 'name': str(name)[:60],
                'definition': 'fake 合成属性：%s.%s' % (subject, name),
                'fields': {'dataType': _data_type_of(value)},
                'ownerKey': obj_key,
                'evidence': {'name': [ref], 'dataType': [ref], 'ownerKey': [ref]},
                'evidenceStatus': 'supported', 'conflicts': [],
            })
            if len(candidates) >= protocol.MAX_CANDIDATES_PER_BATCH - 1:
                break
    return candidates


class FakeModel(object):
    """假模型：默认按序生成能通过对应 codec decode 的 stop 响应；脚本注入异常事件。

    script 事件（按**每次 run 的物理调用序**回放，耗尽后回落默认正常响应）：
    * 'length'       —— finish_reason=length 截断（触发共享核心 split_or_block）；
    * 'rate_limited' —— 429（RATE_LIMITED，可重试，验证退避重试记账）；
    * 'bad_json'     —— 非法 JSON（触发每 job 至多 1 次格式修复）；
    * 'crash'        —— 模型进程崩溃（包装层转 HTTP_UNKNOWN 不可重试）；
    * 'unknown_usage'—— 正常 stop 但 usage 缺失（unknown 不计 0 口径）。
    事件也接受 {'event': 'length'} 形状。候选内容按请求事实造合法候选：
    legacy 用真实 factId 作证据、compact 用局部别名并逐单元回 coverage（全命中，
    否则整叶 COVERAGE_INCOMPLETE）。
    """

    fake = True

    _EVENTS = ('length', 'rate_limited', 'bad_json', 'crash', 'unknown_usage')

    def __init__(self, script=None, model_name='fake-model'):
        self.model_name = str(model_name or 'fake-model')
        self.script = []
        for item in (script or []):
            event = item.get('event') if isinstance(item, dict) else str(item)
            event = str(event or '').strip()
            if event and event not in self._EVENTS:
                raise ConfigError('fakeScript 未知事件 %r（允许 %s）' % (event, self._EVENTS))
            self.script.append(event)
        self.calls = 0

    def __call__(self, messages, max_tokens):
        self.calls += 1
        event = self.script.pop(0) if self.script else ''
        if event == 'crash':
            raise FakeModelCrash('fake 模型崩溃（脚本注入）')
        if event == 'rate_limited':
            return _call_result(None, None, contracts.empty_usage(),
                                error_code='RATE_LIMITED', retryable=True)
        payload = _user_payload(messages)
        user_content = ''
        for message in messages or []:
            if isinstance(message, dict) and message.get('role') == 'user':
                user_content = str(message.get('content') or '')
        if payload.get('outputContract') == contracts.CODEC_COMPACT:
            return self._respond_compact(payload, user_content, event)
        return self._respond_legacy(payload, user_content, event)

    def _respond_legacy(self, payload, user_content, event):
        units = [item for item in (payload.get('facts') or []) if isinstance(item, dict)]
        if event == 'length':
            text = '{"candidates": [{"key": "trunc'
            return _call_result(text, 'length', _fake_usage(user_content, text))
        candidates = _fake_candidates(units, lambda unit: str(unit.get('id') or ''))
        if event == 'bad_json':
            text = '这不是 JSON 响应'
            return _call_result(text, 'stop', _fake_usage(user_content, text))
        response = {'candidates': candidates, 'notes': ['fake model 响应']}
        text = json.dumps(response, ensure_ascii=False)
        usage = contracts.empty_usage() if event == 'unknown_usage' \
            else _fake_usage(user_content, text)
        return _call_result(text, 'stop', usage)

    def _respond_compact(self, payload, user_content, event):
        # encode_request 的实现把目标单元放在 payload['facts']（compact 条目带
        # 'alias'/'unit'，不带真实 id）；与 output_codec.py 实际协议一致。
        units = [item for item in (payload.get('facts') or []) if isinstance(item, dict)]
        if event == 'length':
            text = '{"codecVersion": "compact-v1", "candid'
            return _call_result(text, 'length', _fake_usage(user_content, text))
        candidates = _fake_candidates(units, lambda unit: str(unit.get('alias') or ''))
        coverage = [{'unit': str(unit.get('unit') or ''), 'status': 'processed'}
                    for unit in units if unit.get('unit')]
        if event == 'bad_json':
            text = '这不是 JSON 响应'
            return _call_result(text, 'stop', _fake_usage(user_content, text))
        response = {'codecVersion': contracts.CODEC_COMPACT,
                    'candidates': candidates, 'coverage': coverage}
        text = json.dumps(response, ensure_ascii=False)
        usage = contracts.empty_usage() if event == 'unknown_usage' \
            else _fake_usage(user_content, text)
        return _call_result(text, 'stop', usage)


class RealModel(object):
    """真实调用适配：workbench.llm_client.chat_once_result（D05 冻结 CallResult）。

    provider dict（endpoint/model/api_key/…）由调用方构造并**只留在内存**；本类不打印、
    不持久化、不回传其中任何字段。llm_client 的 import 收敛在这里（fake 模式零依赖）。
    """

    fake = False

    def __init__(self, provider, model_name=''):
        from workbench import llm_client   # 仅真实调用时 import（fake 模式零 workbench 客户端依赖）
        self._llm_client = llm_client
        self._provider = dict(provider) if isinstance(provider, dict) else {}
        self.model_name = str(model_name or self._provider.get('model') or 'unknown-model')

    def __call__(self, messages, max_tokens):
        return self._llm_client.chat_once_result(self._provider, messages, max_tokens)


def resolve_real_provider(provider_id):
    """providerId → 完整 provider dict（含解密 key，仅内存）。

    只在显式 real 模式下调用（内部才 import workbench 存储层/llm_providers）。
    失败抛 ProviderUnavailable 语义的普通异常，由 CLI 转「模型不可用」受阻（退出码 2）。
    """
    from workbench import llm_providers   # 仅真实模式 import 存储层
    provider = llm_providers.resolve(provider_id)
    if not isinstance(provider, dict) or not str(provider.get('endpoint') or '').strip() \
            or not str(provider.get('model') or '').strip():
        raise ValueError('LLM 提供方 %r 缺 endpoint/model，无法真实调用' % provider_id)
    return provider


# ---------------------------------------------------------------------------
# 样本事实与金样（确定性合成事实：文件 → fact dict → semantic_units.build_targets）
# ---------------------------------------------------------------------------

def _clip_text(text, limit=480):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '…'


def _fact_id(material, path_key):
    digest = hashlib.sha1(('%s|%s' % (material, path_key)).encode('utf-8')).hexdigest()
    return 'f-' + digest[:12]


def _facts_from_json(material, filename, root):
    """JSON/JSON-LD → 确定性 fact 列表：@graph 节点按 nodeId 定位；普通 JSON 按键路径。

    每个 dict 节点一个 fact（data=该节点）；标量留在父节点 data 内不单独成 fact
    （铁律「每个 fact 至少一个 target」由 semantic_units 保证，这里只保证不丢 dict 结构）。
    """
    facts = []
    if isinstance(root, dict) and isinstance(root.get('@graph'), list):
        for index, node in enumerate(root['@graph']):
            if not isinstance(node, dict):
                continue
            node_id = str(node.get('@id') or 'node%d' % index)
            facts.append({'id': _fact_id(material, 'graph:' + node_id),
                          'materialId': material,
                          'locator': {'kind': 'jsonld', 'file': filename, 'nodeId': node_id},
                          'snippet': _clip_text(json.dumps(node, ensure_ascii=False)),
                          'kind': 'jsonld', 'data': node, 'quality': 'synthetic'})
        return facts

    def walk(node, path):
        if isinstance(node, dict):
            facts.append({'id': _fact_id(material, path), 'materialId': material,
                          'locator': {'kind': 'json', 'file': filename, 'path': path},
                          'snippet': _clip_text(json.dumps(node, ensure_ascii=False)),
                          'kind': 'json', 'data': node, 'quality': 'synthetic'})
            for key in sorted(node):
                walk(node[key], '%s.%s' % (path, key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, '%s[%d]' % (path, index))

    walk(root, '$')
    return facts


_CREATE_TABLE_RE = re.compile(r'CREATE\s+TABLE\s+["`]?(\w+)["`]?\s*\(', re.IGNORECASE)


def _facts_from_sql(material, filename, text):
    """DDL/规则文本 → fact：每个 CREATE TABLE 一个 ddl fact；其余非空行合一个 text fact。"""
    facts = []
    matches = list(_CREATE_TABLE_RE.finditer(text))
    spans = []
    for match in matches:
        table = match.group(1)
        end = text.find(';', match.start())
        end = len(text) if end < 0 else end + 1
        body = text[match.start():end]
        spans.append((match.start(), end))
        columns = []
        for line in body.splitlines()[1:]:
            stripped = line.strip().rstrip(',')
            if not stripped or stripped.upper().startswith(
                    ('PRIMARY', 'UNIQUE', 'CONSTRAINT', 'FOREIGN', 'CHECK')):
                continue
            columns.append(stripped.split()[0].strip('"`'))
        facts.append({'id': _fact_id(material, 'table:' + table), 'materialId': material,
                      'locator': {'kind': 'ddl', 'file': filename, 'table': table},
                      'snippet': _clip_text(body), 'kind': 'ddl',
                      'data': {'table': table, 'columns': columns}, 'quality': 'synthetic'})
    rest = []
    position = 0
    for start, end in spans:
        rest.append(text[position:start])
        position = end
    rest.append(text[position:])
    rest_text = '\n'.join(line for line in '\n'.join(rest).splitlines() if line.strip())
    if rest_text.strip():
        facts.append({'id': _fact_id(material, 'text'), 'materialId': material,
                      'locator': {'kind': 'text', 'file': filename},
                      'snippet': _clip_text(rest_text), 'kind': 'text',
                      'data': {'text': _clip_text(rest_text, 2000)}, 'quality': 'synthetic'})
    return facts


def load_sample_facts(config, sample):
    """样本目录 → (fact 列表, facts_by_id)；确定性（同输入恒同输出）。

    文件类型：.json/.jsonld 按 JSON(JSON-LD) 走；.sql 按 DDL/文本走；其余按纯文本
    单 fact。sampleId 必须是 config.sampleIds 之一且目录存在。
    """
    if sample not in (config.get('sampleIds') or []):
        raise ConfigError('样本 %r 不在配置 sampleIds 内：%s' % (sample, config.get('sampleIds')))
    sample_dir = os.path.join(config['samplesDir'], sample)
    if not os.path.isdir(sample_dir):
        raise ConfigError('样本目录不存在：%s' % sample_dir)
    facts = []
    for name in sorted(os.listdir(sample_dir)):
        path = os.path.join(sample_dir, name)
        if not os.path.isfile(path):
            continue
        material = '%s/%s' % (sample, name)
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            text = handle.read()
        lower = name.lower()
        if lower.endswith(('.json', '.jsonld')):
            try:
                root = json.loads(text)
            except ValueError:
                root = None
            if root is not None:
                facts.extend(_facts_from_json(material, name, root))
                continue
        if lower.endswith('.sql'):
            facts.extend(_facts_from_sql(material, name, text))
            continue
        facts.append({'id': _fact_id(material, 'text'), 'materialId': material,
                      'locator': {'kind': 'text', 'file': name},
                      'snippet': _clip_text(text), 'kind': 'text',
                      'data': {'text': _clip_text(text, 2000)}, 'quality': 'synthetic'})
    if not facts:
        raise ConfigError('样本 %s 未产出任何事实（目录为空？）' % sample)
    facts_by_id = {}
    for fact in facts:
        if fact['id'] in facts_by_id:
            raise ConfigError('事实 id 冲突：%s（样本 %s）' % (fact['id'], sample))
        facts_by_id[fact['id']] = fact
    return facts, facts_by_id


def goldens_for_sample(config, sample):
    """金样目录中 goldenFor 指向 samples/<sample>/ 的全部金样（按文件名稳定序）。"""
    goldens = []
    golden_dir = config['goldenDir']
    for name in sorted(os.listdir(golden_dir)):
        if not name.lower().endswith('.json'):
            continue
        path = os.path.join(golden_dir, name)
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                golden = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(golden, dict) or not golden.get('goldenFor'):
            continue
        prefixes = ('samples/%s/' % sample, 'samples\\%s\\' % sample)
        expected = golden['goldenFor'] if isinstance(golden['goldenFor'], list) \
            else [golden['goldenFor']]
        if any(str(item).startswith(prefixes) for item in expected):
            goldens.append(golden)
    return goldens


# ---------------------------------------------------------------------------
# campaign 预算与暖缓存（campaign.sqlite3：累计计数 + 模型响应 KV，跨续跑/重复共用）
# ---------------------------------------------------------------------------

def _campaign_counters(campaign_state):
    data = campaign_state.cache_get(CAMPAIGN_COUNTERS_KEY)
    data = data if isinstance(data, dict) else {}
    def _int(value):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    return {'attempts': _int(data.get('attempts')),
            'knownCompletionTokens': _int(data.get('knownCompletionTokens')),
            'activeMs': _int(data.get('activeMs'))}


def _remaining_budget(config, counters):
    """campaign 余量 → run 级 budget dict（直接传共享执行器；0/负余量立即受阻）。"""
    caps = config['campaignBudget']
    return {
        'maxAttempts': max(0, int(caps['maxAttempts']) - counters['attempts']),
        'maxWallMs': max(0, int(caps['maxWallMs']) - counters['activeMs']),
        'completionSoftLimit': max(0, int(caps['maxCompletionTokens'])
                                   - counters['knownCompletionTokens']),
    }


def _campaign_cache_key(codec, messages, max_tokens):
    payload = json.dumps({'codec': codec, 'messages': messages, 'maxTokens': max_tokens},
                         ensure_ascii=False, sort_keys=True, default=str)
    return 'modelresult:v1:' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


def _make_call_fn(model, codec, campaign_state, stats, counters):
    """模型调用包装：campaign 暖缓存命中直接回放（不计物理请求）；未命中调模型并缓存。

    物理请求计数**逐次即时落库**（先计数后返回），崩溃窗口最多多计一次——预算口径
    宁可保守。ok=True 的响应（含 length 截断，内容确定）进缓存；失败响应不缓存。
    模型异常（fake 'crash' 等）统一转 HTTP_UNKNOWN 不可重试 CallResult，不上抛执行器。
    """
    def call(messages, max_tokens):
        key = _campaign_cache_key(codec, messages, max_tokens)
        cached = campaign_state.cache_get(key)
        if cached is not None and isinstance(cached, dict) and 'ok' in cached:
            stats['cacheHits'] += 1
            return cached
        stats['physicalCalls'] += 1
        counters['attempts'] += 1
        campaign_state.cache_put(CAMPAIGN_COUNTERS_KEY, counters)
        try:
            result = model(messages, max_tokens)
        except Exception as exc:   # 模型崩溃不进执行器：转冻结 CallResult 形状
            result = {'ok': False, 'content': None, 'finishReason': None,
                      'errorCode': 'HTTP_UNKNOWN', 'retryable': False,
                      'usage': contracts.empty_usage(), 'requestBytes': 0,
                      'responseBytes': 0, 'durationMs': 0}
            result['crashNote'] = str(exc)[:200]
        if isinstance(result, dict) and result.get('ok'):
            campaign_state.cache_put(key, result)
        return result
    return call


# ---------------------------------------------------------------------------
# 计划输入（C/D：plan_initial 初始装箱；A/B：注入固定批 planner——全走共享核心）
# ---------------------------------------------------------------------------

def _fingerprint_parts(config, sample, codec, targets):
    scope_digest = contracts.plan_fingerprint([json.dumps(
        config.get('scope') or {}, ensure_ascii=False, sort_keys=True, default=str)])
    target_digest = contracts.plan_fingerprint(
        sorted(contracts.target_digest(target) for target in targets))
    return ['ontology-token-pilot', config['campaignId'], sample, codec,
            target_digest, scope_digest]


def _fresh_plan_doc(config, profile, codec, arm, facts_by_id, sample):
    """新建计划 doc：create_plan（schemaVersion=2）；C/D 先落 plan_initial 初始作业。

    A/B 不建初始作业（差异点 2）：全部目标 pending，首步由注入 planner 分批。
    返回（doc, adapter_diffs）。
    """
    facts = [dict(fact) for fact in (facts_by_id or {}).values()]
    targets = semantic_units.build_targets(facts)['targets']
    doc = batch_state.create_plan(
        'pilot-%s-%s' % (config['campaignId'], sample), 1, 1, targets, profile, codec,
        _fingerprint_parts(config, sample, codec, targets), plan_epoch=1)
    if ARM_PLANNER[arm] != 'v3-pack-next':
        return doc, list(FIXED_BATCH_ADAPTER_DIFFS)
    initial = batch_plan.plan_initial(
        targets, facts_by_id, profile, {'scopePayload': config.get('scope') or {}})
    if initial.get('ok') and isinstance(initial.get('initialJob'), dict):
        doc = batch_state.apply_event(
            doc, {'type': batch_state.EVENT_JOB_CREATED, **initial['initialJob']})
    return doc, []


def _fixed_batch_planner():
    """A/B 对照 planner（batch_execution._plan_more 的注入点）：每 job 固定 20 事实。

    只做「pending 目标 → 固定大小 job 定义」这一件事；估算字段按最小占位（不参与
    装箱决策）；调度/认领/持久化仍由共享执行器完成。签名 = planner(context) →
    {'jobs': [job_created 事件体]}。
    """
    def planner(context):
        doc = context['doc']
        targets = doc.get('targets') or {}
        # 计划 targets 表是有界形状（无 targetId 键，id 即键）——按执行器同口径补回
        pending = batch_plan.sorted_targets(
            [dict(targets[tid], targetId=tid)
             for tid in (doc.get('pendingTargetIds') or []) if targets.get(tid)])
        epoch = int(doc.get('planEpoch') or 1)
        pool = [dict(target, targetId=tid)
                for tid, target in (doc.get('targets') or {}).items()]
        jobs = []
        for start in range(0, len(pending), protocol.LLM_BATCH_FACTS):
            chunk = pending[start:start + protocol.LLM_BATCH_FACTS]
            ctx_ids = batch_plan.context_fact_ids_for(chunk, pool)
            estimate = {'slots': budget.unit_slots(chunk), 'inputTokens': 0,
                        'expectedOutput': 0, 'visibleOutput': None,
                        'softTargetExceeded': False}
            job_id = contracts.stable_job_id(epoch, '', 'root', chunk)
            jobs.append(batch_plan.job_definition(job_id, chunk, ctx_ids, estimate,
                                                  parent_id=None, root_id=None,
                                                  split_path='root'))
        return {'jobs': jobs, 'softTargetExceeded': [], 'infeasible': [],
                'reason': 'A/B 对照臂固定 %d 事实/批（不经 pack_next 装箱）'
                          % protocol.LLM_BATCH_FACTS}
    return planner


# ---------------------------------------------------------------------------
# 单次运行（sample × arm × repeat）
# ---------------------------------------------------------------------------

def _ledger_from_doc(doc):
    """计划 doc 的 attempts（落库序）→ UsageLedger（全量入账、不去重不丢弃）。"""
    ledger = evaluate.UsageLedger()
    for attempt_id, attempt in (doc.get('attempts') or {}).items():
        ledger.add_attempt(
            attempt_id, job_id=attempt.get('jobId'), usage=attempt.get('usage'),
            finish_reason=attempt.get('finishReason'), error_code=attempt.get('errorCode'),
            error_message=attempt.get('errorMessage'), duration_ms=attempt.get('durationMs'),
            prompt_bytes=attempt.get('bytes') or 0, state=attempt.get('state'))
    return ledger


def ledger_from_result(result):
    """结果 dict 的 ledgerAttempts → UsageLedger（汇总报告用，与落库口径一致）。"""
    ledger = evaluate.UsageLedger()
    for item in (result.get('ledgerAttempts') or []):
        ledger.add_attempt(item.get('attemptId'), job_id=item.get('jobId'),
                           usage=item.get('usage'), finish_reason=item.get('finishReason'),
                           error_code=item.get('errorCode'),
                           error_message=item.get('errorMessage'),
                           duration_ms=item.get('durationMs'),
                           prompt_bytes=item.get('promptBytes') or 0,
                           state=item.get('state'))
    return ledger


_EVENT_KIND_STATS = {
    'job_succeeded': 'jobSucceeded',
    'job_split': 'jobSplit',
    'format_repair': 'formatRepair',
    'network_retry': 'networkRetry',
    'job_failed': 'jobFailed',
    'blocked': 'blocked',
}


def _event_stats(events):
    stats = {'plannedJobs': 0, 'jobSucceeded': 0, 'jobSplit': 0, 'formatRepair': 0,
             'networkRetry': 0, 'jobFailed': 0, 'blocked': 0}
    for event in events or []:
        kind = str(event.get('kind') or '')
        if kind == 'planned':
            stats['plannedJobs'] += int(event.get('jobs') or 0)
            continue
        key = _EVENT_KIND_STATS.get(kind)
        if key:
            stats[key] += 1
    return stats


def _result_shell(config, sample, arm, repeat, codec, doc, run_state, blocking, mode):
    """终态短路径（已有 blocking / 跳过）的最小结果壳：口径与 run_arm 主路径一致。"""
    ledger = _ledger_from_doc(doc)
    return {
        'schemaVersion': RESULT_SCHEMA_VERSION,
        'resultKind': RESULT_KIND,
        'campaignId': config['campaignId'],
        'sample': sample, 'arm': arm, 'repeat': int(repeat),
        'mode': mode,
        'runState': run_state,
        'blocking': blocking,
        'planId': str(doc.get('planId') or ''),
        'usage': ledger.aggregate(),
        'quality': [],
        'qualitySummary': evaluate.summarize_eval([]),
        'events': _event_stats([]),
        'ledgerAttempts': [dict(attemptId=aid, jobId=at.get('jobId'), usage=at.get('usage'),
                                finishReason=at.get('finishReason'),
                                errorCode=at.get('errorCode'),
                                errorMessage=at.get('errorMessage'),
                                durationMs=at.get('durationMs'),
                                promptBytes=at.get('bytes') or 0, state=at.get('state'))
                           for aid, at in (doc.get('attempts') or {}).items()],
        'meta': {'codec': codec, 'planner': ARM_PLANNER[arm],
                 'adapterDiffs': list(FIXED_BATCH_ADAPTER_DIFFS)
                 if ARM_PLANNER[arm] == 'fixed-batch' else [],
                 'schemaVersion': int(doc.get('schemaVersion') or 0)},
    }


def run_arm(config, sample, arm, repeat, model, output_dir):
    """跑一次 (sample, arm, repeat)：建/续计划 → 共享核心执行 → 评价 → 落盘结果。

    返回脱敏后的单次结果 dict（含 usage/质量/事件统计/meta）。持久化异常原样上抛
    （执行器纪律），由 CLI 捕获记为受阻；结果文件已存在的终态 run 直接跳过（续跑
    语义：成功/失败/受阻不重做，累计不重置）。
    """
    if arm not in ARM_CODEC:
        raise ConfigError('未知臂 %r（允许 %s）' % (arm, '/'.join(ARMS)))
    codec = ARM_CODEC[arm]
    campaign_id = config['campaignId']
    task_key = '%s-%s-r%d' % (sample, arm, repeat)
    states_dir = os.path.join(output_dir, 'states', campaign_id)
    results_dir = os.path.join(output_dir, 'results', campaign_id)
    state_path = os.path.join(states_dir, '%s.sqlite3' % task_key)
    result_path = os.path.join(results_dir, '%s.json' % task_key)

    existing = _load_result(result_path)
    if existing is not None and existing.get('runState') in _TERMINAL_RUN_STATES:
        existing.setdefault('meta', {})['skippedExisting'] = True
        return existing

    facts, facts_by_id = load_sample_facts(config, sample)
    model_name = str(getattr(model, 'model_name', '') or config.get('model') or 'unknown-model')
    profile = build_budget_profile(config, model_name)
    targets = semantic_units.build_targets([dict(f) for f in facts])['targets']

    campaign = exp_state.open_state(os.path.join(states_dir, 'campaign.sqlite3'))
    stats = {'physicalCalls': 0, 'cacheHits': 0}
    try:
        counters = _campaign_counters(campaign)
        remaining = _remaining_budget(config, counters)
        call_fn = _make_call_fn(model, codec, campaign, stats, counters)

        state = exp_state.open_state(state_path)
        try:
            doc = state.load(task_key)
            resumed = doc is not None
            if doc is None:
                doc, adapter_diffs = _fresh_plan_doc(config, profile, codec, arm,
                                                     facts_by_id, sample)
                state.save_plan(task_key, doc)
                run_attempt = 1
                requeue_failed = False
            else:
                adapter_diffs = list(FIXED_BATCH_ADAPTER_DIFFS) \
                    if ARM_PLANNER[arm] == 'fixed-batch' else []
                started = [aid for aid, attempt in (doc.get('attempts') or {}).items()
                           if str(attempt.get('state') or '') == contracts.ATTEMPT_STARTED]
                if started:
                    state.mark_interrupted_unknown(task_key, started)
                run_marks = [int(attempt.get('runAttempt') or 0)
                             for attempt in (doc.get('attempts') or {}).values()]
                run_attempt = (max(run_marks) + 1) if run_marks else 1
                requeue_failed = True
                if doc.get('blocking'):
                    result = _result_shell(config, sample, arm, repeat, codec, doc,
                                           'blocked', doc.get('blocking'),
                                           'fake' if getattr(model, 'fake', False) else 'real')
                    result['meta'].update({'physicalCalls': 0, 'cacheHits': 0,
                                           'resumed': True, 'skippedBlocking': True,
                                           'campaignBudgetRemainingAtStart': remaining})
                    _write_result(result_path, result)
                    return result

            planner = _fixed_batch_planner() if ARM_PLANNER[arm] == 'fixed-batch' else None
            context = executor.make_context(
                state, call_fn, profile, doc, codec_version=codec,
                facts_by_id=facts_by_id, scope_payload=config.get('scope') or {},
                planner=planner,
                sleeper=(lambda _seconds: None) if getattr(model, 'fake', False) else None,
                run_attempt=run_attempt, budget=dict(remaining), task_key=task_key,
                resume_requeue_failed=requeue_failed)
            summary = executor.run_plan(context)
            final_doc = summary['doc']
            # campaign 累计结算：物理请求已逐次即时入账；已知 completion/活跃耗时按
            # 本次 runAttempt 的收口尝试补记（跨 resume 不双计；崩溃在途尝试 usage
            # 未知，本就不进已知口径）。
            counters['knownCompletionTokens'] += _run_attempt_known(final_doc, run_attempt)
            counters['activeMs'] += _run_attempt_active_ms(final_doc, run_attempt)
            campaign.cache_put(CAMPAIGN_COUNTERS_KEY, counters)

            candidates = []
            for page in context['persistence'].iterate_candidates():
                candidates.extend(page)
            evals = [evaluate.evaluate_candidates(candidates, golden)
                     for golden in goldens_for_sample(config, sample)]

            result = {
                'schemaVersion': RESULT_SCHEMA_VERSION,
                'resultKind': RESULT_KIND,
                'campaignId': campaign_id,
                'sample': sample, 'arm': arm, 'repeat': int(repeat),
                'mode': 'fake' if getattr(model, 'fake', False) else 'real',
                'runState': str(summary['state']),
                'blocking': summary['blocking'],
                'planId': summary['planId'],
                'usage': _ledger_from_doc(final_doc).aggregate(),
                'quality': evals,
                'qualitySummary': evaluate.summarize_eval(evals),
                'events': _event_stats(summary['events']),
                'ledgerAttempts': [dict(
                    attemptId=aid, jobId=attempt.get('jobId'), usage=attempt.get('usage'),
                    finishReason=attempt.get('finishReason'),
                    errorCode=attempt.get('errorCode'),
                    errorMessage=attempt.get('errorMessage'),
                    durationMs=attempt.get('durationMs'),
                    promptBytes=attempt.get('bytes') or 0,
                    state=attempt.get('state'))
                    for aid, attempt in (final_doc.get('attempts') or {}).items()],
                'meta': {
                    'codec': codec,
                    'planner': ARM_PLANNER[arm],
                    'adapterDiffs': adapter_diffs,
                    'schemaVersion': int(final_doc.get('schemaVersion') or 0),
                    'model': model_name,
                    'targetCount': len(targets),
                    'jobCount': len(final_doc.get('jobs') or {}),
                    'goldenCount': len(evals),
                    'physicalCalls': stats['physicalCalls'],
                    'cacheHits': stats['cacheHits'],
                    'resumed': resumed,
                    'runAttempt': run_attempt,
                    'campaignBudgetRemainingAtStart': remaining,
                    'campaignCountersAfter': dict(counters),
                },
            }
            _write_result(result_path, result)
            return result
        finally:
            state.close()
    finally:
        campaign.close()


def _run_attempt_known(doc, run_attempt):
    """本次 runAttempt 内已收口尝试的已知 completion（跨 resume 不双计）。"""
    total = 0
    for attempt in (doc.get('attempts') or {}).values():
        if int(attempt.get('runAttempt') or 0) != int(run_attempt):
            continue
        if str(attempt.get('state') or '') == contracts.ATTEMPT_STARTED:
            continue
        usage = contracts.normalize_usage(attempt.get('usage'))
        if usage['usageSource'] == 'api' and usage['completionTokens'] is not None:
            total += int(usage['completionTokens'])
    return total


def _run_attempt_active_ms(doc, run_attempt):
    total = 0
    for attempt in (doc.get('attempts') or {}).values():
        if int(attempt.get('runAttempt') or 0) == int(run_attempt) \
                and attempt.get('durationMs'):
            total += int(attempt['durationMs'])
    return total


def _load_result(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_result(path, result):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cleaned = evaluate.redact_report(result)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(cleaned, handle, ensure_ascii=False, indent=2)
    return cleaned
