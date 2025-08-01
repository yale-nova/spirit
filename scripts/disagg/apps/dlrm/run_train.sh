#!/bin/bash

python3 dlrm_s_pytorch.py \
  --arch-sparse-feature-size=16 --arch-mlp-bot="13-512-256-64-16" --arch-mlp-top="512-256-1"\
  --data-generation=dataset \
  --data-set=kaggle \
  --raw-data-file=/root/dlrm_bench/train.txt \
  --mini-batch-size=128 \
  --print-freq=1024 \
  --print-time \
  --loss-function=bce --round-targets=True --learning-rate=0.1 \
  --test-mini-batch-size=16384 --test-num-workers=16 --test-freq=1024 \
  --save-model=/root/dlrm_bench/kaggle-trained.pt
