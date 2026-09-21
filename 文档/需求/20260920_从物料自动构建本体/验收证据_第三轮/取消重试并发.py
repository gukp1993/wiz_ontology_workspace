import os,tempfile,sys,threading,json
from pathlib import Path
root=Path(tempfile.mkdtemp(prefix='wiz_build_race_'))
os.environ['WIZ_WORKBENCH_ROOT']=str(root)
os.environ['WIZ_DATABASE_URL']='sqlite:///'+str(root/'data/workbench.sqlite3')
sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store
from workbench.ontology_build import runner
sto.initialize()
uid='isolated-probe-owner'
with sto.write_tx() as tx:
 def setup(c):
  t=store.create_task(c,uid,'race probe'); r,l=store.create_run(c,t,uid,'scan',{})
  return r,l
 rid,oldlease=tx.run(setup)
a_started=threading.Event(); b_started=threading.Event(); a_go=threading.Event(); b_go=threading.Event(); out={}
def a(u,r):
 a_started.set();a_go.wait(8)
 out['old_worker_lease']=runner.lease_of(u,r)
 try:
  runner.stage(u,r,'OLD_WORKER_WRITE','old worker wrote after retry')
  out['stale_write_accepted']=True
 except runner.Cancelled:out['stale_write_accepted']=False
def b(u,r):
 b_started.set();b_go.wait(8)
a_thread=runner.submit(uid,rid,a);assert a_started.wait(3)
runner.request_cancel(uid,rid)
with sto.write_tx() as tx:
 tx.run(lambda c:store.update_run(c,rid,uid,state='queued',attempt=2,cancel_requested=False))
b_thread=runner.submit(uid,rid,b);assert b_started.wait(3)
a_go.set();a_thread.join(4)
with sto.read_connection() as c:
 row=store.get_run(c,rid,uid)
 out.update({'old_lease':oldlease,'new_lease':row['lease_token'],'state_while_new_worker_waiting':row['state'],'stage':row['stage'],'tracked_active_runs':runner.active_runs(),'remaining_lease':runner.lease_of(uid,rid)})
b_go.set();b_thread.join(4)
print(json.dumps(out,ensure_ascii=False,indent=2));print('isolated_root:',root)
assert out['stale_write_accepted'], 'race did not reproduce'
