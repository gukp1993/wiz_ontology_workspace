import os,sys,tempfile,json
from unittest.mock import patch
from pathlib import Path
repo=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(repo))
with tempfile.TemporaryDirectory(prefix='wiz-review-payload-') as root:
    os.environ['WIZ_WORKBENCH_ROOT']=root
    os.environ['WIZ_DATABASE_URL']='sqlite:///'+root+'/data/workbench.sqlite3'
    from workbench.ontology_build import review
    candidate={'id':'probe-series','taskId':'probe-task','type':'property','name':'SOC采样','definition':'电量百分比采样','fields':{'dataType':'timeSeries','valueType':'double'},'revision':'r-probe'}
    # Exact saveEdit payload at BuildReviewPage.vue:673-675 after user chooses number (UI 数值 option).
    payload={'name':'SOC采样新名称','definition':'电量百分比采样','dataType':{'type':'timeSeries','valueType':'number'}}
    with patch.object(review,'owner',return_value='probe-owner'), patch.object(review.store,'get_candidate',return_value=candidate), patch.object(review.store,'candidate_view',side_effect=lambda row:row):
        try:
            review.update_candidate(None,'probe-series',payload,'r-probe')
        except ValueError as exc:
            print(json.dumps({'result':'REPRODUCED','payload':payload,'error':str(exc),'storage':'mocked read only; no database opened'},ensure_ascii=False))
        else:
            raise AssertionError('Expected actual review.update_candidate to reject frontend timeSeries payload')
