#!/usr/bin/env python3
"""Gate do hulk (playbooks/08-gpu-host.yml): servidor de GPU sob demanda.

Cada bloco trava uma decisão da spec (Frentes/homelab/spec-2026-11-15-hulk-gpu-sob-demanda.md):
o hulk fica FORA do grupo `homelab`, a rede estática só é gravada (nunca
aplicada) enquanto `gpu_rede_estatica` for false, o desligamento por
ociosidade exige 30 amostras seguidas, e a imagem de ferramenta tem tag fixa.
"""
import importlib.util
import re
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def ler(rel):
    caminho = RAIZ / rel
    checar(caminho.is_file(), f"existe {rel}")
    return caminho.read_text(encoding="utf-8") if caminho.is_file() else ""


# --- Inventário e variáveis (Tarefa 1) ---
exemplo = ler("inventories/prod/hosts.ini.example")
checar(re.search(r"^\[gpu\]\s*$", exemplo, re.M) is not None, "hosts.ini.example tem o grupo [gpu]")
checar(re.search(r"^hulk ansible_host=", exemplo, re.M) is not None, "hulk declarado no grupo gpu")
filhos = re.search(r"^\[homelab:children\]\n((?:[a-z_]+\n)+)", exemplo, re.M)
checar(filhos is not None and "gpu" not in filhos.group(1).split(),
       "gpu NÃO é filho de homelab (01-bootstrap-os e Swarm nunca tocam o hulk)")

d = yaml.safe_load(ler("inventories/prod/group_vars/gpu/10-defaults.yml")) or {}
checar(d.get("gpu_rede_estatica") is False, "gpu_rede_estatica nasce false (rede só gravada na fase B)")
checar("gpu_mac" not in d, "gpu_mac fora do arquivo versionado (o hook barra MAC; vive no 99-local.yml)")
checar(str(d.get("gpu_ip_estatico", "")).startswith("192.0.2."),
       "gpu_ip_estatico fictício (192.0.2.0/24) no arquivo versionado")
checar(d.get("gpu_prefixo") == 24 and isinstance(d.get("gpu_dns"), list), "prefixo 24 e DNS em lista")
checar(isinstance(d.get("gpu_clientes_cidrs"), list) and bool(d.get("gpu_clientes_cidrs")),
       "gpu_clientes_cidrs é lista não vazia")
checar(str(d.get("gpu_driver_pkg", "")).endswith("-open"), "gpu_driver_pkg termina em -open")
versao = re.search(r"nvidia-driver-(\d+)-open", str(d.get("gpu_driver_pkg", "")))
checar(versao is not None and int(versao.group(1)) >= 570, "driver >= 570 (Blackwell)")
checar(d.get("gpu_dir") == "/srv/containers/gpu", "gpu_dir = /srv/containers/gpu")
ferr = d.get("gpu_ferramentas", {})
checar(set(ferr) == {"ollama", "whisper"}, f"ferramentas: ollama e whisper (achado: {sorted(ferr)})")
for nome, f in ferr.items():
    tag = str(f.get("image", "")).rsplit(":", 1)[-1]
    checar(":" in str(f.get("image", "")) and tag != "latest" and not tag.startswith("latest"),
           f"{nome}: imagem com tag fixa (achado: {f.get('image')})")

# --- Playbook 08, play 1: base, SSH, rede, firewall (Tarefa 2) ---
pb_txt = ler("playbooks/08-gpu-host.yml")
pb = yaml.safe_load(pb_txt) if pb_txt else [{}, {}]
play1 = pb[0]


def achatar(tarefas):
    for t in tarefas or []:
        yield t
        yield from achatar(t.get("block"))


t1 = list(achatar(play1.get("tasks")))
nomes1 = [t.get("name", "") for t in t1]


def tarefa(nome):
    achadas = [t for t in t1 if t.get("name") == nome]
    checar(len(achadas) == 1, f"task '{nome}' existe uma vez")
    return achadas[0] if achadas else {}


checar(play1.get("hosts") == "gpu", "play 1 mira só o grupo gpu")
pre = str(play1.get("pre_tasks"))
checar("validate_vault.yml" in pre and "ssh_public_key" in pre, "exige ssh_public_key do vault")
checar("gpu_mac is defined" in pre, "aborta se gpu_mac não estiver definido (vive no 99-local.yml)")

rede = tarefa("Gravar o netplan estático do hulk")
checar("gpu_rede_estatica" in str(rede.get("when")), "netplan estático só quando gpu_rede_estatica")
checar("netplan apply" not in pb_txt.replace("`netplan apply`", ""),
       "o Ansible nunca roda netplan apply (cortaria a própria conexão)")
checar("netplan generate" in pb_txt, "valida o netplan com netplan generate")
tpl = ler("templates/gpu/netplan.yaml.j2")
checar("wakeonlan: true" in tpl, "netplan liga o wakeonlan na placa")
checar("dhcp4: false" in tpl and "accept-ra: false" in tpl, "sem DHCP e sem RA (IPv6 fora de escopo)")
checar("macaddress: {{ gpu_mac }}" in tpl, "casa a placa por MAC (nome da interface é instável)")
checar("set-name" not in tpl, "sem renomear interface")
ssh = tarefa("Fechar o SSH para login por senha")
checar("PasswordAuthentication no" in str(ssh), "SSH sem senha")
checar(any("sshd -T" in str(t) for t in t1), "confere o sshd efetivo, não só o arquivo")
checar(any("unattended-upgrades" in str(t) for t in t1), "unattended-upgrades presente")
ufw22 = [t for t in t1 if (t.get("community.general.ufw") or {}).get("port") == "22"]
checar(len(ufw22) == 1 and "src" in ufw22[0]["community.general.ufw"], "SSH liberado só por origem (src)")
i_ufw_on = next((i for i, t in enumerate(t1)
                 if (t.get("community.general.ufw") or {}).get("state") == "enabled"), -1)
i_ufw_22 = next((i for i, t in enumerate(t1) if ufw22 and t is ufw22[0]), -1)
checar(i_ufw_22 != -1 and i_ufw_on > i_ufw_22, "UFW só liga depois de liberar o SSH (senão tranca o próprio acesso)")

# --- novos blocos entram aqui (uma tarefa por bloco) ---

if falhas:
    print(f"\n{len(falhas)} falha(s)")
    sys.exit(1)
print("\ngate do hulk OK")
