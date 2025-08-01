#!/bin/bash

python3 dlrm_s_pytorch.py \
  --inference-only \
  --arch-sparse-feature-size=16 --arch-mlp-bot="13-512-256-64-16" --arch-mlp-top="512-256-1" \
  --data-generation=dataset \
  --data-set=kaggle \
  --raw-data-file=/root/dlrm_bench/test.txt \
  --mini-batch-size=128 \
  --print-freq=1 \
  --print-time \
  --processed-data-file=/root/dlrm_bench/kaggleAdDisplayChallenge_processed.npz \
  --load-model=/root/dlrm_bench/kaggle-trained.pt \
  --data-randomize=none
# --web-metric-server
