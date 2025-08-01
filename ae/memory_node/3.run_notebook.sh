#!/bin/bash
set -euo pipefail

SPIRIT_PATH="/opt/spirit"
NOTEBOOK="$SPIRIT_PATH/spirit-controller/ae/memory_node/spirit_ae.ipynb"
LOG_DIR="$SPIRIT_PATH/spirit-controller"
LOG_FILE="$LOG_DIR/jupyter.log"
VENV_DIR="$LOG_DIR/myenv"

cd "$LOG_DIR"

# Activate/create venv idempotently
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# Function to kill existing notebook processes matching the notebook path
cleanup_existing() {
  # Find Jupyter processes whose command line contains the notebook path
  pids=()
  while IFS= read -r pid; do
    pids+=("$pid")
  done < <(pgrep -f "jupyter.*$(basename "$NOTEBOOK")" || true)

  if [ "${#pids[@]}" -gt 0 ]; then
    echo "Found existing Jupyter notebook process(es): ${pids[*]}" >> "$LOG_FILE"
    for pid in "${pids[@]}"; do
      echo "Attempting graceful termination of PID $pid" >> "$LOG_FILE"
      kill "$pid" || true
    done
    # wait up to 5 seconds for them to exit
    for i in {1..5}; do
      sleep 1
      still=()
      for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
          still+=("$pid")
        fi
      done
      if [ "${#still[@]}" -eq 0 ]; then
        break
      fi
      pids=("${still[@]}")
    done
    if [ "${#pids[@]}" -gt 0 ]; then
      echo "Forcing kill of remaining PIDs: ${pids[*]}" >> "$LOG_FILE"
      for pid in "${pids[@]}"; do
        kill -9 "$pid" || true
      done
    fi
  else
    echo "No existing Jupyter notebook for $NOTEBOOK found." >> "$LOG_FILE"
  fi
}

# Ensure log file exists and is appendable
touch "$LOG_FILE"

# Cleanup any existing instance
cleanup_existing

# Start fresh notebook
echo "Starting new Jupyter notebook for $NOTEBOOK" >> "$LOG_FILE"
setsid jupyter notebook --no-browser \
  --NotebookApp.token='' \
  --NotebookApp.password='' \
  "$NOTEBOOK" \
  > "$LOG_FILE" 2>&1 < /dev/null &
pid=$!
echo "Jupyter notebook server - PID: $pid"
echo "Log location: $LOG_FILE"
echo "Jupyter notebook server - PID: $pid" >> "$LOG_FILE"

