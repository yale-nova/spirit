# Step 1: Choose a base image
FROM ubuntu:20.04

# Avoid prompts during package installation
ARG DEBIAN_FRONTEND=noninteractive

# Step 2: Install dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    gfortran \
    make \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Step 3: Set the working directory (optional)
WORKDIR /app

# Step 4: Copy the application source code and Makefile into the image
COPY . /app

# Step 5: Compile the application using the Makefile
RUN make all

# Step 6: Specify the runtime command
# This line is optional and could be adjusted based on how you want to use the container
CMD ["./stream"]
