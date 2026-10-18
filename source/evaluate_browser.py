import json,subprocess,hashlib
from pathlib import Path
from pactsignal import IntentModel,Message,analyze_message
root=Path(__file__).resolve().parents[1];fixture=json.loads((root/'source/fresh_evaluation.json').read_text());m=IntentModel()
script="const m=require('./dist/model.json'),p=require('./dist/inference.js'),f=require('./source/fresh_evaluation.json');console.log(JSON.stringify(f.rows.map(x=>p.predict(m,x[1]))));"
browser=json.loads(subprocess.check_output(['node','-e',script],cwd=root));rows=[]
for (expected,text),js in zip(fixture['rows'],browser):
 py=m.predict(text);assert py['label']==js['label'];assert abs(py['score_not_calibrated']-js['score'])<0.000051,(text,py,js)
 full=analyze_message(Message('test','test','2026-10-18T00:00:00Z','fictional',text),m)
 rows.append(dict(text=text,expected=expected,model=py['label'],hybrid=full['status'],basis=full['decision_basis'],score=py['score_not_calibrated']))
result=dict(description=fixture['description'],model_sha256=hashlib.sha256((root/'dist/model.json').read_bytes()).hexdigest(),fixture_sha256=hashlib.sha256((root/'source/fresh_evaluation.json').read_bytes()).hexdigest(),total=len(rows),raw_correct=sum(r['expected']==r['model'] for r in rows),hybrid_correct=sum(r['expected']==r['hybrid'] for r in rows),browser_python_parity=len(rows),rows=rows)
(root/'dist/evaluation.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
