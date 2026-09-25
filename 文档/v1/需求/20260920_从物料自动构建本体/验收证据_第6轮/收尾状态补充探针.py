# -*- coding: utf-8 -*-
import sys,importlib.util
from pathlib import Path
root=Path('/Users/gukepeng/Desktop/ZHDL/code/wiz_ai/wiz_kq_builder_v2-ontology-build')
sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location('fixture',root/'tests/test_ontology_build_finish_guard.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
try:
 for state,cancel in [('failed',False),('interrupted',False),('running',True)]:
  m.new_isolated_root('independent-'+state)
  task,batch,run=m.seed_task_run()
  with m.runner.begin_worker_scope(m.UID,run):
   with m.sto.write_tx() as tx:
    tx.run(lambda c:m.store.update_run(c,run,m.UID,state=state,cancel_requested=cancel))
   m.runner.finish_success(m.UID,run)
   got=m.read_run(run)
   assert got['state']==state,(state,got)
   assert m.read_task(task)['status']=='generating'
   print(state,'cancel=',cancel,'preserved; task not advanced')
 print('EXTRA_PROBE_PASS')
finally:m.cleanup_roots()
