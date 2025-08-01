#!/usr/bin/env bash
set -euo pipefail

TARGET="/mnt/spirit_data/dlrm_bench"
URL="https://go.criteo.net/criteo-research-kaggle-display-advertising-challenge-dataset.tar.gz"
ARCHIVE="dataset.tar.gz"

# 1. Prepare host directory
sudo mkdir -p "$TARGET"
sudo chown "$(id -u):$(id -g)" "$TARGET"

# 2. Ensure directory is empty (allow retries)
while true; do
  if [ -z "$(ls -A "$TARGET")" ]; then
    echo "✔ $TARGET is empty—starting download..."
    break
  else
    echo "✖ $TARGET is not empty!"
    read -p "Clean it up and press [Enter] to retry, or Ctrl+C to abort." _
  fi
done

# 3. Download with progress bar
echo "↓ Downloading dataset..."
wget -q --show-progress "$URL" -O "$ARCHIVE"

# 4. Extract with optional progress
if command -v pv &> /dev/null; then
  echo "⇅ Extracting with pv..."
  pv "$ARCHIVE" | tar --warning=no-unknown-keyword -xz -C "$TARGET"
else
  echo "⇅ Extracting..."
  tar --warning=no-unknown-keyword -xzf "$ARCHIVE" -C "$TARGET"
fi

# 5. Clean up archive
rm -f "$ARCHIVE"

# 6. Verify
echo "✔ Contents of $TARGET:"
ls -l "$TARGET" | head