# justfile

global-build:
    cargo build --manifest-path global-enforcer/Cargo.toml

global-test:
    cargo test --manifest-path global-enforcer/Cargo.toml

global-run PORT CONFIG_FILE:
    ROCKET_PORT={{PORT}} cargo run --release --manifest-path global-enforcer/Cargo.toml -- {{CONFIG_FILE}}

global-run-docker CONFIG_FILE:
    sudo docker run -v $(pwd):/spirit-controller -v $(pwd)/{{CONFIG_FILE}}:/config.json -p 8001:8000 --name spirit-global-alloc -d rust:latest /bin/bash -c "tail -f /dev/null"

local-disagg-run CONFIG_FILE:
    cargo build --release --manifest-path local-enforcer/Cargo.toml
    sudo ./target/release/local-enforcer {{CONFIG_FILE}}

local-disagg-test-config:
    curl -X POST -H "Content-Type: application/json" -d '{"allocation_map": {"1": [512, 1000]}}' http://localhost:8000/config

benchmark-mc-client-build-docker:
    cargo build --release --manifest-path bench-mc-client/Cargo.toml
    sudo docker build -t bench-mc-client-docker -f bench-mc-client/Dockerfile .

benchmark-mc-client-run CONFIG_FILE:
    docker run -it --rm -v "$(pwd)"/target/release/bench-mc-client:/bench-mc-client:ro -v /workload:/workload:ro -v "$(pwd)"/benchmarks/ycsb:/ycsb:ro -v "$(pwd)/{{CONFIG_FILE}}":/configs:ro --network host --name "spirit_bench_mc_client_test" bench-mc-client-docker

fmt:
    cargo fmt

lint:
    cargo clippy

clean:
    cargo clean
