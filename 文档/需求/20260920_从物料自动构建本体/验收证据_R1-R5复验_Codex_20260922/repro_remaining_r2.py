import runpy,pathlib,sys
sys.path.insert(0,str(pathlib.Path.cwd()))
m=runpy.run_path('tests/test_ontology_build_conflict_gate.py')
m['new_isolated_root']('independent')
sto,store,d=m['sto'],m['store'],m['delivery']
try:
 def seed(c):
  t=store.create_task(c,m['UID'],'synthetic'); b=store.create_batch(c,t,m['UID'],'',{})
  for n in [dict(type='object',key='obj',name='电池',definition='电池',fields={},evidenceStatus='conflict',conflicts=[{'field':'definition','note':'x'}]),dict(type='property',key='p',name='功率',definition='额定功率',ownerKey='obj',fields={'dataType':'number'},evidenceStatus='supported')]:
   store.create_candidate(c,t,m['UID'],b,dict(n,decision='include',evidence={}))
  return t,b
 with sto.write_tx() as tx:t,b=tx.run(seed)
 with sto.read_connection() as c:
  pre=d.precheck(c,t,b);print('precheck',pre['ok'],pre['issues'],'excluded',pre['conflictExcluded'],'counts',pre['counts'])
  assert pre['ok'] and pre['conflictExcluded']==1
  try:d.prepare_payload(c,t,b)
  except d.DeliveryBlocked as e:print('prepare blocked:',str(e))
  else:raise AssertionError('Expected missing owner failure; re-evaluate')
finally:m['shutdown']()
