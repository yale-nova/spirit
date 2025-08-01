CC = gcc
CFLAGS = -O2 -fopenmp -mcmodel=large

FC = gfortran
FFLAGS = -O2 -fopenmp

all: stream_inf

# stream_f.exe: stream.f mysecond.o
# 	$(CC) $(CFLAGS) -c mysecond.c
# 	$(FC) $(FFLAGS) -c stream.f
# 	$(FC) $(FFLAGS) stream.o mysecond.o -o stream_f.exe

# 700 millon -> ~12 GB
# as it can be slow on remote memory, output throughput every 2 iterations
stream_inf: stream.c
	$(CC) $(CFLAGS) -DINFINITE_RUN_MODE -DSTREAM_ARRAY_SIZE=700000000 -DNTIMES=2 stream.c -o stream

clean:
	rm -f stream *.o

# an example of a more complex build line for the Intel icc compiler
stream.icc: stream.c
	icc -O3 -xCORE-AVX2 -ffreestanding -qopenmp -DSTREAM_ARRAY_SIZE=100000000 -DNTIMES=20 stream.c -o stream.omp.AVX2.80M.20x.icc
