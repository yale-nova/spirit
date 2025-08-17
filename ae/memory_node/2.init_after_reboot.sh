#/bin/bash
export SPIRIT_PATH="/opt/spirit"
# remove unnecessary kernel files
cd $SPIRIT_PATH/linux-6.13
sudo make clean

cd $SPIRIT_PATH/spirit-controller/remote_mem/daemons/network/rdma/

# Install required packages and run the rdma model
sudo apt update
sudo apt install librdmacm-dev libibverbs-dev -y
make

# Run the server as a background process
set -euo pipefail

SERVER_CMD_PATTERN='make run_server'
LOCKFILE=$SPIRIT_PATH/spirit-controller/ae/memory_node/remote_memory_server.lock

# Acquire serialized access so two concurrent invocations don't race
exec 9>"$LOCKFILE" || { echo "cannot open lockfile" >&2; exit 1; }
if ! flock -n 9; then
  echo "Another invocation is already checking/starting the server; continuing." >&2
fi

# Helper: is there already a live server?
is_running() {
  if pgrep -f "$SERVER_CMD_PATTERN" > /dev/null; then
    return 0
  fi
  return 1
}

if is_running; then
  echo "Remote memory server already running; skipping start."
else
  echo "Starting remote memory server..."
  setsid make run_server > server.log 2>&1 < /dev/null &
  pid=$!
  echo "Remote memory server - PID: $pid"
  disown
fi

# Ansible setup
cd $SPIRIT_PATH/spirit-controller/scripts/disagg
just setup_ansible

# Setup pthon and jupytor notebook
sudo apt update
sudo apt install python3-venv libzstd-dev -y
cd $SPIRIT_PATH/spirit-controller
python3 -m venv myenv
source myenv/bin/activate
cd $SPIRIT_PATH/spirit-controller/res_allocation/
pip install -r requirements.txt
pip install -U pip jupyter bash_kernel
python -m bash_kernel.install

# Pre-build global enforcer
cd $SPIRIT_PATH/spirit-controller
# For the initial setup for trace_loader
cargo build
# Actual buliding step with optimizations
cargo build --release

# Jupytor notebook
cd $SPIRIT_PATH/spirit-controller/ae/memory_node/
./3.run_notebook.sh
