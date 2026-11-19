"""SDK-fake-only backend benchmark; never a provider performance/quality claim.

Run from the checkout being measured. Output must be a new private directory.
The supplied manifest is a paper identity list, never blind reference answers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--paper',required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--concurrency',type=int,choices=[1,2],default=2)
    parser.add_argument('--fake-delay-ms',type=int,default=0)
    args=parser.parse_args()
    pdf=args.pdf.read_bytes()
    identity=next(p for p in json.loads(args.manifest.read_text())['papers'] if p['id']==args.paper)
    if hashlib.sha256(pdf).hexdigest()!=identity['sha256']:
        raise SystemExit('paper_identity_mismatch')
    if args.output.exists() or args.fake_delay_ms<0:
        raise SystemExit('output_must_be_new_and_delay_nonnegative')
    os.umask(0o077)
    args.output.mkdir(parents=True,mode=0o700)
    sys.path.insert(0,str(Path.cwd()))
    os.environ['PAPER_REVIEW_DATA_DIR']=str(args.output/'import-scratch')
    os.environ.pop('P5C_CONTROLLED_MODE',None)
    # No credential file is consumed, and OpenAI is replaced before app creation.
    with patch('dotenv.dotenv_values',return_value={}):
        from pytest import MonkeyPatch
        from backend.tests.model_test_config import configure_test_model
        configure_test_model(MonkeyPatch(),key='fake-benchmark',model='actual-model')
        from backend.app import create_app
        from backend.tests.auto_review_fake_sdk import AutoReviewFakeSDK
        from fastapi.testclient import TestClient
        original=AutoReviewFakeSDK.create
        AutoReviewFakeSDK.calls=[]
        def fake(self,**kwargs):
            result=original(**kwargs)
            if kwargs.get('stream'):
                def read():
                    time.sleep(args.fake_delay_ms/1000)
                    yield from result
                return read()
            time.sleep(args.fake_delay_ms/1000)
            return result
        with patch.object(AutoReviewFakeSDK,'create',fake),patch('backend.app.OpenAI',AutoReviewFakeSDK):
            app=create_app(args.output/'data')
            client=TestClient(app)
            begun=time.monotonic()
            uploaded=client.post('/api/documents',files={'file':('paper.pdf',pdf,'application/pdf')})
            assert uploaded.status_code in (200,201), uploaded.status_code
            doc=uploaded.json()
            parse_ms=round((time.monotonic()-begun)*1000)
            assert doc['page_count']==identity['pages']
            from backend.tests.test_auto_review_p2a_integration import budget
            # Generous software-only fixture budget, not a production recommendation.
            stage=budget(max_calls=90,max_input_tokens=2000000,max_output_tokens=180000,max_output_tokens_per_request=2000,max_duration_seconds=3600,max_cost_usd='10')
            payload={'document_id':doc['id'],'execution_policy':{'max_concurrency':args.concurrency,'requests_per_minute':120,'input_reservation_per_minute':2000000},'total_budget':stage,
                     **{s+'_budget':stage for s in ('p2a','p2b','p2c','p3','p4')}}
            made=client.post('/api/auto-review-tasks',json=payload)
            assert made.status_code==201,made.text
            task_id=made.json()['id']
            t=time.monotonic()
            client.post(f'/api/auto-review-tasks/{task_id}/start',json={'confirm_budget':True})
            run_ms=round((time.monotonic()-t)*1000)
            task=client.get(f'/api/auto-review-tasks/{task_id}').json()
            assert task['status']=='completed', (task['status'],task['error'])
            db=sqlite3.connect(args.output/'data/review.db'); db.row_factory=sqlite3.Row
            rows=db.execute('SELECT stage,input_estimate,status FROM auto_review_calls ORDER BY rowid').fetchall()
            stages={s:{'calls':sum(r['stage']==s for r in rows),'input_bytes':sum(r['input_estimate'] for r in rows if r['stage']==s)} for s in ('p2a','p2b','p2c','p3','p4')}
            source=client.get(f"/api/understanding-runs/{task['p2a_run_id']}/source-bundle").json()
            understanding=client.get(f"/api/understanding-runs/{task['p2a_run_id']}").json()
            coverage={k:understanding[k]['input_coverage'] for k in ('paper_profile','claim_evidence_map')}
            report=client.get(task['report_url']).json()
            (args.output/'report-DRAFT.md').write_text(report['body'])
            # Exact repeated system prompts: a lower bound on duplicate context.
            systems={}; repeated=0
            for call in AutoReviewFakeSDK.calls:
                text=call['messages'][0]['content'].encode()
                digest=hashlib.sha256(text).hexdigest()
                if digest in systems: repeated+=len(text)
                systems[digest]=True
            result={'kind':'fake_sdk_engineering_only','provider_requests':0,
                    'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                    'working_diff_sha256':hashlib.sha256(subprocess.check_output(['git','diff','HEAD'])).hexdigest(),
                    'pdf_sha256':identity['sha256'],'paper_id':args.paper,
                    'parse_ms':parse_ms,'pipeline_ms':run_ms,'end_to_end_ms':parse_ms+run_ms,
                    'synthetic_delay_per_request_ms':args.fake_delay_ms,'stages':stages,
                    'calls':len(rows),'input_bytes':sum(r['input_estimate'] for r in rows),
                    'repeated_system_bytes_lower_bound':repeated,'unit_count':len(source['reading_units']),
                    'coverage':coverage,'status':task['status'],'unknown_usage':task['unknown_usage_calls'],
                    'task_id':task_id,'draft_id':task['p4_draft_id'],'performance':task.get('performance'),
                    'request_audit':task['calls'],
                    'stage_run_ids':{s:task.get(s+'_run_id') for s in ('p2a','p2b','p2c','p3')},
                    'p4_project_id':task.get('p4_project_id'),
                    'five_minutes':'unverified','scientific_quality':'not_evaluated_by_fake_sdk'}
            source_hashes={}
            for directory in ('backend','scripts','frontend/src'):
                for source_file in Path(directory).rglob('*'):
                    if source_file.is_file() and source_file.suffix in {'.py','.ts','.tsx','.css','.txt'}:
                        source_hashes[source_file.as_posix()]=hashlib.sha256(source_file.read_bytes()).hexdigest()
            result['source_tree_sha256']=hashlib.sha256(json.dumps(source_hashes,sort_keys=True).encode()).hexdigest()
            result['prompt_sha256']={k:v for k,v in source_hashes.items() if k.startswith('backend/prompts/')}
            result['paper_manifest_sha256']=hashlib.sha256(args.manifest.read_bytes()).hexdigest()
            (args.output/'measurement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            print(json.dumps({k:result[k] for k in ('status','calls','input_bytes','end_to_end_ms','provider_requests')}))


if __name__=='__main__':
    main()
