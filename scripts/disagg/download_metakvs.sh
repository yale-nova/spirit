#!/bin/bash
if [ -z "$SPIRIT_PATH" ] || [ ! -d "$SPIRIT_PATH" ]; then
    echo "Error: SPIRIT_PATH is not set or does not exist" >&2
    exit 1
fi

DOWNLOAD_PATH="$SPIRIT_PATH/downloads"
mkdir -p "$DOWNLOAD_PATH"
cd "$DOWNLOAD_PATH"

# KVS
wget https://ftp.pdl.cmu.edu/pub/datasets/twemcacheWorkload/cacheDatasets/metaKV/meta_kvcache_traces_1.oracleGeneral.bin.zst

# Make symlink
sudo ln -s "$DOWNLOAD_PATH" /workload
