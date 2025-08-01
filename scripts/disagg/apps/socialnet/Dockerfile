# Stage 1: Build the Rust application
FROM rust:latest AS builder

# Install Git and OpenSSL (for libssl.so.3)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    libssl-dev \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Set environment variables for repository details
ENV REPO_URL=https://github.com/shsym/DeathStarBench.git
ENV BRANCH=dev/social-init-opt

# Clone the specified branch of the repository
# Disable cache to get the up-to-date commit of the branch
ARG CACHE_BUST=1
RUN git clone --branch $BRANCH --single-branch $REPO_URL /DeathStarBench

# Set the working directory to the Rust project
WORKDIR /DeathStarBench/socialNetwork/scripts/init_social_graph_rs

# Build the project in release mode
RUN cargo build --release

# Stage 2: Runtime stage (reuse builder container)
FROM rust:latest

# Install necessary runtime dependencies (OpenSSL and others are already available)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Create the appropriate folder structure
RUN mkdir -p /app/socialNetwork/scripts/init_social_graph_rs && \
    mkdir -p /datasets/social-graph

# Copy the compiled binary from the builder stage
COPY --from=builder /DeathStarBench/socialNetwork/scripts/init_social_graph_rs/target/release/init_social_graph_rs /app/socialNetwork/scripts/init_social_graph_rs/init_social_graph_rs

# Copy the dataset directories from the builder stage
COPY --from=builder /DeathStarBench/socialNetwork/datasets/social-graph /app/socialNetwork/datasets/social-graph

# Set the working directory to where the Rust binary expects relative paths to work
WORKDIR /app/socialNetwork/scripts/init_social_graph_rs

# Ensure the binary has execute permissions
RUN chmod +x /app/socialNetwork/scripts/init_social_graph_rs/init_social_graph_rs

# Set the binary as the entrypoint
ENTRYPOINT ["/app/socialNetwork/scripts/init_social_graph_rs/init_social_graph_rs"]
