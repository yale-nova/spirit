## NOTE:: This dockerfile should be run from the parent directory:
# => docker build -t dynamic-docker -f dynamic_docker/Dockerfile .

# Step 1: Choose a base image
FROM ubuntu:20.04
# FROM memcached:latest

# Avoid prompts during package installation
ARG DEBIAN_FRONTEND=noninteractive

WORKDIR /dynamic_docker
COPY ./dynamic_docker .
RUN cp /dynamic_docker/run_commands.sh /usr/local/bin/run_commands.sh

# Update and install Memcached
RUN apt-get update && \
    apt-get install -y memcached

# Expose default Memcached port
EXPOSE 11211

# Stream bench setup
# Install dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    gfortran \
    make \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Compile the application using the Makefile
WORKDIR /stream_bench
COPY ./stream_bench .
RUN make clean
RUN make all
# Print the current working directory
RUN echo "Current Directory:" && pwd 1>&2
RUN cp /stream_bench/stream /usr/local/bin/stream

# Make the script executable
RUN chmod +x /usr/local/bin/run_commands.sh

# Set the working directory
WORKDIR /usr/local/bin

# Set the entrypoint to the custom script
ENTRYPOINT ["/usr/local/bin/run_commands.sh"]
