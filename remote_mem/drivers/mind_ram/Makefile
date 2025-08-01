# Specify the modules to build
obj-m += mind_ram_rdma.o
# CC=x86_64-linux-gnu-gcc-13
CC=gcc

# Define the object files for each module
mind_ram_rdma-objs := mind_ram_drv_rdma.o mind_ram_rdma_worker.o mind_ram_rdma_ftns.o

# Define the kernel build directory
KERNEL_SRC := /opt/spirit/linux-6.13

PWD := $(shell pwd)

# Build the modules
all:
	$(MAKE) -C $(KERNEL_SRC) M=$(PWD) modules CC=$(CC)

# Clean the build artifacts
clean:
	$(MAKE) -C $(KERNEL_SRC) M=$(PWD) clean
