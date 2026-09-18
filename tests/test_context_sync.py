"""Shared handoff regression. Only temporary roots; no application/data imports."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.collaboration/context.py'


class ContextSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wiz_context_test_')
        self.root = Path(self.temp.name)
        (self.root / '.collaboration').mkdir()
        (self.root / '.collaboration/baseline.md').write_text('## 基线\n测试基线\n')
        self.addCleanup(self.temp.cleanup)

    def call(self, *args, payload=None, ok=True):
        p = subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root), *args],
                           input=json.dumps(payload) if payload is not None else None,
                           text=True, capture_output=True)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0)
        return p

    def data(self, actor='codex', task='test'):
        ticket = self.call('read', '--actor', actor)['ticket']
        return {'ticket': ticket, 'task': task, 'status': 'ready', 'summary': '测试需求交付'}

    def test_append_idempotence_and_disagreement(self):
        a = self.data()
        b = self.data('zcode')
        first = self.call('record', payload=a)
        self.assertTrue(self.call('record', payload=a)['duplicate'])
        b.update(status='blocked', summary='等待需求确认')
        self.assertTrue(self.call('record', payload=b)['context_advanced'])
        text = (self.root / 'session_context.md').read_text()
        self.assertIn('测试需求交付', text)
        self.assertIn('等待需求确认', text)
        a['summary'] = '不能覆盖'
        self.call('record', payload=a, ok=False)
        self.assertEqual(len(list((self.root / '.collaboration/entries').glob('*.json'))), 2)
        self.assertTrue(first['entry'])

    def test_no_change_does_not_append(self):
        a = self.data()
        a['status'] = 'no_change'
        self.call('record', payload=a)
        self.assertFalse(list((self.root / '.collaboration/entries').glob('*.json')))

    def test_hook_one_reminder_and_round_ack(self):
        base = {'cwd': str(self.root), 'session_id': 's', 'turn_id': 't'}
        self.call('hook', '--actor', 'codex', payload=dict(base, hook_event_name='UserPromptSubmit'))
        out = self.call('hook', '--actor', 'codex', payload=dict(base, hook_event_name='Stop'))
        self.assertEqual(out['decision'], 'block')
        out2 = self.call('hook', '--actor', 'codex', payload=dict(base, hook_event_name='Stop'))
        self.assertNotIn('decision', out2)
        self.assertIn('尚未交付', out2['systemMessage'])
        import hashlib
        ticket = hashlib.sha256(json.dumps(['codex', 's', 't'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        self.call('record', payload={'ticket': ticket, 'task': 'round', 'status': 'no_change', 'summary': '无新增'})
        self.assertEqual(self.call('hook', '--actor', 'codex', payload=dict(base, hook_event_name='Stop')), {})

    def test_hook_scope_missing_ids_and_active_guard(self):
        self.assertEqual(self.call('hook', '--actor', 'codex', payload={'cwd':'/', 'hook_event_name':'Stop'}), {})
        missing = self.call('hook', '--actor', 'codex', payload={'cwd':str(self.root), 'hook_event_name':'Stop'})
        self.assertIn('缺少', missing['systemMessage'])
        out = self.call('hook', '--actor', 'codex', payload={'cwd':str(self.root), 'session_id':'s', 'turn_id':'x', 'hook_event_name':'Stop', 'stop_hook_active':True})
        self.assertNotIn('decision', out)

    def test_parallel_processes_no_lost_records(self):
        data = [self.data('codex' if i % 2 else 'zcode', f'task-{i}') for i in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda value: self.call('record', payload=value), data))
        entries = [json.loads(p.read_text()) for p in (self.root / '.collaboration/entries').glob('*.json')]
        self.assertEqual(sorted(x['sequence'] for x in entries), list(range(1, 13)))
        text = (self.root / 'session_context.md').read_text()
        for i in range(12):
            self.assertIn(f'### task-{i} ·', text)

    def test_secret_rejection_and_regenerate(self):
        data = self.data()
        data['summary'] = 'sk-' + 'EXAMPLE' * 5
        self.call('record', payload=data, ok=False)
        self.assertNotIn(data['summary'], (self.root / 'session_context.md').read_text())
        data['summary'] = '安全摘要'
        self.call('record', payload=data)
        (self.root / 'session_context.md').unlink()
        self.call('render')
        self.assertIn('安全摘要', (self.root / 'session_context.md').read_text())

    def test_recover_after_entry_written_before_ticket(self):
        data = self.data()
        result = self.call('record', payload=data)
        ticket = self.root / '.runtime/context-tickets' / (data['ticket'] + '.json')
        state = json.loads(ticket.read_text())
        for key in ('record_hash', 'entry', 'completed_at'):
            state.pop(key)
        ticket.write_text(json.dumps(state))
        self.assertEqual(self.call('record', payload=data)['entry'], result['entry'])
        self.assertEqual(len(list((self.root / '.collaboration/entries').glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
