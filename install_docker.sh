#!/bin/bash

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

echo "Wait for 10 seconds..."
sleep 10

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep -oP '"tag_name": "\K(.*)(?=")')/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# post docker
if ! getent group docker > /dev/null 2>&1; then
  echo "Creating 'docker' group and adding user '$USER'…"
  sudo groupadd docker
  sudo usermod -aG docker "$USER"
else
  echo "Group 'docker' already exists. No action needed."
fi

# Start Docker service
echo "Wait for 10 seconds..."
sleep 10

MAX_ATTEMPTS=3
SLEEP_INTERVAL=10

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  if sudo docker ps >/dev/null 2>&1; then
    echo "Docker is up (on attempt $attempt)."
    break
  fi

  echo "Attempt $attempt/$MAX_ATTEMPTS: Docker not responding."
  echo "  → Starting docker.service..."
  sudo systemctl start docker

  # Only sleep if we're going to retry
  if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
    echo "  → Waiting $SLEEP_INTERVAL seconds before retry..."
    sleep "$SLEEP_INTERVAL"
  fi

  attempt=$(( attempt + 1 ))
done

# Final check
if ! docker ps >/dev/null 2>&1; then
  echo "Error: Docker still unavailable after $MAX_ATTEMPTS attempts." >&2
  exit 1
fi

# Show containers
docker ps
