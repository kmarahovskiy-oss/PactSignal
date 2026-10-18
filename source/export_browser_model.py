"""Export trained TF-IDF + logistic regression, with no retraining on evaluation."""
import json,hashlib
from pathlib import Path
from pactsignal import IntentModel
p=Path(__file__).resolve().parents[1];m=IntentModel();parts=[]
for name,v in m.features.transformer_list:
 parts.append(dict(name=name,vocabulary=v.vocabulary_,idf=v.idf_.tolist(),range=list(v.ngram_range),analyzer=v.analyzer))
d=dict(version=1,training_examples=m.fit_examples,parts=parts,classes=m.model.classes_.tolist(),coef=m.model.coef_.tolist(),intercept=m.model.intercept_.tolist(),training_sha256=hashlib.sha256((p/'source/training_data.py').read_bytes()).hexdigest())
(p/'dist/model.json').write_text(json.dumps(d,separators=(',',':')))
print('Exported',m.fit_examples,'training examples; frozen model sha256',hashlib.sha256((p/'dist/model.json').read_bytes()).hexdigest())
