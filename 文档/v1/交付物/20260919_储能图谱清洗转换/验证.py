import os,tempfile,json,sys,uuid,base64,hashlib
from pathlib import Path
root=Path.cwd();out=root/'文档/交付物/20260919_储能图谱清洗转换'
with tempfile.TemporaryDirectory(prefix='wiz-graph-review-') as tmp:
 os.environ['WIZ_WORKBENCH_ROOT']=tmp
 os.environ['WIZ_DATABASE_URL']='sqlite:///'+tmp+'/isolated.sqlite3'
 os.environ['WIZ_WORKBENCH_PORT']='18907'
 sys.path.insert(0,str(root))
 from workbench import auth,storage,config_packages as cp,workspaces
 from workbench.model_routes import validate
 from workbench.model_format import decode_state,encode_state
 from workbench.storage import assets
 state=json.loads((out/'本体草稿.json').read_text()); errors=validate(decode_state(state))
 print('DEFINITION_ERRORS',json.dumps(errors,ensure_ascii=False))
 assert errors==['规则 充放电计划 缺少规则内容','规则 充放电计划 缺少输出结果'],errors
 user=auth.create_user('conversion_test','Conversion-Test-98231');auth.bind_request(user);owner=user['userId']
 data=(out/'储能本体_清洗待确认.zip').read_bytes(); digest=hashlib.sha256(data).hexdigest()
 begin=cp.stage_begin(owner,'storage.zip',len(data),digest)
 upload=begin['uploadId']
 cp.stage_chunk(owner,upload,0,base64.b64encode(data).decode(),digest)
 preview=cp.build_import_preview(owner,upload)
 assert not preview['blockers'],preview
 frozen=cp.import_previews.get(preview['previewToken'],owner)['preview']
 names={a['packageKey']:a['suggestedName'] for a in preview['assets']}
 imported=cp.import_transaction(owner,frozen,str(uuid.uuid4()),names)
 print('RECEIPT',json.dumps(imported['receipt']['assets'],ensure_ascii=False))
 asset=imported['receipt']['assets'][0]
 mid=asset.get('newId')
 assert mid,asset
 loaded=assets.read_current('model',mid,owner_user_id=owner)['snapshot']['payload']
 assert loaded['ontology']==state['ontology']
 assert loaded['workflow']==state['workflow']
 assert encode_state(decode_state(loaded))==loaded
 assert loaded['workspaceId']==mid and mid!=state['workspaceId']
 assert len(loaded['ontology']['extensions']['conversionProvenance']['originalGraph']['@graph'])==134
 result={'schemaValidation':'passed','encodeDecodeRoundtrip':'passed','manifestAndHash':'passed','isolatedImportPreview':'passed','isolatedImportAndReadback':'passed','originalGraphPreserved':134,'expectedPublishBlockers':errors,'realWorkbenchModified':False,'browserTested':False}
 (out/'验证结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print('PASS')
