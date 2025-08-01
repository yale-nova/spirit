#!/bin/bash
DIR=$(dirname "${BASH_SOURCE[0]}")
cd ${DIR}
echo "Current directory: $(pwd)"

python main.py
