FROM rust:latest

# Set environment variables to make the build non-interactive
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y software-properties-common libglib2.0-dev

# Install just for later use, to run global enforcer
RUN cargo install just
RUN apt-get install -y libfontconfig1-dev

WORKDIR /

CMD ["./bench-mc-client", "-c", "/configs/local_meta.json"]
