FROM rust:latest

# Set environment variables to make the build non-interactive
ENV DEBIAN_FRONTEND=noninteractive

# Update and install prerequisites
RUN apt-get update && \
    apt-get install -y software-properties-common gnupg2 curl

# Update and install Python and other necessary packages
RUN apt-get update && \
    apt-get install -y python3 python3-venv python3-pip ansible

# Install rsync
RUN apt-get install -y rsync
# RUN apt-get install -y software-properties-common gnupg2 && \
#     # echo "deb http://ppa.launchpad.net/ansible/ansible/ubuntu trusty main" >> /etc/apt/sources.list && \
#     # apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 93C4A3FD7BB9C367 && \
#     apt-get update && \
#     apt-get install -y ansible

# For rocksdb
# RUN apt-get install clang libclang-dev libsnappy-dev liblz4-dev libzstd-dev -y
# Install just for later use, to run global enforcer
RUN cargo install just@1.39.0
RUN apt-get install -y libfontconfig1-dev

# Install python packages for resource allocation
RUN apt-get install -y python3-pip python3-venv
RUN python3 -m venv /opt/venv
# Activate the virtual environment
RUN /bin/bash -c "source /opt/venv/bin/activate && pip install requests scikit-learn numpy pandas redis argparse joblib matplotlib tqdm plotly pygam"

WORKDIR /ansible

CMD [ "ansible-playbook", "--version" ]
