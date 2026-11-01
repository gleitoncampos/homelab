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
COMMON = (RAIZ / "ansible" / "inventories" / "_common" / "all.yml").read_text(encoding="utf-8")
falhas = []


def nomes_tasks():
    return [t.get("name") for t in TASKS]


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
ign = lambda corpo: re.findall(r"^ignoreip = (.*)$", corpo, re.M)
checar(ign(jail) == ["{{ fail2ban_ignoreip }} {{ fail2ban_ignoreip_proxmox }}"],
       f"jail proxmox leva o ignoreip comum + o do manager (achado: {ign(jail)})")
checar(ign(sshd) == ["{{ fail2ban_ignoreip }}"],
       f"jail sshd leva só o ignoreip comum, sem o manager (achado: {ign(sshd)})")
expr = re.search(r"^fail2ban_ignoreip_proxmox: >-\n((?:[ ]+\S.*\n)+)", COMMON, re.M)
expr = " ".join(expr.group(1).split()) if expr else ""
checar(expr == "{{ groups['homelab_managers'] | map('extract', hostvars, 'ansible_host') | join(' ') }}",
       f"fail2ban_ignoreip_proxmox = ansible_host dos homelab_managers, sem default (achado: {expr!r})")
guarda = task("Exigir o manager do Swarm na isenção do jail da 8006")
checar((guarda.get("ansible.builtin.assert") or {}).get("that")
       == ["fail2ban_ignoreip_proxmox | trim | length > 0"]
       and set(guarda) <= {"name", "ansible.builtin.assert"}
       and nomes_tasks().index("Exigir o manager do Swarm na isenção do jail da 8006")
       < nomes_tasks().index("Configurar Fail2ban para proteção do Proxmox Web GUI (Porta 8006)"),
       "assert de isenção não vazia roda antes de gravar o jail, sem when/ignore_errors")
checar(sshd.lstrip().startswith("[sshd]"), "jail.d/sshd.conf é a seção [sshd]")
conf = task("Conferir que os jails sshd e proxmox do Fail2ban estão ativos")
checar(conf.get("ansible.builtin.command") == "fail2ban-client status {{ item }}"
       and conf.get("loop") == ["sshd", "proxmox"], "task confere sshd e proxmox no fail2ban-client")
efet = task("Conferir que o ignoreip efetivo do jail proxmox tem o manager")
checar(efet.get("ansible.builtin.command") == "fail2ban-client get proxmox ignoreip"
       and "fail2ban_ignoreip_proxmox.split()" in str(efet.get("failed_when", ""))
       and "_f2b_ignoreip.rc != 0" in str(efet.get("failed_when", "")),
       "task confere o manager no ignoreip efetivo e falha se o comando falhar")
for t, nome in ((conf, "status dos jails"), (efet, "ignoreip efetivo")):
    checar(t.get("when") == "not ansible_check_mode" and "ignore_errors" not in t
           and (t is efet or "failed_when" not in t),
           f"conferência '{nome}' não é desligável (when exato, sem ignore_errors/failed_when)")
for nome in ("Conferir que os jails sshd e proxmox do Fail2ban estão ativos",
             "Conferir que o ignoreip efetivo do jail proxmox tem o manager"):
    checar(bool(conf) and bool(efet) and nomes_tasks().index("Recarregar o sshd agora")
           < nomes_tasks().index(nome), f"'{nome}' roda depois do flush_handlers")
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
