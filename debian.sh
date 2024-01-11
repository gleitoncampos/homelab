echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/backports.list
apt install -y curl git wget vim htop httpie lm-sensors 
apt install -t bookworm-backports cockpit
curl -flSL https://get.docker.io | sh
nmcli con mod enp1s0 ipv4.address "192.168.100.10/24" ipv4.gateway "192.168.100.1" ipv4.dns "192.168.100.1"
systemctl restart NetworkManager
