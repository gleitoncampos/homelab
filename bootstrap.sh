#!/bin/bash
set -eo pipefail

#### Bootstrap Debian 12/13 Fresh Install ####

echo "==> Atualizando sistema..."
sudo apt update && sudo apt upgrade -y

echo "==> Instalando Docker CE..."
sudo apt install ca-certificates curl gnupg -y
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin -y
sudo systemctl enable --now docker

echo "==> Instalando Syncthing..."
sudo curl -o /usr/share/keyrings/syncthing-archive-keyring.gpg https://syncthing.net/release-key.gpg
echo "deb [signed-by=/usr/share/keyrings/syncthing-archive-keyring.gpg] https://apt.syncthing.net/ syncthing stable" | sudo tee /etc/apt/sources.list.d/syncthing.list
sudo apt update
sudo apt install syncthing apt-transport-https -y
sudo systemctl enable --now syncthing@root
sleep 5

if [ -f ~/.local/state/syncthing/config.xml ]; then
    sed -i 's/127.0.0.1/0.0.0.0/g' ~/.local/state/syncthing/config.xml
    sudo systemctl restart syncthing@root.service
fi

echo "==> Instalando Rsyslog..."
sudo apt install rsyslog -y
sudo systemctl enable --now rsyslog

echo "==> Bootstrap concluído com sucesso!"
