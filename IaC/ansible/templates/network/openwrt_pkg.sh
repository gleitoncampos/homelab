# shellcheck shell=sh
# Gerenciador de pacotes do OpenWrt: opkg (ate a 24.10) ou apk (25.12+).
# Incluido no inicio dos `raw` que instalam pacote (lookup('file')), para que
# os playbooks nao conhecam o gerenciador.
#
# ⚠️ Instalar UM pacote por vez e imprimir o rc, nunca devolve-lo: o `apk add`
# e atomico (um pacote ausente derruba a transacao inteira), e o opkg sai com
# 255 quando nao acha o pacote — o Ansible le 255 de um `raw` como queda de
# SSH (14/10/2026). Medido no apk-tools 3.0.5 (25.12.5): `info -e` da 0/1 e
# `add` de pacote inexistente da 1.
if command -v apk >/dev/null 2>&1; then
  pkg_tem() { apk info -e "$1" >/dev/null 2>&1; }
  pkg_atualiza_listas() { apk update >/dev/null 2>&1; }
  pkg_instala() { apk add "$1"; }
else
  pkg_tem() { opkg list-installed 2>/dev/null | grep -q "^$1 "; }
  pkg_atualiza_listas() { opkg update >/dev/null 2>&1; }
  pkg_instala() { opkg install "$1"; }
fi
