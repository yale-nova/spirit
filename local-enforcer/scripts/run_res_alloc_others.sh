#!/bin/bash
log_path="logs/spirit_python.log"

# Check if the correct number of arguments are provided
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <path to a resource allocation config file> <algorithm name>"
    exit 1
fi

# Assign arguments to variables for better readability
res_alloc_config_path="$1"
alloc_algo="$2"

# Check if alloc_algo is one of the allowed values
valid_algos=("spirit" "static" "partial" "oracle" "inc-trade" "fij-trade")
is_valid=false

for algo in "${valid_algos[@]}"; do
    if [ "$alloc_algo" = "$algo" ]; then
        is_valid=true
        break
    fi
done

if ! $is_valid; then
    echo "Error: Invalid algorithm name. Must be one of: ${valid_algos[*]}"
    exit 1
fi

# Check if the python environment exists
if [ ! -d "/opt/venv/bin" ]; then
    echo "Error: Python environment not found at /opt/venv/bin"
    exit 1
fi

# Activate the preconfigured python env
source /opt/venv/bin/activate

# Check if the program directory exists
if [ ! -d "/spirit-controller/res_allocation" ]; then
    echo "Error: Program directory not found at /spirit-controller/res_allocation"
    exit 1
fi

# Run the python program
cd /spirit-controller/res_allocation || exit 1

# Check if the config file exists and is readable
if [ ! -f "$res_alloc_config_path" ] || [ ! -r "$res_alloc_config_path" ]; then
    echo "Error: Config file does not exist or is not readable: $res_alloc_config_path"
    exit 1
fi

python3 main_memcached.py --config "${res_alloc_config_path}" --allocator "${alloc_algo}" > "${log_path}" 2>&1
