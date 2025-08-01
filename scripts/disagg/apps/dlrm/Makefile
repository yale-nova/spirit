.PHONY: all run

all: build
	./prepare_dataset.sh

build:
	sudo docker build -t dlrm_cpu .

run:
	sudo docker run --cpus="8" -v /mnt/spirit_data/dlrm_bench:/root/dlrm_bench -p 8001:8001 --rm --name spirit_dlrm_inf_test -it dlrm_cpu ./run_inf.sh

train:
	sudo docker run --cpus="8" -v /mnt/spirit_data/dlrm_bench:/root/dlrm_bench -p 8010:8001 --rm --name spirit_dlrm_train -it dlrm_cpu ./run_train.sh
