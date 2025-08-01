#/bin/bash
echo "Creating partition and formatting...press <y> and then <enter> as requested on the screen"

sudo parted -s --align optimal /dev/sda mkpart primary ext4 69.0GB 472GB
sudo mkfs.ext4 /dev/sda4
sudo mkdir -p /mnt/spirit_data
sudo mount /dev/sda4 /mnt/spirit_data

# Python venv and dependencies
sudo apt update
sudo apt install python3-venv libzstd-dev -y
export SPIRIT_PATH="/opt/spirit"
cd $SPIRIT_PATH/spirit-controller
python3 -m venv myenv
source myenv/bin/activate
cd $SPIRIT_PATH/spirit-controller/res_allocation/
pip install -r requirements.txt

# Perf setup
cd $SPIRIT_PATH/linux-6.13
sudo apt-get install libpfm4-dev libtraceevent-dev -y
make -C tools/perf
sudo mv /usr/bin/perf /usr/bin/perf_6.13
sudo cp tools/perf/perf /usr/bin/perf

# Install libs in /usr/local/lib
sudo cp -r $SPIRIT_PATH/spirit-controller/trace_loader/*.a /usr/local/lib/

# Application setup
# 1. Stream
cd $SPIRIT_PATH/spirit-controller/scripts/disagg/apps/stream_bench
./build_docker.sh

# 2. Memcached
# Build memcached and memcached client dockers
cd $SPIRIT_PATH/spirit-controller/
just benchmark-mc-client-build-docker

# Dataset
cd $SPIRIT_PATH && cd spirit-controller/scripts/disagg
# since the ftp server removed the dataset, we prepared it as cloudlab dataset
# ./download_metakvs.sh
sudo rm -rf /workload
DOWNLOAD_PATH="$SPIRIT_PATH/dataset"
sudo ln -s "$DOWNLOAD_PATH" /workload

# Memcached image
docker pull memcached@sha256:768b8b14b264b87cdde0d4bc0e800c332b8563ce19fd15ce98945c4441b98146
