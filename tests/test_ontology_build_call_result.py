#!/usr/bin/env python3
"""D05 单HTTP请求安全结果与usage 回归（llm_client.chat_once_result，2026-09-22）。

契约来源：workbench/ontology_build/batch_contracts.py（提交 87d1473）「单次调用结果 CallResult」
节 + 整合计划 v3 §5 事件表/§8 D05 行。验证点：

1.  正常 stop 响应带 usage → ok=True、finishReason='stop'、usage 各值正确、usageSource='api'；
    CallResult/usage 形状逐字段冻结（不多不少）。
2.  finish_reason='length' 带部分 content 与 usage → ok=True、截断不丢用量、content 为部分正文。
3.  响应无 usage → usageSource='absent'、四个 token 字段全 None。
4.  reasoning_tokens + completion_tokens → completionTokens 逐字保留、reasoningTokens 单列、
    聚合不双加（knownCompletionTokens == completionTokens）。
5.  HTTPError 429→RATE_LIMITED/可重试；502/503/504→PROVIDER_ERROR/可重试；
    400→HTTP_UNKNOWN/不可重试（未知错误不猜类别）。
6.  超时与连接失败 → 可重试：socket.timeout/TimeoutError/URLError(timeout reason)→TIMEOUT；
    URLError(连接拒绝)→NETWORK_RETRYABLE。
7.  响应体 >1MB 上限 → RESPONSE_TOO_LARGE、不可重试、responseBytes 记录实际读取量（上限+1）。
8.  非 JSON / 非 dict / 缺 choices / content 非 str → RESPONSE_MALFORMED。
9.  provider 缺 endpoint/model → CONFIG_INVALID、ok=False、requestBytes=0。
10. 旧 chat() 回归：正常响应返回 (content, trace)；length 仍抛 LlmError；probe 2xx 不抛；
    trace 含 provider/model/durationMs（旧语义不变）。
11. 安全边界：CallResult 不含提示词原文片段、'Authorization' 字样与密钥值；
    JSON 网络错误结果同样不含。

隔离（AGENTS.md 测试隔离铁律）：unittest.mock 替换 workbench.llm_client.urlopen，
全部场景不访问网络、不写文件、不起服务、不依赖真实 provider。

运行：python3 tests/run.py --test tests/test_ontology_build_call_result.py
"""
import contextlib
import json
import socket
import sys
import traceback
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import llm_client  # noqa: E402
from workbench.ontology_build import batch_contracts  # noqa: E402

PROVIDER = {'endpoint': 'http://mock.local/v1/chat/completions', 'model': 'mock-model',
            'name': '测试提供方', 'api_key': 'sk-TEST-SECRET-KEY-456'}
PROMPT_SECRET = 'TOPSECRET-PROMPT-BODY-9137'
CALL_RESULT_KEYS = {'ok', 'content', 'finishReason', 'errorCode', 'retryable',
                    'usage', 'requestBytes', 'responseBytes', 'durationMs'}
USAGE_KEYS = {'promptTokens', 'completionTokens', 'reasoningTokens', 'totalTokens',
              'usageSource'}

PASSED = []
FAILED = []


def check(name, condition, detail=''):
    if condition:
        PASSED.append(name)
        print('  PASS %s' % name)
    else:
        FAILED.append(name)
        print('  FAIL %s  %s' % (name, detail))


@contextlib.contextmanager
def fake_urlopen(value):
    """替换 llm_client.urlopen：value 为异常实例则抛出，否则作为响应对象返回。"""
    state = {'request': None, 'timeout': None}

    def _fake(request, timeout=None):
        state['request'] = request
        state['timeout'] = timeout
        if isinstance(value, Exception):
            raise value
        return value

    with patch('workbench.llm_client.urlopen', _fake):
        yield state


class FakeResponse:
    """urlopen 返回替身：with 上下文 + read(limit)（与真实 HTTPResponse 同形）。"""

    def __init__(self, payload):
        self._data = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')

    def read(self, limit=-1):
        if limit is None or limit < 0:
            return self._data
        return self._data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def chat_payload(content='回答正文', finish_reason='stop', usage=None):
    data = {'id': 'cmpl-mock', 'object': 'chat.completion',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content},
                         'finish_reason': finish_reason}]}
    if usage is not None:
        data['usage'] = usage
    return data


def run(fn):
    try:
        fn()
    except Exception:
        FAILED.append(fn.__name__ + '（异常）')
        print('  FAIL %s（未预期异常）' % fn.__name__)
        traceback.print_exc()


# --- 1. 正常 stop 响应 ---------------------------------------------------------------

def scenario_normal_stop():
    expected_response = json.dumps(chat_payload(
        content='你好，这是结果', finish_reason='stop',
        usage={'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18})).encode('utf-8')
    with fake_urlopen(FakeResponse(expected_response)) as state:
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': '问题Q'}])
    check('S1 ok=True', result['ok'] is True)
    check('S1 finishReason=stop', result['finishReason'] == 'stop')
    check('S1 content 原文', result['content'] == '你好，这是结果')
    check('S1 errorCode=None', result['errorCode'] is None)
    check('S1 retryable=False', result['retryable'] is False)
    check('S1 usageSource=api', result['usage']['usageSource'] == 'api')
    check('S1 promptTokens=11', result['usage']['promptTokens'] == 11)
    check('S1 completionTokens=7', result['usage']['completionTokens'] == 7)
    check('S1 totalTokens=18', result['usage']['totalTokens'] == 18)
    check('S1 reasoningTokens=None', result['usage']['reasoningTokens'] is None)
    check('S1 CallResult 形状冻结', set(result.keys()) == CALL_RESULT_KEYS,
          str(sorted(result.keys())))
    check('S1 usage 形状冻结', set(result['usage'].keys()) == USAGE_KEYS,
          str(sorted(result['usage'].keys())))
    check('S1 durationMs 非负整数', isinstance(result['durationMs'], int) and result['durationMs'] >= 0)
    sent = json.loads(state['request'].data.decode('utf-8'))
    check('S1 请求体 model/max_tokens/messages', sent['model'] == 'mock-model'
          and sent['max_tokens'] == 4000 and sent['messages'][0]['content'] == '问题Q')
    check('S1 requestBytes=实际序列化字节数', result['requestBytes'] == len(state['request'].data))
    check('S1 responseBytes=实际读取字节数', result['responseBytes'] == len(expected_response))


# --- 2. length 截断：ok=True 且不丢用量 ----------------------------------------------

def scenario_length_truncated():
    usage_raw = {'prompt_tokens': 100, 'completion_tokens': 2000, 'total_tokens': 2100}
    resp = FakeResponse(chat_payload(content='PARTIAL-CONTENT-ABC', finish_reason='length',
                                     usage=usage_raw))
    with fake_urlopen(resp):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': PROMPT_SECRET}],
                                             max_tokens=16)
    check('S2 length ok=True', result['ok'] is True)
    check('S2 finishReason=length', result['finishReason'] == 'length')
    check('S2 content=已返回部分', result['content'] == 'PARTIAL-CONTENT-ABC')
    check('S2 截断保留 usage（completion=2000）', result['usage']['completionTokens'] == 2000)
    check('S2 截断保留 usage（total=2100）', result['usage']['totalTokens'] == 2100)
    check('S2 usageSource=api', result['usage']['usageSource'] == 'api')


# --- 3. 响应无 usage -----------------------------------------------------------------

def scenario_usage_absent():
    resp = FakeResponse(chat_payload(content='有正文但没用量', finish_reason='stop', usage=None))
    with fake_urlopen(resp):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S3 usageSource=absent', result['usage']['usageSource'] == 'absent')
    for field in ('promptTokens', 'completionTokens', 'reasoningTokens', 'totalTokens'):
        check('S3 %s=None' % field, result['usage'][field] is None)
    check('S3 不影响 ok', result['ok'] is True)


# --- 4. reasoning 不双加 --------------------------------------------------------------

def scenario_reasoning_no_double_count():
    usage_raw = {'prompt_tokens': 10, 'completion_tokens': 50, 'reasoning_tokens': 30,
                 'total_tokens': 60}
    resp = FakeResponse(chat_payload(content='推理作答', finish_reason='stop', usage=usage_raw))
    with fake_urlopen(resp):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S4 completionTokens 逐字=50（绝不 50+30）', result['usage']['completionTokens'] == 50)
    check('S4 reasoningTokens 单列=30', result['usage']['reasoningTokens'] == 30)
    # 聚合入口按冻结契约接 provider 原始 usage（usage_aggregate/add_usage 内部 normalize）：
    # 验证 normalize 后聚合时 completion 只算一次、reasoning 绝不并入 completion。
    aggregate = batch_contracts.usage_aggregate([usage_raw])
    check('S4 聚合 completion 只算一次', aggregate['completionTokens'] == 50
          and aggregate['knownCompletionTokens'] == 50, str(aggregate))
    merged = batch_contracts.add_usage(usage_raw, usage_raw)
    check('S4 add_usage 两份也只各算一次', merged['completionTokens'] == 100
          and merged['reasoningTokens'] == 60, str(merged))


# --- 5. HTTP 错误分类 ----------------------------------------------------------------

def scenario_http_errors():
    cases = [(429, 'RATE_LIMITED', True), (502, 'PROVIDER_ERROR', True),
             (503, 'PROVIDER_ERROR', True), (504, 'PROVIDER_ERROR', True),
             (400, 'HTTP_UNKNOWN', False), (401, 'HTTP_UNKNOWN', False),
             (500, 'HTTP_UNKNOWN', False)]
    for code, expected_code, expected_retryable in cases:
        with fake_urlopen(HTTPError(PROVIDER['endpoint'], code, 'err', {}, None)):
            result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
        check('S5 HTTP %d→%s' % (code, expected_code),
              result['ok'] is False and result['errorCode'] == expected_code
              and result['retryable'] is expected_retryable,
              str(result))
        check('S5 HTTP %d 用量记 absent' % code, result['usage']['usageSource'] == 'absent')
        check('S5 HTTP %d 无正文/finishReason' % code,
              result['content'] is None and result['finishReason'] is None)


# --- 6. 超时与连接失败 ---------------------------------------------------------------

def scenario_network_and_timeout():
    with fake_urlopen(socket.timeout('timed out')):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S6 socket.timeout→TIMEOUT/可重试',
          result['ok'] is False and result['errorCode'] == 'TIMEOUT' and result['retryable'] is True,
          str(result))
    with fake_urlopen(TimeoutError()):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S6 TimeoutError→TIMEOUT/可重试',
          result['errorCode'] == 'TIMEOUT' and result['retryable'] is True, str(result))
    with fake_urlopen(URLError(socket.timeout('timed out'))):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S6 URLError(超时原因)→TIMEOUT/可重试',
          result['errorCode'] == 'TIMEOUT' and result['retryable'] is True, str(result))
    with fake_urlopen(URLError(ConnectionRefusedError(111, 'Connection refused'))):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S6 连接拒绝→NETWORK_RETRYABLE/可重试',
          result['ok'] is False and result['errorCode'] == 'NETWORK_RETRYABLE'
          and result['retryable'] is True, str(result))
    with fake_urlopen(OSError('network unreachable')):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S6 OSError→NETWORK_RETRYABLE/可重试',
          result['errorCode'] == 'NETWORK_RETRYABLE' and result['retryable'] is True, str(result))


# --- 7. 响应超上限 -------------------------------------------------------------------

def scenario_response_too_large():
    oversized = b'{"choices": ' + b' ' * 1_100_000 + b'}'
    with fake_urlopen(FakeResponse(oversized)):
        result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
    check('S7 RESPONSE_TOO_LARGE', result['ok'] is False
          and result['errorCode'] == 'RESPONSE_TOO_LARGE', str(result))
    check('S7 不可重试', result['retryable'] is False)
    check('S7 responseBytes=实际读取（上限+1）',
          result['responseBytes'] == llm_client._MAX_RESPONSE + 1, str(result['responseBytes']))
    check('S7 不解析半截正文（usage absent）', result['usage']['usageSource'] == 'absent')


# --- 8. 响应格式不对 ------------------------------------------------------------------

def scenario_response_malformed():
    for label, payload in [('非 JSON 文本', b'this is not json at all'),
                           ('JSON 数组', b'[1, 2, 3]'),
                           ('缺 choices', json.dumps({'id': 'x'}).encode('utf-8')),
                           ('choices 空', json.dumps({'choices': []}).encode('utf-8')),
                           ('content 非 str',
                            json.dumps({'choices': [{'message': {'content': 123},
                                                     'finish_reason': 'stop'}]}).encode('utf-8'))]:
        with fake_urlopen(FakeResponse(payload)):
            result = llm_client.chat_once_result(PROVIDER, [{'role': 'user', 'content': 'q'}])
        check('S8 %s→RESPONSE_MALFORMED/不可重试' % label,
              result['ok'] is False and result['errorCode'] == 'RESPONSE_MALFORMED'
              and result['retryable'] is False, str(result))


# --- 9. 配置缺失 ---------------------------------------------------------------------

def scenario_config_invalid():
    with fake_urlopen(FakeResponse(chat_payload())):
        result = llm_client.chat_once_result({'model': 'm'}, [{'role': 'user', 'content': 'q'}])
    check('S9 缺 endpoint→CONFIG_INVALID',
          result['ok'] is False and result['errorCode'] == 'CONFIG_INVALID'
          and result['retryable'] is False, str(result))
    check('S9 未发请求（requestBytes=0）', result['requestBytes'] == 0
          and result['responseBytes'] == 0)
    with fake_urlopen(FakeResponse(chat_payload())):
        result = llm_client.chat_once_result({'endpoint': 'http://x'}, [{'role': 'user', 'content': 'q'}])
    check('S9 缺 model→CONFIG_INVALID', result['errorCode'] == 'CONFIG_INVALID', str(result))
    with fake_urlopen(FakeResponse(chat_payload())):
        result = llm_client.chat_once_result(None, [{'role': 'user', 'content': 'q'}])
    check('S9 provider=None 也不抛→CONFIG_INVALID', result['errorCode'] == 'CONFIG_INVALID',
          str(result))


# --- 10. 旧 chat() 行为回归 -----------------------------------------------------------

def scenario_legacy_chat_regression():
    resp = FakeResponse(chat_payload(content='legacy-content', finish_reason='stop'))
    with fake_urlopen(resp):
        pair = llm_client.chat(PROVIDER, [{'role': 'user', 'content': 'q'}], timeout=5)
    check('S10 chat 返回 (content, trace) 二元组', isinstance(pair, tuple) and len(pair) == 2)
    content, trace = pair
    check('S10 content 正确', content == 'legacy-content')
    check('S10 trace 含 provider/model/durationMs',
          trace.get('provider') == '测试提供方' and trace.get('model') == 'mock-model'
          and isinstance(trace.get('durationMs'), int), str(trace))

    resp = FakeResponse(chat_payload(content='截断正文', finish_reason='length'))
    raised = None
    with fake_urlopen(resp):
        try:
            llm_client.chat(PROVIDER, [{'role': 'user', 'content': 'q'}], timeout=5)
        except llm_client.LlmError as exc:
            raised = exc
    check('S10 length 仍抛 LlmError（旧语义不变）',
          raised is not None and '截断' in str(raised), str(raised))

    resp = FakeResponse(chat_payload(content='截断正文', finish_reason='length'))
    with fake_urlopen(resp):
        probe_content, _trace = llm_client.chat(PROVIDER, [{'role': 'user', 'content': 'q'}],
                                                max_tokens=1, timeout=5, probe=True)
    check('S10 probe 2xx 即连通（length 不抛，返回空 content）', probe_content == '')

    with fake_urlopen(URLError('no route')):
        try:
            llm_client.chat(PROVIDER, [{'role': 'user', 'content': 'q'}], timeout=5)
            raised = None
        except llm_client.LlmError as exc:
            raised = exc
    check('S10 网络失败仍抛 LlmError 中文文案',
          raised is not None and '无法连接' in str(raised), str(raised))


# --- 11. 安全边界 ---------------------------------------------------------------------

def scenario_no_leak():
    messages = [{'role': 'system', 'content': PROMPT_SECRET},
                {'role': 'user', 'content': '再提一次 ' + PROMPT_SECRET}]
    with fake_urlopen(FakeResponse(chat_payload(content='正常回答', finish_reason='stop'))):
        ok_result = llm_client.chat_once_result(PROVIDER, messages)
    dump = json.dumps(ok_result, ensure_ascii=False)
    check('S11 成功结果不含提示词原文片段', PROMPT_SECRET not in dump)
    check('S11 成功结果不含 Authorization 字样', 'Authorization' not in dump)
    check('S11 成功结果不含密钥值', PROVIDER['api_key'] not in dump)
    check('S11 成功结果无 request/response/trace 键',
          not ({'request', 'response', 'trace'} & set(ok_result.keys())))
    with fake_urlopen(URLError('boom')):
        err_result = llm_client.chat_once_result(PROVIDER, messages)
    dump = json.dumps(err_result, ensure_ascii=False)
    check('S11 网络错误结果不含提示词原文/密钥',
          PROMPT_SECRET not in dump and PROVIDER['api_key'] not in dump
          and 'Authorization' not in dump)


def main():
    for scenario in (scenario_normal_stop, scenario_length_truncated, scenario_usage_absent,
                     scenario_reasoning_no_double_count, scenario_http_errors,
                     scenario_network_and_timeout, scenario_response_too_large,
                     scenario_response_malformed, scenario_config_invalid,
                     scenario_legacy_chat_regression, scenario_no_leak):
        print('--- %s' % scenario.__name__)
        run(scenario)
    print('\n========== 汇总 ==========')
    print('PASSED: %d  FAILED: %d' % (len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print('  失败: %s' % name)
        return 1
    print('ALL PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
