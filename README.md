# PactSignal — inspectable commitments across conversations

Contest showcase, frozen for review on 18 October 2026. Fictional studio and repair cases; no private customer correspondence.

## Run the current showcase

From the repository root: `python -m http.server 8765 --bind 127.0.0.1 --directory dist`.
Open http://127.0.0.1:8765/final.html. No installation, API key or account is needed for the browser showcase. Use English for judging; optional Russian translation is for owner review.

- `scenario.html`: interactive two-client, two-order decision challenge.
- `lab.html`: actual browser inference with exported scikit-learn TF-IDF/logistic-regression weights plus separately identified deterministic context review.
- `index.html`, `repair.html`: precomputed fictional cases and source-linked history.
- `article.html`: business story and limitations.
- `video.html`: English narrated explanation and workflow demonstration.

## Evidence and limits

All datasets are synthetic. Raw model 13/20, existing guards 14/20 on the historical fresh set; JavaScript/Python parity 20/20. Context first pass 35/36, then the same 36 became development regression after correcting the miss. Workflow tests are not model-accuracy measurements. Owner feedback is real; simulated checks are not independent human validation.

No live mailbox, messenger or banking connection is demonstrated. No automatic money movement. Overview fixtures and sandbox state are separate. Context rules are implemented in the browser and are not automatically part of the legacy Python pipeline.

## Hosting

Root render.yaml deploys the current static frontend, including browser-local inference. No backend, paid model API or database is required. Use only a no-charge workspace; do not add billing or paid resources. The legacy source/render.yaml is not the current deployment configuration.

## Development checks

Run `node source/test_judge_acceptance.cjs`, `node source/test_context_safety.cjs`, `node source/test_context_amounts.cjs`, and `node source/test_model_lab.cjs` from the repository root. Python training/evaluation uses source/requirements.txt. Browser weights are already exported.

## License and publication

This repository contains the contest prototype and fictional examples. No private session transcript is included. The project currently carries no additional license grant; third-party components retain their own licenses. The open-source AI foundation is scikit-learn (BSD-3-Clause).
