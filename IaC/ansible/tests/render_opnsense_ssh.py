"""Confere o SSH do firewall no render e o no_log das tasks que gravam segredo.

CAMADA 1 — `<system><ssh>` sem `permitrootlogin` e sem `passwordauth`.
  O gerador do OPNsense (plugins.inc.d/openssh.inc) testa as duas chaves por
  isset(): PRESENTE liga, qualquer que seja o valor. O template gravava `0`
  achando que desligava, e o sshd vivo rodou com PermitRootLogin yes e
  PasswordAuthentication yes desde a reinstalação até 15/10/2026. Junto:
  `enabled` presente, `noauto` presente e só a LAN em `interfaces`.

CAMADA 2 — `no_log: true` nas tasks que escrevem segredo em texto puro.
  O config.xml inteiro (hashes, chave privada da GUI, PPPoE) no
  01-opnsense-setup.yml, e o /etc/config/wireless com as PSKs no
  02-openwrt-setup.yml. Tirar a linha publica o segredo no log do Ansible.
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
falhas = []


def checar(ok, msg):
    print(f"  {'OK' if ok else 'FALHOU'} {msg}")
    if not ok:
        falhas.append(msg)


with tempfile.TemporaryDirectory() as tmp:
    saida = Path(tmp) / "config.xml"
    r = subprocess.run(
        [sys.executable, str(RAIZ / "tests" / "render_opnsense.py"),
         "--template", str(RAIZ / "templates/network/opnsense/config.xml.j2"),
         "--common", str(RAIZ / "inventories/_common/opnsense.yml"),
         "--fixture", str(RAIZ / "tests/fixtures/opnsense.yml"),
         "--out", str(saida)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"✗ render falhou: {r.stderr.strip()}")
    ssh = ET.parse(saida).getroot().find("./system/ssh")

checar(ssh is not None, "system.ssh existe")
if ssh is not None:
    for flag in ("permitrootlogin", "passwordauth"):
        checar(ssh.find(flag) is None,
               f"system.ssh sem <{flag}> (presença liga, isset no openssh.inc)")
    checar(ssh.find("enabled") is not None, "system.ssh.enabled presente")
    checar(ssh.find("noauto") is not None, "system.ssh.noauto presente")
    checar((ssh.findtext("interfaces") or "").strip() == "lan",
           "system.ssh.interfaces só na LAN")


def task(playbook, nome):
    for play in yaml.safe_load((RAIZ / playbook).read_text()):
        for t in play.get("tasks", []) + play.get("pre_tasks", []):
            if t.get("name", "").startswith(nome):
                return t
    return None


for playbook, nome in (
    ("playbooks/network/01-opnsense-setup.yml", "Aplicar novo arquivo /conf/config.xml"),
    ("playbooks/network/02-openwrt-setup.yml", "Aplicar configuração de Wi-Fi e SSIDs Segregados"),
):
    t = task(playbook, nome)
    checar(t is not None, f"{playbook}: task '{nome}' existe")
    checar(t is not None and t.get("no_log") is True, f"{playbook}: '{nome}' com no_log: true")

print()
if falhas:
    sys.exit(f"✗ render-opnsense-ssh: {len(falhas)} falha(s)")
print("  TODOS OS GATES DO SSH DO FIREWALL PASSARAM")
