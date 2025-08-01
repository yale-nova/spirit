CC=gcc
CFLAGS=-lrdmacm -libverbs -lpthread -O2 -g
COMMON_FILES=rdma_common.c rdma_server.c
FILES=clean_rdma_server
SERVER_IP := $(shell ifconfig | grep -Eo 'inet (10\.10\.10\.[0-9]+)' | awk '{print $$2}' | head -n 1)

all: clean_rdma_server

clean_rdma_server: clean_rdma_server.c $(COMMON_FILES)
	$(CC) $^ $(CFLAGS) -o $@

# Note) server needs to be run as root to register memory as RDMA-able
run_server: clean_rdma_server setup_huge_pages
	sudo ./clean_rdma_server ${SERVER_IP}

setup_huge_pages:
	echo 48 | sudo tee /proc/sys/vm/nr_hugepages

# for shared library
CFLAGS += -fPIC
rdma_common.o: rdma_common.c
	$(CC) -c rdma_common.c $(CFLAGS) -o rdma_common.o

clean:
	rm -f $(FILES) *.o

.PHONY: all clean
