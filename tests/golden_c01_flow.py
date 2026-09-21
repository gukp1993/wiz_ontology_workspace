"""C01 金样共用夹具（2026-09-21 验收补充修复）。

金样回放要求可复现：编排必须用**固定 flowId** 直接 `flows.save_draft` 播种（不能走
随机 id 的 `flows.create`），生成端（make_validation_golden.py）与回放端
（test_validation_split.py）各自在自己的隔离根内写入同一份内容。

用途：锁定「纯空白 providerId 属已声明但无效」的判据——适配层不得把它当作未声明而
跳过检查（03 §2.2 / 04 §2.2，C01 反例的模型空集合分支）。仅构造数据，不执行任何节点。
"""

FLOW_ID = 'c01goldenblankprovider'
FLOW_NAME = 'C01空白提供方编排'


def flow_state():
    """空白 providerId（三个空格）的 Python 计算节点编排，输出 number。"""
    return {
        'schemaVersion': 1, 'flowId': FLOW_ID, 'name': FLOW_NAME,
        'description': '', 'status': 'active', 'inputs': [], 'connections': [],
        'outputs': [{'id': 'out_p', 'name': 'power', 'label': '功率', 'type': {'type': 'number'},
                     'binding': {'kind': 'node', 'nodeId': 'nd', 'outputId': 'nd_out'}}],
        'nodes': [{'id': 'nd', 'kind': 'python', 'name': 'LLM加工', 'inputs': [],
                   'outputs': [{'id': 'nd_out', 'name': 'value', 'label': '值',
                                'type': {'type': 'number'}}],
                   # providerId 是纯空白：已声明但无效（判据见接口文档 04 §2.2）
                   'implementation': {'code': 'def main():\n    return {"value": 1}\n',
                                      'providerId': '   '}}],
        'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}},
    }


def seed(flows_module):
    """把共用编排写入当前隔离根（幂等：同一 flowId 再次写入按当前 head 覆盖同内容）。"""
    flows_module.save_draft(flow_state())
