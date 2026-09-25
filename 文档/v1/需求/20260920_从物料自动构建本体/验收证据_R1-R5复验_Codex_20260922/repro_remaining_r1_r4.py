"""Read-only synthetic verification; run from auto_build cwd. No services or model calls."""
import ast,json,os,pathlib,tempfile,types,sys
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path.cwd()))
os.environ.pop('WIZ_DATABASE_URL',None)
os.environ['WIZ_WORKBENCH_ROOT']=tempfile.mkdtemp(prefix='codex-r14-')
from workbench.ontology_build import pipeline as p,alignment as a,ontology_adapter as oa

def pair(name,f):
 return [dict(key='obj',type='object',name=name,definition=name,fields={},evidence={'definition':[f]},evidenceStatus='supported'),dict(key='prop',type='property',name='额定功率',definition=name+'额定功率',ownerKey='obj',fields={'dataType':'number'},evidence={'definition':[f],'dataType':[f]},evidenceStatus='supported')]
def fake(provider,scope,batch,**kwargs):
 if len(batch)>1:return dict(ok=False,error='输出截断',candidates=[])
 f=batch[0]['id'];return dict(ok=True,candidates=pair('电池' if f=='f1' else '逆变器',f))
with patch.object(p.llm,'extract_candidates',fake):
 r=p._extract_batch_with_split({}, {},[{'id':'f1'},{'id':'f2'}])
v,_=p.verify_candidates(r['candidates'],['f1','f2'])
v=a.namespace_batch(v,1,existing=[])
v,_=p.verify_candidates(a.align(v)['candidates'],['f1','f2'])
final=p._dedupe_keys(p.adapt_candidates(v)['candidates'])
for i,n in enumerate(final):n['id']='c'+str(i)
graph=oa.assemble(final)[0]['@graph'];byid={n['@id']:n['rdfs:label'] for n in graph}
actual=[(n['rdfs:comment'],byid[n['rdfs:domain']['@id']]) for n in graph if 'rdfs:domain' in n]
print('R1 split ok:',r['ok'],'actual domains:',actual)
assert actual==[('电池额定功率','电池'),('逆变器额定功率','电池')], 'Observed defect changed; re-evaluate'

# Execute the actual nested callback with persistence/runner stubbed only.
source=pathlib.Path('workbench/ontology_build/pipeline.py').read_text();tree=ast.parse(source)
run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_generate')
flush=next(n for n in run.body if isinstance(n,ast.FunctionDef) and n.name=='_flush_batch')
wrapper=ast.parse('def harness():\n    rejected_total = 0\n    return None').body[0]
wrapper.body=[wrapper.body[0],flush,ast.Return(value=ast.Name(id='_flush_batch',ctx=ast.Load()))]
g=dict(vars(p));rows=[]
g.update(owner_id='u',task_id='t',run_id='r',batch_id='b',usage=p._usage(),failed_batches=[],batch_log=[],notes=[],done_positions=set(),accumulated=[],by_id={'f1':{},'f2':{}},weak_fact_ids=set(),batches=[[],[]],model_facts=[],label='抽取')
g['runner']=types.SimpleNamespace(content_tx=lambda o,r,f:f(None),stage=lambda *a,**k:None,lease_of=lambda *a:None,Cancelled=type('Cancelled',(Exception,),{}))
g['store']=types.SimpleNamespace(all_candidates=lambda *a,**k:list(rows),update_run=lambda *a,**k:None)
g['_checkpoint']=lambda *a,**k:{}
g['_append_candidates']=lambda conn,o,t,b,items:rows.extend(dict(i) for i in items)
exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[])),'actual_flush_callback','exec'),g)
flush_fn=g['harness']()
flush_fn(1,{'ok':True,'candidates':[pair('园区','f1')[0]]})
partial={'ok':False,'error':'拆批后部分失败','candidates':[pair('电池','f2')[0]]}
flush_fn(2,partial)
print('R4 initial partial rows:',[(x['key'],x['name'],x.get('alignedKey')) for x in rows])
g['accumulated'][:]=[dict(x) for x in rows]
flush_fn(2,partial)
print('R4 repeated partial rows:',[(x['key'],x['name'],x.get('alignedKey')) for x in rows])
assert len(rows)==3 and rows[-1]['key']=='b2:obj_', 'Observed defect changed; re-evaluate'
print('Both outstanding defects reproduced; assertions describe current defects, not desired behavior.')
