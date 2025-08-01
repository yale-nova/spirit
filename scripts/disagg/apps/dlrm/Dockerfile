# Use the official Ubuntu base image
FROM ubuntu:20.04

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Update and install dependencies
RUN apt-get update && apt-get install -y \
    git \
    python3 \
    python3-pip \
    build-essential \
    wget \
    libssl-dev \
    libffi-dev \
    tar \
    python3-dev

# Upgrade pip and install Python packages
RUN pip3 install --upgrade pip
RUN pip3 install numpy scipy scikit-learn torch==1.9.0 tensorboard

# Clone the DLRM repository
RUN git clone https://github.com/facebookresearch/dlrm.git /opt/dlrm
RUN cd /opt/dlrm && git checkout ddd0fdc && pip install -r requirements.txt

# Apply the patch to data_utils.py
# COPY data_utils.patch /opt/dlrm/data_utils.patch
# RUN cd /opt/dlrm && patch data_utils.py data_utils.patch
COPY dlrm_s_pytorch.patch /opt/dlrm/dlrm_s_pytorch.patch
RUN cd /opt/dlrm && patch dlrm_s_pytorch.py dlrm_s_pytorch.patch

# Copy run_train.sh and run_inf.sh
COPY run_train.sh /opt/dlrm
COPY run_inf.sh /opt/dlrm

# Set the working directory
WORKDIR /opt/dlrm
