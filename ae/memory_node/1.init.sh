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

# Hugepages
sudo sed -i '/^GRUB_CMDLINE_LINUX_DEFAULT=/ s/"$/ default_hugepagesz=1G hugepagesz=1G hugepages=48"/' /etc/default/grub
sudo update-grub
sudo reboot
