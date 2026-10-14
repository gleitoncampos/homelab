#!/usr/bin/env python3
"""Confere o upgrade do AP (02-openwrt-upgrade.yml) e a preservação do sysupgrade.

O que cada gate impede (14/10/2026):
- arquivo que o IaC grava no AP fora de /etc/config e que some no próximo
  sysupgrade (crontab, led-health e scripts sumiram nos dois saltos de versão);
- gravar imagem antes de conferir a preservação, do backup e do download
  verificado;
- install síncrono: o reboot derruba a sessão que o disparou.
"""
import re
import sys
from pathlib import Path

import yaml

ANSIBLE = Path(__file__).resolve().parents[1]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def tarefas(pb):
    return yaml.safe_load((ANSIBLE / "playbooks/network" / pb).read_text(encoding="utf-8"))[0]["tasks"]


for env in ("prod", "dev"):
    keep = yaml.safe_load((ANSIBLE / f"inventories/{env}/group_vars/network/10-defaults.yml")
                          .read_text(encoding="utf-8")).get("openwrt_sysupgrade_keep") or []
    # Todo destino de `cat << ... > /usr/bin|/etc/init.d/...`, e o binário do
    # xinfc (scp), precisa estar na lista: senão some no próximo upgrade.
    gravados = set()
    for pb in ("02-openwrt-setup.yml", "02-openwrt-exporter.yml", "02-openwrt-nfc.yml"):
        texto = (ANSIBLE / "playbooks/network" / pb).read_text(encoding="utf-8")
        gravados |= set(re.findall(r"> (/usr/bin/[\w.-]+|/etc/init\.d/[\w.-]+)", texto))
        gravados |= set(re.findall(r":(/usr/bin/[\w.-]+)\"", texto))
    gravados |= {"/usr/bin/led-health"}  # o setup grava por variável (openwrt_led_health_path)
    checar(len(gravados) >= 5, f"{env}: a varredura achou os arquivos gravados ({len(gravados)}: {sorted(gravados)})")
    faltam = sorted(g for g in gravados if g not in keep)
    checar(not faltam, f"{env}: tudo que o IaC grava em /usr/bin e /etc/init.d está em openwrt_sysupgrade_keep {faltam}")
    for item in ("/etc/crontabs/root", "/etc/rc.d/S97led-health", "/root/xinfc/"):
        checar(item in keep, f"{env}: {item} é preservado")

setup = tarefas("02-openwrt-setup.yml")
pres = [t for t in setup if "IAC-INICIO" in str(t.get("ansible.builtin.raw", ""))]
checar(len(pres) == 1, "o setup grava o bloco IAC no /etc/sysupgrade.conf")
if pres:
    r = pres[0]["ansible.builtin.raw"]
    checar("openwrt_sysupgrade_keep" in r and "IAC-FIM" in r and "awk" in r,
           "o bloco é reescrito inteiro a partir de openwrt_sysupgrade_keep")
    checar(not pres[0].get("tags"), "a preservação roda em toda execução completa do setup")

up = tarefas("02-openwrt-upgrade.yml")


def idx(trecho):
    return next((i for i, t in enumerate(up) if trecho in str(t)), None)


ordem = {
    "check": idx("owut check {{ _owut_opts }} 2>&1; echo"),
    "preserva": idx("sysupgrade -l"),
    "backup": idx("sysupgrade -b"),
    "download": idx("owut download"),
    "install": idx("owut install"),
    "cai": idx("'state': 'stopped'"),
    "volta": idx("'state': 'started'"),
    "confere": idx("RADIOS:"),
}
checar(None not in ordem.values(), f"o upgrade tem todas as etapas {sorted(k for k, v in ordem.items() if v is None)}")
if None not in ordem.values():
    seq = [ordem[k] for k in ("check", "preserva", "backup", "download", "install", "cai", "volta", "confere")]
    checar(seq == sorted(seq), "ordem: check → preservação → backup → download → install → cai → volta → confere")
    inst = str(up[ordem["install"]]["ansible.builtin.raw"])
    checar("start-stop-daemon -S -b" in inst, "o install roda destacado da sessão SSH")
    checar(" -m -p " in inst, "o install usa pidfile (sem ele o start-stop-daemon acha o led-health e não dispara)")
    checar("INSTALL:DISPARADO" in str(up[ordem["install"]].get("failed_when", "")),
           "o disparo do install é conferido, não ignorado")
    play = yaml.safe_load((ANSIBLE / "playbooks/network/02-openwrt-upgrade.yml").read_text(encoding="utf-8"))[0]
    checar(str(play["vars"]["_bkp_dir"]).startswith("{{ playbook_dir }}/../../../../.local/"),
           "o backup vai para .local/ da raiz do repositório (4 níveis acima)")
    checar(any("chmod" in str(t) and "0600" in str(t) for t in up), "o backup local fica 0600")
    checar("'OWUT:rc=0' not in" in str(up[ordem["download"]].get("failed_when", "")),
           "o download falha se o owut não sair com 0")
    conf = str(up[ordem["confere"]].get("failed_when", ""))
    checar(all(k in conf for k in ("RADIOS:true true", "LED:OK", "CRON:OK")),
           "depois do upgrade: rádios, led-health e cron conferidos")
    checar("[/]usr/bin/led-health" in str(up[ordem["confere"]]["ansible.builtin.raw"]),
           "a conferência do led-health não casa a própria linha de comando")

if falhas:
    sys.exit(f"\n✗ gates-openwrt-upgrade: {len(falhas)} falha(s)")
print("\n✓ gates-openwrt-upgrade")
