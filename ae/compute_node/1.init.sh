#/bin/bash

# Docker
export SPIRIT_PATH="/opt/spirit"
cd $SPIRIT_PATH/spirit-controller
git pull origin main
git submodule init
git submodule update

# cargo and others
./install.sh
./install_docker.sh || true
sudo systemctl start docker

# PEBS
if ! grep -q "intel_pmu" /etc/default/grub; then
  # Append intel_pmu=on
  sudo sed -i '/^GRUB_CMDLINE_LINUX_DEFAULT=/ s/"$/ intel_pmu=on"/' /etc/default/grub
  echo "intel_pmu=on added to GRUB_CMDLINE_LINUX_DEFAULT."
else
  echo "intel_pmu already present; no change made."
fi

sudo update-grub
sudo reboot
