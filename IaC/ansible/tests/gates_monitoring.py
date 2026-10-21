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

# Updates do OpenWrt no dashboard, como o apt_upgrades_pending dos Proxmox
# (14/10/2026): script -> textfile -> painéis. Uma ponta sem a outra é painel
# vazio sem erro nenhum.
import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402

script = ANSIBLE / "templates/network/openwrt_update_metrics.sh.j2"
roda = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
checar(roda.returncode == 0, f"openwrt_update_metrics.sh.j2 passa no `sh -n` {roda.stderr.strip()}")
metricas_script = set(re.findall(r"^(openwrt_[a-z_]+)", script.read_text(encoding="utf-8"), re.M))
pacotes = yaml.safe_load((ANSIBLE / "inventories/prod/group_vars/network/10-defaults.yml")
                         .read_text(encoding="utf-8"))["openwrt_exporter_packages"]
checar("prometheus-node-exporter-lua-textfile" in pacotes, "o coletor textfile está nos pacotes do exportador")
checar("openwrt_update_metrics.sh.j2" in exp and "* * * * /usr/bin/openwrt-update-metrics" in exp,
       "02-openwrt-exporter instala o script e o agenda no cron")
# O exportador só enxerga coletor instalado antes da partida (14/10/2026).
_cura = [t for t in yaml.safe_load(exp)[0]["tasks"]
         if "^openwrt_firmware_update_level" in str(t.get("ansible.builtin.raw", ""))
         and "prometheus-node-exporter-lua restart" in str(t.get("ansible.builtin.raw", ""))]
checar(len(_cura) == 1, "02-openwrt-exporter reinicia o exportador se o /metrics não traz o textfile")
# Redes vizinhas (20/10/2026): script -> textfile -> painel, de hora em hora
# por banda. Scan a cada 15 min dava soluço de 1,5 s nos clientes do 5 GHz.
checar("openwrt_wifi_neighbors.uc.j2" in exp, "02-openwrt-exporter instala o script de redes vizinhas")
_cron_viz = re.findall(r'"(\S+) (\S+) \* \* \* /usr/bin/openwrt-wifi-neighbors (\S+) (\S+)"', exp)
checar(sorted(b for *_, b in _cron_viz) == ["2g", "5g"], f"um cron de redes vizinhas por banda ({_cron_viz})")
checar(all(h == "*" and m.isdigit() and m != "17" for m, h, *_ in _cron_viz),
       "redes vizinhas de hora em hora, fora do minuto do update-metrics")
painel = json.loads((REPO / "docker/monitoring/swarm/dashboards/openwrt.json").read_text(encoding="utf-8"))
exprs = " ".join(t.get("expr", "") for p in painel["panels"] for t in p.get("targets") or [])
usadas = set(re.findall(r"\bopenwrt_[a-z_]+", exprs))
checar({"openwrt_firmware_update_level", "openwrt_packages_upgradable"} <= usadas,
       "dashboard do OpenWrt mostra firmware e pacotes pendentes")
checar(usadas <= metricas_script,
       f"toda métrica openwrt_* do dashboard sai do script ({sorted(usadas - metricas_script)})")

if falhas:
    sys.exit(f"\n✗ gates-monitoring: {len(falhas)} falha(s)")
print("\n✓ gates-monitoring")
