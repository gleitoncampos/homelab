#!/usr/bin/env python3
"""Gate do endurecimento dos Proxmox (posições 22 e 59 do ranking de segurança).

Confere no 00-proxmox-setup.yml e no proxmox_datacenter.j2:

  - unattended-upgrades com origem SÓ Debian-Security e sem reboot automático;
  - sysctl ptrace/protected_*/syncookies no 99-proxmox-tuning.conf;
  - export das configurações em 0700, tar com umask 077;
  - consent-text em base64 (a GUI decodifica com base64ToUtf8) e omitido vazio;
  - Fail2ban: o filtro da 8006 existe (sem ele o jail era descartado no boot,
    21/10/2026), casa a linha real do pvedaemon e extrai o IPv4, e os dois
    jails levam o ignoreip;
  - pam_pwquality com enforce_for_root e o dicionário do cracklib instalado.
"""
import re
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
PB_TXT = (RAIZ / "ansible" / "playbooks" / "00-proxmox-setup.yml").read_text(encoding="utf-8")
DC = (RAIZ / "ansible" / "templates" / "proxmox" / "proxmox_datacenter.j2").read_text(encoding="utf-8")
TASKS = [t for play in yaml.safe_load(PB_TXT) for t in play.get("tasks", [])]
HANDLERS = [h["name"] for play in yaml.safe_load(PB_TXT) for h in play.get("handlers", [])]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def task(nome):
    achadas = [t for t in TASKS if t.get("name") == nome]
    checar(len(achadas) == 1, f"task '{nome}' existe uma vez")
    return achadas[0] if achadas else {}


def conteudo(t):
    return (t.get("ansible.builtin.copy") or {}).get("content", "")


# unattended-upgrades
pacotes = next((t["ansible.builtin.apt"]["name"] for t in TASKS
                if t.get("name", "").startswith("Instalar pacotes Enterprise")), [])
for p in ("unattended-upgrades", "libpam-pwquality", "cracklib-runtime"):
    checar(p in pacotes, f"pacote {p} na lista do apt")

uu = task("Configurar unattended-upgrades só para Debian-Security, sem reboot automático")
arquivos = {i["nome"]: i["conteudo"] for i in uu.get("loop", [])}
checar('APT::Periodic::Unattended-Upgrade "1";' in arquivos.get("20auto-upgrades", ""),
       "20auto-upgrades liga o Unattended-Upgrade")
iac = arquivos.get("52unattended-upgrades-iac", "")
checar(iac.lstrip().startswith("#clear Unattended-Upgrade::Origins-Pattern;"),
       "52unattended-upgrades-iac começa limpando a lista do pacote (#clear)")
origens = re.findall(r'^\s*"([^"]+)";', iac, re.M)
checar(origens == ["origin=Debian,codename=${distro_codename}-security,label=Debian-Security"],
       f"Origins-Pattern é só a Debian-Security (achado: {origens})")
checar('Unattended-Upgrade::Automatic-Reboot "false";' in iac, "Automatic-Reboot false")

# sysctl
sysctl = conteudo(task("Configurar Sysctl Enterprise do Kernel (swappiness=10, fq_codel, somaxconn, "
                       "eBPF, suid_dumpable, dirty memory, max_map_count)"))
for linha in ("kernel.yama.ptrace_scope = 1", "fs.protected_symlinks = 1", "fs.protected_hardlinks = 1",
              "fs.protected_fifos = 2", "fs.protected_regular = 2", "net.ipv4.tcp_syncookies = 1"):
    checar(re.search(rf"^{re.escape(linha)}$", sysctl, re.M) is not None, f"sysctl: {linha}")

# export das configurações
pasta = task("Criar diretório local de exportação de backup das configurações do Proxmox")
checar(str(pasta.get("ansible.builtin.file", {}).get("mode")) == "0700", "pasta de export em 0700")
export = task("Exportar configurações essenciais do Proxmox VE")
script = export.get("ansible.builtin.shell", "")
checar(script.lstrip().startswith("umask 077"), "export roda com umask 077 antes do tar")

# consent-text
checar("consent-text: {{ proxmox_consent_text | b64encode }}" in DC, "consent-text vai em base64")
checar(re.search(r"{%\s*if proxmox_consent_text \| default\(''\) \| length > 0\s*%}", DC) is not None,
       "consent-text omitido quando vazio")

# Fail2ban
filtro = conteudo(task("Configurar o filtro do Fail2ban para a GUI do Proxmox"))
m = re.search(r"^failregex = (.+)$", filtro, re.M)
checar(m is not None, "filtro proxmox tem failregex")
checar("journalmatch = _SYSTEMD_UNIT=pvedaemon.service" in filtro, "filtro lê o journal do pvedaemon")
if m:
    rx = re.compile(m.group(1).replace("<HOST>", r"(?P<host>\S+)"))
    # Forma da linha real do PVE 9.2 (hipervisor secundário, 21/10/2026), com
    # o IP trocado por um de documentação (RFC 5737) e usuário inexistente.
    real = ("pvedaemon[133368]: authentication failure; rhost=::ffff:198.51.100.7 "
            "user=f2bteste@pam msg=no such user ('f2bteste@pam')")
    achado = rx.search(real)
    checar(achado is not None and achado.group("host") == "198.51.100.7",
           "failregex casa a linha real e extrai o IPv4, sem o ::ffff:")
    checar(rx.search("pvedaemon[1]: <root@pam> successful auth for user 'prometheus@pve'") is None,
           "failregex não casa login bem-sucedido")

jail = conteudo(task("Configurar Fail2ban para proteção do Proxmox Web GUI (Porta 8006)"))
sshd = conteudo(task("Configurar o ignoreip do jail sshd do Fail2ban"))
checar("filter = proxmox" in jail, "jail proxmox usa o filtro proxmox")
for nome, corpo in (("proxmox", jail), ("sshd", sshd)):
    checar("ignoreip = {{ fail2ban_ignoreip }}" in corpo, f"jail {nome} leva o ignoreip")
checar(sshd.lstrip().startswith("[sshd]"), "jail.d/sshd.conf é a seção [sshd]")
checar("Recarregar o Fail2ban" in HANDLERS, "handler que recarrega o Fail2ban existe")
for nome in ("Configurar Fail2ban para proteção do Proxmox Web GUI (Porta 8006)",
             "Configurar o filtro do Fail2ban para a GUI do Proxmox",
             "Configurar o ignoreip do jail sshd do Fail2ban"):
    checar(task(nome).get("notify") == "Recarregar o Fail2ban", f"'{nome}' recarrega o Fail2ban")

# pam_pwquality
pw = conteudo(task("Configurar a complexidade mínima de senha (pam_pwquality)"))
checar(re.search(r"^enforce_for_root$", pw, re.M) is not None, "pwquality com enforce_for_root")
checar(re.search(r"^minlen = (\d+)$", pw, re.M) is not None
       and int(re.search(r"^minlen = (\d+)$", pw, re.M).group(1)) >= 14, "pwquality minlen >= 14")
dropin = task("Criar o diretório de drop-ins do pwquality")
checar(dropin.get("ansible.builtin.file", {}).get("path") == "/etc/security/pwquality.conf.d",
       "task cria o diretório pwquality.conf.d")
nomes = [t.get("name") for t in TASKS]
checar(dropin in TASKS and pw and nomes.index("Criar o diretório de drop-ins do pwquality")
       < nomes.index("Configurar a complexidade mínima de senha (pam_pwquality)"),
       "diretório pwquality.conf.d criado antes do drop-in")

if falhas:
    sys.exit(f"\n✗ gates-proxmox-hardening: {len(falhas)} falha(s)")
print("\n✓ gates-proxmox-hardening")
