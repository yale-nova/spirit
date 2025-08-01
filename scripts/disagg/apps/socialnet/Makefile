.PHONY: build run enforce_mem restart_jaeger

# Variables
DOCKER_IMAGE=init_social_graph_rs:latest
CONTAINER_NAME ?= spirit_socialnet_1_client
SOCIAL_NETWORK_DIR=/home/sslee/DeathStarBench/socialNetwork
# Default memory limit in MB
MEMORY_LIMIT_MB ?= 5120

# MongoDB variables
MONGO_CONTAINER_NAME = socialnetwork-user-mongodb-1
MONGO_DB = user
MONGO_COLLECTION = user
MONGO_INDEX = username_1

# Build the Docker image
build:
	docker build --build-arg CACHE_BUST=$$(date +%s) -t $(DOCKER_IMAGE) .

# Run the Docker container
run:
	docker run --rm --network host --name $(CONTAINER_NAME) -d $(DOCKER_IMAGE) --graph soc-twitter-follows-mun --timeline-only --num-requests=-1 --limit=8 --metric-port=8009 --theta=1.15 --timeline-start=5 --timeline-stop=5

# Shutdown the Docker container
stop:
	docker rm -f $(CONTAINER_NAME) || true

# Enforce memory limit
enforce_mem:
	echo $(MEMORY_LIMIT_MB)M | sudo tee /sys/fs/cgroup/spirit.slice/spirit-snet.slice/memory.max

# Restart Jaeger agent
restart_jaeger:
	cd $(SOCIAL_NETWORK_DIR) && docker-compose restart jaeger-agent

# Start the entire social network service
start_service:
	cd $(SOCIAL_NETWORK_DIR) && docker-compose up -d
	sleep 3
	@echo "Creating index on '${MONGO_COLLECTION}.username' in database '${MONGO_DB}'..."
	docker exec -i $(MONGO_CONTAINER_NAME) mongo --eval "db.getSiblingDB('$(MONGO_DB)').$(MONGO_COLLECTION).createIndex({ username: 1 }, { name: '$(MONGO_INDEX)', background: true })"
	@echo "Index '${MONGO_INDEX}' created (if it did not already exist)."
	@echo "Allocate CPUs"
	echo "36-47" | sudo tee /sys/fs/cgroup/spirit.slice/spirit-snet.slice/cpuset.cpus

# Shutdown the entire social network service
shutdown_service: stop
	cd $(SOCIAL_NETWORK_DIR) && docker-compose down

shutdown_service_flush: stop
	cd $(SOCIAL_NETWORK_DIR) && docker-compose down --volumes

# Preload graph and timeline
preload:
	cd $(SOCIAL_NETWORK_DIR)/scripts/init_social_graph_rs/ && cargo run --release -- --graph soc-twitter-follows-mun --compose --num-requests=10 --print-every=1000 --text-length=512

preload_compose:
	cd $(SOCIAL_NETWORK_DIR)/scripts/init_social_graph_rs/ && cargo run --release -- --graph soc-twitter-follows-mun --compose-only --num-requests=10 --print-every=1000 --text-length=512

preload_docker:
	docker run --rm --network host --name $(CONTAINER_NAME) -d $(DOCKER_IMAGE) --graph soc-twitter-follows-mun --compose --num-requests=10 --print-every=1000 --text-length=512

timeline:
	cd $(SOCIAL_NETWORK_DIR)/scripts/init_social_graph_rs/ && cargo run --release -- --graph soc-twitter-follows-mun --timeline-only --num-requests=-1 --limit=4 --metric-port=8009
