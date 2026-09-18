"""迁移演练固化：inspect / import / verify / export / backup 全链（D09/D10/D11）。

* 合成临时树（V3 多修订 + 发布 + 项目 + 编排 + 三类凭据 + 目录缓存 + LLM 配置），
  不含真实数据、不含真实密钥。
* 验证：导入语义正确、旧 revision token 一次兼容、可重入零重复、源变化中止、
  凭据解密与根密钥独立恢复、反向导出与在线备份。

运行：python3 tests/test_storage_transfer.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix='wiz_transfer_'))
ROOT = TMP / 'source'
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP / 'wbroot')
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'wbroot' / 'data' / 'workbench.sqlite3')

ONT_ID = 'abc12345-1111-2222-3333-444444444444'
PROJ_ID = 'p1234567890a'
FLOW_ID = 'f1234567890a'
LEGACY_HASH = 'a' * 64

PASSED = []


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print(f'通过) {message}')
        return
    print(f'[失败] {message}')
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:1500])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)


def ontology_schema(name):
    return {'schemaVersion': 1, 'namespaces': {'mg': 'https://example.com/microgrid/'},
            'objectTypes': [{'id': 'mg:Cluster', 'displayName': name}],
            'linkTypes': [], 'properties': [], 'sharedProperties': [], 'valueTypes': [],
            'metadata': [], 'definitionOrder': ['mg:Cluster']}


def build_source_tree():
    ws = ROOT / 'ontology/workspaces' / ONT_ID
    ws.mkdir(parents=True)
    (ws / 'workspace.json').write_text(
        json.dumps({'id': ONT_ID, 'name': '迁移本体'}, ensure_ascii=False), encoding='utf-8')
    draft_base = ROOT / 'ontology/drafts/models' / ONT_ID
    for seq, q in ((1, ''), (2, 'Q2'), (3, 'Q3')):
        rev = draft_base / 'revisions' / f'{seq:04d}-deadbee'
        rev.mkdir(parents=True)
        state = {'ontology': ontology_schema('迁移本体'),
                 'workflow': {'objective': {'name': '迁移本体', 'question': q}, 'functions': [],
                              'actions': [], 'interfaces': [], 'release': {'note': '', 'reviewer': ''}},
                 'metrics': {'metrics': []}, 'rules': {'rules': []},
                 'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}
        (rev / 'ontology.json').write_text(json.dumps(state['ontology'], ensure_ascii=False), encoding='utf-8')
        (rev / 'workflow.json').write_text(json.dumps(state['workflow'], ensure_ascii=False), encoding='utf-8')
        (rev / 'metrics.yaml').write_text(yaml.safe_dump(state['metrics'], allow_unicode=True), encoding='utf-8')
        (rev / 'rules.yaml').write_text(yaml.safe_dump(state['rules'], allow_unicode=True), encoding='utf-8')
        (rev / 'layout.json').write_text(json.dumps(state['layout'], ensure_ascii=False), encoding='utf-8')
    (draft_base / 'current.json').write_text(json.dumps(
        {'seq': 3, 'revision': LEGACY_HASH, 'revisionDir': '0003-deadbee',
         'updatedAt': '2026-09-18T00:00:00+00:00'}), encoding='utf-8')
    rel = ROOT / 'ontology/releases/models' / ONT_ID / '1.0.0'
    rel.mkdir(parents=True)
    (rel / 'ontology.json').write_text(json.dumps(ontology_schema('迁移本体'), ensure_ascii=False), encoding='utf-8')
    (rel / 'workflow.json').write_text(json.dumps(
        {'objective': {'name': '迁移本体'}, 'functions': [], 'actions': [], 'interfaces': []},
        ensure_ascii=False), encoding='utf-8')
    entry = {'version': '1.0.0', 'changeType': 'initial', 'changeNote': '', 'reviewer': '',
             'createdAt': '2026-09-18T00:00:00+00:00', 'revision': 'imported',
             'parentVersion': None, 'reasons': []}
    (rel / 'manifest.yaml').write_text(yaml.safe_dump(entry, allow_unicode=True), encoding='utf-8')
    (ROOT / 'ontology/releases/models' / ONT_ID / 'index.json').write_text(
        json.dumps({'versions': [entry]}), encoding='utf-8')

    proj = ROOT / 'ontology/projects' / PROJ_ID
    proj.mkdir(parents=True)
    (proj / 'project.yaml').write_text(yaml.safe_dump(
        {'project_id': PROJ_ID, 'name': '迁移项目', 'ontology': ONT_ID,
         'ontology_version': '1.0.0', 'timezone': 'Asia/Shanghai'}, allow_unicode=True), encoding='utf-8')
    (proj / 'connections.yaml').write_text(yaml.safe_dump(
        {'connections': [{'id': 'c1', 'engine': 'mysql', 'host': 'db'}]}, allow_unicode=True), encoding='utf-8')
    (proj / 'bindings.yaml').write_text(yaml.safe_dump(
        {'object_bindings': [], 'observation_binding': {}, 'source_candidates': [], 'notice': ''},
        allow_unicode=True), encoding='utf-8')
    (proj / 'implementations.yaml').write_text(yaml.safe_dump({'implementations': []}, allow_unicode=True), encoding='utf-8')
    (proj / 'parameters.yaml').write_text(yaml.safe_dump({}, allow_unicode=True), encoding='utf-8')
    pd = ROOT / 'ontology/drafts/projects' / PROJ_ID / 'revisions' / '0001-abc12345'
    pd.mkdir(parents=True)
    for _k, f in (('project', 'project.yaml'), ('connections', 'connections.yaml'), ('bindings', 'bindings.yaml')):
        (pd / f).write_text((proj / f).read_text(), encoding='utf-8')
    (pd / 'implementations.yaml').write_text(yaml.safe_dump({'implementations': []}, allow_unicode=True), encoding='utf-8')
    (pd / 'parameters.yaml').write_text(yaml.safe_dump({}, allow_unicode=True), encoding='utf-8')
    (ROOT / 'ontology/drafts/projects' / PROJ_ID / 'current.json').write_text(json.dumps(
        {'seq': 1, 'revision': 'b' * 64, 'revisionDir': '0001-abc12345',
         'updatedAt': '2026-09-18T00:00:00+00:00'}), encoding='utf-8')

    fd = ROOT / 'ontology/drafts/flows' / FLOW_ID / 'revisions' / '0001-11111111'
    fd.mkdir(parents=True)
    flow_state = {'schemaVersion': 1, 'flowId': FLOW_ID, 'name': '迁移编排', 'description': '',
                  'status': 'active', 'inputs': [], 'outputs': [], 'connections': [], 'nodes': [],
                  'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}
    (fd / 'flow.json').write_text(json.dumps(flow_state, ensure_ascii=False), encoding='utf-8')
    (ROOT / 'ontology/drafts/flows' / FLOW_ID / 'current.json').write_text(json.dumps(
        {'seq': 1, 'revision': 'c' * 64, 'revisionDir': '0001-11111111',
         'updatedAt': '2026-09-18T00:00:00+00:00'}), encoding='utf-8')

    vault = ROOT / 'ontology/vault/projects' / PROJ_ID
    vault.mkdir(parents=True)
    (vault / 'conn1').write_text('password-plaintext-1')
    api_dir = vault / 'api-credentials'
    api_dir.mkdir()
    (api_dir / 'cred-1.json').write_text(json.dumps(
        {'id': 'cred-1', 'name': '外部系统', 'secret': 'sk-test-2'}), encoding='utf-8')
    (ROOT / 'ontology/catalogs/projects' / PROJ_ID).mkdir(parents=True)
    (ROOT / 'ontology/catalogs/projects' / PROJ_ID / 'c1.json').write_text(json.dumps(
        {'database': 'db', 'tables': [{'name': 't1'}], 'refreshedAt': ''}), encoding='utf-8')
    llm = ROOT / 'ontology/vault/llm-providers'
    llm.mkdir(parents=True)
    (llm / 'llm-aaa.json').write_text(json.dumps(
        {'id': 'llm-aaa', 'name': '主力', 'endpoint': 'https://x/v1/chat/completions',
         'model': 'm1', 'timeout': 60, 'temperature': 0, 'api_key': 'sk-3', 'is_default': True}),
        encoding='utf-8')


def main():
    from workbench import storage
    from workbench.storage import transfer
    build_source_tree()
    check(transfer.main(['init']) == 0, 'init 初始化存储库')
    check(transfer.main(['import', '--source', str(ROOT)]) == 0, 'import 导入无错误')
    verify_rc = transfer.main(['verify', '--source', str(ROOT)])
    check(verify_rc == 0, 'verify 全部核对通过（语义对比非仅计数）')

    from workbench import workspaces, projects, flows
    old_token = workspaces.current_token(ONT_ID)
    check(old_token == LEGACY_HASH, 'head token 继承旧 revision（一次兼容）')
    state = workspaces.read_draft(ONT_ID)
    check(state['workflow']['objective']['question'] == 'Q3', '当前草稿语义正确（Q3）')
    state['workflow']['objective']['question'] = 'Q4'
    r = workspaces.write_draft(state, expected_token=old_token)
    check(r['revision'].startswith('r-'), '旧 token 保存成功且换新 r- token')
    try:
        workspaces.write_draft(state, expected_token=old_token)
        check(False, '旧 token 第二次保存被拒')
    except storage.RevisionConflict:
        check(True, '旧 token 只接受一次')
    check([p['name'] for p in projects.listing()] == ['迁移项目'], '项目列表正确')
    check(projects.load(PROJ_ID)[0]['ontologyVersion'] == '1.0.0', '项目引用版本固定正确')
    check([f['name'] for f in flows.listing()] == ['迁移编排'], '编排列表正确')

    from workbench.storage import configuration as config_store
    from workbench.storage import assets as store
    from workbench.storage.engine import read_connection
    with read_connection() as conn:
        proj_uid = store.get_asset(conn, 'project', PROJ_ID)['asset_uid']
        check(config_store.get_secret(conn, 'connection', proj_uid, 'conn1') == 'password-plaintext-1',
              '连接凭据迁移后可解密（工具进程内，不输出值）')
        check(config_store.get_secret(conn, 'api', proj_uid, 'cred-1') == 'sk-test-2',
              'API 凭据迁移后可解密')
        check(config_store.get_secret(conn, 'model', 'global', 'llm-aaa') == 'sk-3',
              '模型密钥迁移后可解密')
        check(config_store.get_setting(conn, config_store.DEFAULT_PROVIDER_KEY) == 'llm-aaa',
              '默认提供方设置迁移正确')
        check(config_store.read_catalog(conn, proj_uid, 'c1') is not None, '目录缓存迁移正确')

    # 可重入
    from sqlalchemy import text as sql
    with read_connection() as conn:
        before = conn.execute(sql('SELECT COUNT(*) FROM wb_snapshots')).scalar()
    check(transfer.main(['import', '--source', str(ROOT)]) == 0, '重入导入完成')
    with read_connection() as conn:
        after = conn.execute(sql('SELECT COUNT(*) FROM wb_snapshots')).scalar()
    check(before == after, '重入导入零重复')

    # 源变化中止：同 key 不同内容必须失败而非覆盖
    flow_file = ROOT / 'ontology/drafts/flows' / FLOW_ID / 'revisions' / '0001-11111111' / 'flow.json'
    flow_file.write_text(json.dumps(
        {'schemaVersion': 1, 'flowId': FLOW_ID, 'name': '迁移编排', 'description': 'changed',
         'status': 'active', 'inputs': [], 'outputs': [], 'connections': [], 'nodes': [],
         'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}, ensure_ascii=False), encoding='utf-8')
    try:
        rc_changed = transfer.main(['import', '--source', str(ROOT)])
        check(rc_changed != 0, '源变化后导入中止（同 key 不同 hash 拒绝）')
    except RuntimeError:
        check(True, '源变化后导入中止（同 key 不同 hash 拒绝）')

    # 根密钥独立恢复：删 key → 解密明确报错 → 还原 key → 恢复可用
    from workbench.storage import secret_store
    key_file = Path(os.environ['WIZ_WORKBENCH_ROOT']) / 'keys' / 'wb-root.key'
    key_backup = key_file.read_bytes()
    key_file.unlink()
    secret_store._root = None
    try:
        with read_connection() as conn:
            try:
                config_store.get_secret(conn, 'connection', proj_uid, 'conn1')
                check(False, '缺根密钥时应明确报错')
            except secret_store.SecretKeyError:
                check(True, '缺根密钥时明确报错（不静默重建）')
            except Exception as exc:
                check(False, '缺根密钥时报错类型可诊断', str(exc))
    finally:
        key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_file.write_bytes(key_backup)
        secret_store._root = None
    with read_connection() as conn:
        check(config_store.get_secret(conn, 'connection', proj_uid, 'conn1') == 'password-plaintext-1',
              '根密钥恢复后凭据可解密')

    # 反向导出 + 在线备份
    check(transfer.main(['export', '--output', str(TMP / 'exported')]) == 0, '反向导出成功')
    exported_state = json.loads(((TMP / 'exported' / 'ontology/drafts/models' / ONT_ID / 'revisions') /
                                 sorted((TMP / 'exported' / 'ontology/drafts/models' / ONT_ID / 'revisions').iterdir())[-1].name /
                                 'workflow.json').read_text())
    check(exported_state['objective']['question'] == 'Q4', '反向导出包含迁移后的新写入（Q4）')
    check((TMP / 'exported' / 'ontology/vault/projects' / PROJ_ID / 'conn1').read_text() ==
          'password-plaintext-1', '反向导出凭据（0600）可用于回滚')
    check(transfer.main(['backup', '--output', str(TMP / 'backup.sqlite3')]) == 0, '在线备份成功（备份 API）')
    check((TMP / 'backup.sqlite3').is_file(), '备份文件存在')


if __name__ == '__main__':
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f'统计：{len(PASSED)} 项全部通过')
