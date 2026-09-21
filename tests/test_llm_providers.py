"""LLM 提供方配置与 llm_client 回归（存储边界 / 密钥不回传 / 默认唯一 / JSON 求值重试）。

纯 python3 标准库直跑；WIZ_WORKBENCH_ROOT 挂临时数据根，urlopen 用桩替换，
绝不发起真实网络请求。运行：python3 tests/test_llm_providers.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix='wiz_llm_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import llm_client, llm_providers, flow_routes  # noqa: E402  （临时根就位后再 import）

# 账号体系（20260918）：提供方配置与密钥按账号隔离，域级测试绑定测试账号
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent))
import auth_client as _auth_client
_auth_client.bind_fixture_user()

PASSED = []


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print(f'通过) {message}')
        return
    print(f'[失败] {message}')
    if actual is not None:
        print('  实际:', json.dumps(actual, ensure_ascii=False, default=str)[:1500])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)


class _FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()
        self.status = 200

    def read(self, limit=-1):
        return self._raw[:limit] if isinstance(limit, int) and limit > 0 else self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _stub_urlopen(payload):
    def handler(request, timeout=None):
        return _FakeResponse(payload)
    return handler


# 1) 存储：保存 / 元数据无密钥 / 编辑沿用密钥 ------------------------------------
first = llm_providers.save(name='DeepSeek 主力', endpoint='https://api.deepseek.com/chat/completions',
                           model='deepseek-chat', api_key='sk-secret-1', timeout=60, temperature=0)
check(first['keyConfigured'] and first['isDefault'], '首次保存成为默认')
meta = llm_providers.list_metadata()
check(all('api_key' not in item and 'apiKey' not in item for item in meta), '元数据绝不含密钥', meta)
check(meta[0]['endpoint'] == 'https://api.deepseek.com/chat/completions' and meta[0]['timeout'] == 60
      and meta[0]['temperature'] == 0, '元数据回显 endpoint/timeout/temperature（编辑用）', meta)
stored = llm_providers.read(first['id'])
check(stored.get('api_key') == 'sk-secret-1', 'read() 仅供服务端取密钥')

second = llm_providers.save(name='通义备用', endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                            model='qwen-plus', api_key='sk-secret-2', is_default=False)
check(llm_providers.default_provider()['id'] == first['id'], '非默认保存不改变默认')
llm_providers.save(name='通义备用', endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                   model='qwen-plus', is_default=True, provider_id=second['id'])
check(llm_providers.default_provider()['id'] == second['id'], '保存默认切换 is_default')
meta = llm_providers.list_metadata()
check(sum(1 for m in meta if m['isDefault']) == 1, '默认提供方唯一', meta)
edited = llm_providers.save(name='通义备用2', endpoint='https://dashscope.aliyuncs.com/x', model='qwen-plus',
                            api_key='', timeout=90, temperature=0.2, is_default=True, provider_id=second['id'])
meta = {m['id']: m for m in llm_providers.list_metadata()}
check(meta[second['id']]['timeout'] == 90 and meta[second['id']]['temperature'] == 0.2,
      '超时与温度编辑后回显正确', meta[second['id']])
check(llm_providers.read(second['id'])['api_key'] == 'sk-secret-2', '编辑留空沿用已存密钥')
check(edited['keyConfigured'], '沿用后 keyConfigured 为真')
llm_providers.clear(second['id'])
check(llm_providers.default_provider()['id'] == first['id'], '删除默认后自动回退剩余第一个')

# 2) 校验错误 ----------------------------------------------------------------
for kwargs, needle in [
    (dict(name='', endpoint='https://x/y', model='m'), '名称'),
    (dict(name='a', endpoint='ftp://x', model='m'), 'http'),
    (dict(name='a', endpoint='https://x', model=''), '模型'),
    (dict(name='a', endpoint='https://x', model='m', timeout=9999), '超时'),
    (dict(name='a', endpoint='https://x', model='m'), 'API Key'),
]:
    try:
        llm_providers.save(**kwargs)
        check(False, f'校验应拦截：{needle}')
    except ValueError as exc:
        check(needle in str(exc), f'校验拦截（{needle}）', str(exc))

# 3) llm_client：JSON 提取 / 重试 / 截断 / HTTP 错误 --------------------------------
check(llm_client._extract_json('```json\n{"a": 1}\n```') == {'a': 1}, '代码围栏剥离')
check(llm_client._extract_json('结果如下：{"a": {"b": 2}} 完毕') == {'a': {'b': 2}}, '嵌入 JSON 提取')

calls = []


def _ok_then_payload(request, timeout=None):
    calls.append(1)
    if len(calls) == 1:
        return _FakeResponse({'choices': [{'message': {'content': '这不是 JSON'}, 'finish_reason': 'stop'}]})
    return _FakeResponse({'choices': [{'message': {'content': '{"r": 1}'}, 'finish_reason': 'stop'}]})


real_urlopen = llm_client.urlopen
try:
    llm_client.urlopen = _ok_then_payload
    verdict = llm_client.evaluate_json({'code': 'x'}, {'name': 'p', 'endpoint': 'https://x', 'model': 'm', 'api_key': 'k'})
    check(verdict['ok'] and verdict['result'] == {'r': 1} and len(calls) == 2, '非法 JSON 带反馈重试 1 次后成功', verdict)
    check(verdict['trace']['model'] == 'm' and 'durationMs' in verdict['trace'], 'trace 留痕齐全', verdict['trace'])

    calls.clear()
    llm_client.urlopen = _stub_urlopen({'choices': [{'message': {'content': '仍然不是'}, 'finish_reason': 'stop'}]})
    verdict = llm_client.evaluate_json({'x': 1}, {'endpoint': 'https://x', 'model': 'm'})
    check(not verdict['ok'] and 'JSON' in verdict['error'], '两次失败给出可读错误', verdict)

    llm_client.urlopen = _stub_urlopen({'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}]})
    verdict = llm_client.evaluate_json({'x': 1}, {'endpoint': 'https://x', 'model': 'm'})
    check(not verdict['ok'] and '截断' in verdict['error'], '截断判定', verdict)

    def _http_error(request, timeout=None):
        raise llm_client.HTTPError('https://x', 401, 'Unauthorized', None, None)
    llm_client.urlopen = _http_error
    verdict = llm_client.evaluate_json({'x': 1}, {'endpoint': 'https://x', 'model': 'm', 'api_key': 'bad'})
    check(not verdict['ok'] and 'HTTP 401' in verdict['error'], 'HTTP 错误转可读文案', verdict)
finally:
    llm_client.urlopen = real_urlopen

# 4) 路由：保存/删除/连通性测试（密钥不回传） ---------------------------------------
saved = flow_routes.post_llm_provider_save({'name': '路由测试', 'endpoint': 'https://x/chat', 'model': 'm',
                                            'apiKey': 'sk-route', 'isDefault': False})
check(saved[1] == 200 and 'sk-route' not in json.dumps(saved[0]), 'save 响应无密钥', saved[0])
listed = flow_routes.get_llm_providers({})
check(listed[1] == 200 and not any('sk' in json.dumps(i) for i in listed[0]['items']), '列表无密钥')
try:
    real_urlopen2 = llm_client.urlopen
    llm_client.urlopen = _stub_urlopen({'choices': [{'message': {'content': 'pong'}, 'finish_reason': 'stop'}]})
    tested = flow_routes.post_llm_provider_test({'providerId': saved[0]['provider']['id']})
finally:
    llm_client.urlopen = real_urlopen2
check(tested[1] == 200 and tested[0]['ok'], '连通性测试（探测请求，无密钥外泄）', tested)
cleared = flow_routes.post_llm_provider_delete({'providerId': saved[0]['provider']['id']})
check(cleared[1] == 200 and llm_providers.read(saved[0]['provider']['id']) is None, '删除生效')

# 5) 设为默认（列表页按钮；只切指针，不重写配置） ------------------------------------
a = flow_routes.post_llm_provider_save({'name': '默认甲', 'endpoint': 'https://x/a', 'model': 'ma',
                                        'apiKey': 'sk-a', 'timeout': 60, 'temperature': 0})
b = flow_routes.post_llm_provider_save({'name': '默认乙', 'endpoint': 'https://x/b', 'model': 'mb',
                                        'apiKey': 'sk-b', 'timeout': 90, 'temperature': 0.3})
a_id, b_id = a[0]['provider']['id'], b[0]['provider']['id']
meta = {m['id']: m for m in llm_providers.list_metadata()}
check(meta[a_id]['keyConfigured'] and meta[b_id]['keyConfigured'],
      'keyConfigured 反映密钥已配置（SELECT 含 secret_id）', meta)
before = llm_providers.read(b_id)
switched = flow_routes.post_llm_provider_default({'providerId': b_id})
check(switched[1] == 200 and switched[0]['provider']['isDefault'], '设为默认返回 200', switched[0])
meta = {m['id']: m for m in llm_providers.list_metadata()}
check(meta[b_id]['isDefault'] and not meta[a_id]['isDefault'], '默认项已切换', meta)
check(llm_providers.list_metadata()[0]['id'] == b_id, '默认项排最前')
check(llm_providers.default_provider()['id'] == b_id, 'default_provider 取到新默认')
after = llm_providers.read(b_id)
check(after['timeout'] == 90 and after['temperature'] == 0.3 and after['endpoint'] == 'https://x/b',
      '设为默认不重写配置', after)
check(after['api_key'] == 'sk-b' and after['api_key'] == before['api_key'], '设为默认不动密钥')
check(sum(1 for m in llm_providers.list_metadata() if m['isDefault']) == 1, '默认仍然唯一')
again = flow_routes.post_llm_provider_default({'providerId': b_id})
check(again[1] == 200 and llm_providers.default_provider()['id'] == b_id, '重复设为默认幂等', again[0])
check(flow_routes.post_llm_provider_default({'providerId': 'llm-0000000000'})[1] == 404, '不存在 → 404')
check(flow_routes.post_llm_provider_default({'providerId': 'not-an-id'})[1] == 400, '标识非法 → 400')
check(flow_routes.post_llm_provider_default({})[1] == 400, '缺 providerId → 400')
llm_providers.clear(b_id)
check(llm_providers.default_provider()['id'] in {first['id'], a_id}
      and sum(1 for m in llm_providers.list_metadata() if m['isDefault']) == 1,
      '删除默认后自动回退剩余提供方（回归）')

print(f'\n全部通过：{len(PASSED)} 项')
shutil.rmtree(TMP, ignore_errors=True)
