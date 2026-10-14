#!/usr/bin/env python3
"""Confere as decisões de 13/10/2026 sobre a stack de monitoramento.

- InfluxDB fora: nenhum datasource do Grafana, nenhum dashboard e nada no
  repositório escrevia nele.
- Statping fora: o 03-stack-monitoring não implanta mais a página de status.
- prometheus-pve-exporter fixo no manager: a exceção do firewall que deixa a
  VLAN do homelab chegar à API do Proxmox vale só para o IP do manager.
- Exportador do AP escutando na interface de gerência: a `lan` deixou de
  existir na migração de VLANs, e ele ficou sem escutar porta nenhuma.
"""
import sys
from pathlib import Path

import yaml

ANSIBLE = Path(__file__).resolve().parents[1]
REPO = ANSIBLE.parents[1]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


compose = yaml.safe_load((REPO / "docker/monitoring/swarm/compose.yml").read_text(encoding="utf-8"))
servicos = compose.get("services") or {}
checar("influxdb" not in servicos, "compose de monitoring sem influxdb")
pve = servicos.get("prometheus-pve-exporter") or {}
restricoes = ((pve.get("deploy") or {}).get("placement") or {}).get("constraints") or []
checar("node.role == manager" in restricoes, "prometheus-pve-exporter fixo no manager")

# `on-failure` deixa a task `Complete` num restart limpo do dockerd e o Swarm
# não a recria (o Traefik ficou 0/1 assim, 13/10/2026).
for nome, sv in servicos.items():
    cond = ((sv.get("deploy") or {}).get("restart_policy") or {}).get("condition", "any")
    checar(cond == "any", f"monitoring/{nome}: restart_policy.condition = any (veio {cond})")

boot = (ANSIBLE / "playbooks/01-bootstrap-os.yml").read_text(encoding="utf-8")
checar("influxdb" not in boot, "01-bootstrap-os não cria diretório do influxdb")

mon = (ANSIBLE / "playbooks/03-stack-monitoring.yml").read_text(encoding="utf-8")
checar("statping" not in mon.lower(), "03-stack-monitoring não implanta o Statping")

exp = (ANSIBLE / "playbooks/network/02-openwrt-exporter.yml").read_text(encoding="utf-8")
checar("listen_interface" in exp and "openwrt_mgmt_vlan" in exp,
       "02-openwrt-exporter põe o exportador na interface de gerência")
checar("openwrt_mgmt_ip" in exp, "02-openwrt-exporter acha o AP no endereço de gerência")

# O AP rodou só com --tags rede/wifi: o /etc/config/system novo foi gravado,
# mas o restart do `log` (sem tag) ficou de fora e o logread seguiu mandando
# syslog para o servidor antigo (13/10/2026).
setup = yaml.safe_load((ANSIBLE / "playbooks/network/02-openwrt-setup.yml").read_text(encoding="utf-8"))[0]
ap = setup.get("tasks") or []
grava = [t for t in ap if "/etc/config/system" in str(t.get("ansible.builtin.raw", "")) and "cat <<" in str(t.get("ansible.builtin.raw", ""))]
reinicia = [t for t in ap if "/etc/init.d/log restart" in str(t.get("ansible.builtin.raw", ""))]
checar(len(grava) == 1 and len(reinicia) == 1, "02-openwrt-setup: uma task grava o system e uma reinicia o log")


def tags(t):
    v = t.get("tags") or []
    return {v} if isinstance(v, str) else set(v)


if grava and reinicia:
    tg = tags(grava[0])
    checar(bool(tg) and tg <= tags(reinicia[0]),
           f"o restart do log roda com as tags de quem grava o system ({sorted(tg)})")

if falhas:
    sys.exit(f"\n✗ gates-monitoring: {len(falhas)} falha(s)")
print("\n✓ gates-monitoring")
