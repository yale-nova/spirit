#!/bin/bash

sudo apt-get install -y curl git libssl-dev pkg-config build-essential default-jre libfontconfig-dev wget unzip libglib2.0-dev

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

# Add Rust to PATH (may not be necessary, but can be useful)
source $HOME/.cargo/env

# Install Just
cargo install just
sudo apt update && sudo apt install byobu htop python3-pip -y
