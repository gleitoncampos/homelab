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

# --- GPU, toolkit e Docker (Tarefa 3) ---
drv = tarefa("Instalar o driver NVIDIA open")
checar("gpu_driver_pkg" in str((drv.get("ansible.builtin.apt") or {}).get("name")), "driver vem de gpu_driver_pkg")
checar("nvidia-driver-" not in pb_txt.replace("gpu_driver_pkg", ""),
       "nenhum nome de driver literal no playbook (versão só em variável)")
checar("Instalar o driver NVIDIA open" in nomes1 and "Reiniciar para carregar o driver" in nomes1
       and nomes1.index("Instalar o driver NVIDIA open") < nomes1.index("Reiniciar para carregar o driver"),
       "reboot depois do driver")
reb = tarefa("Reiniciar para carregar o driver")
checar("_driver" in str(reb.get("when")), "reboot só se o driver mudou")
checar(any("nvidia-smi" in str(t.get("ansible.builtin.command", "")) for t in t1), "confere nvidia-smi no host")
checar("signed-by" in pb_txt.lower() and "nvidia.github.io/libnvidia-container" in pb_txt,
       "repo do nvidia-container-toolkit com chave assinada (Signed-By)")
checar("Registrar o runtime nvidia no Docker" in nomes1 and "Reiniciar o Docker" in nomes1
       and nomes1.index("Registrar o runtime nvidia no Docker") < nomes1.index("Reiniciar o Docker"),
       "runtime registrado antes do restart do Docker")
prova = tarefa("Provar a GPU dentro de um container")
checar("--gpus all" in str(prova) and "nvidia-smi" in str(prova), "prova a GPU em container, não só no host")
checar(any("docker" in str(t.get("ansible.builtin.user", "")) for t in t1), "usuário no grupo docker")

# --- Monitor de ociosidade (Tarefa 4) ---
script = RAIZ / "files" / "gpu" / "hulk-idle-check.py"
checar(script.is_file(), "existe files/gpu/hulk-idle-check.py")
if script.is_file():
    spec = importlib.util.spec_from_file_location("hulk_idle", script)
    idle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(idle)
    checar((idle.GPU_MAX, idle.CPU_MAX) == (5.0, 10.0), "limiares: GPU < 5 e CPU < 10")
    checar(idle.AMOSTRAS == 30, "30 amostras (1/min = 30 min)")
    c = 0
    for _ in range(29):
        c, off = idle.decidir(0.0, 1.0, c, False)
    checar(c == 29 and off is False, "29 amostras ociosas NÃO desligam")
    c, off = idle.decidir(0.0, 1.0, c, False)
    checar(off is True, "a 30ª amostra seguida desliga")
    checar(idle.decidir(5.0, 1.0, 20, False) == (0, False), "GPU em 5% zera o contador (limite é estrito)")
    checar(idle.decidir(0.0, 10.0, 20, False) == (0, False), "CPU em 10% zera o contador")
    checar(idle.decidir(0.0, 1.0, 29, True) == (0, False), "lock de keepalive zera e não desliga")
    checar(idle.decidir(None, 1.0, 29, False) == (0, False), "GPU ilegível: fail-safe, não desliga")
    checar(idle.decidir(0.0, None, 29, False) == (0, False), "CPU sem amostra anterior: não desliga")
svc = ler("templates/gpu/hulk-idle.service.j2")
tmr = ler("templates/gpu/hulk-idle.timer.j2")
checar("Type=oneshot" in svc and "StateDirectory=hulk-idle" in svc, "service oneshot com StateDirectory")
checar("OnUnitActiveSec=1min" in tmr and "OnBootSec=1min" in tmr, "timer a cada 1 min, primeira em 1 min do boot")
checar(any("hulk-idle.timer" in str(t) for t in t1), "playbook habilita o timer")

# --- Ferramentas e portas (Tarefa 5) ---
for nome in ("ollama", "whisper"):
    txt = ler(f"templates/gpu/{nome}.compose.yml.j2")
    c = yaml.safe_load(re.sub(r"\{\{[^}]*\}\}", "x", txt)) if txt else {}
    svc_c = (c.get("services") or {}).get(nome, {})
    checar(svc_c.get("restart") in ("no", False), f"{nome}: restart 'no' (sob demanda, nada sobe no boot)")
    dev = (((svc_c.get("deploy") or {}).get("resources") or {}).get("reservations") or {}).get("devices") or [{}]
    checar(dev[0].get("driver") == "nvidia" and "gpu" in dev[0].get("capabilities", []), f"{nome}: reserva a GPU")
    checar("{{ gpu_ip_estatico }}:" in txt, f"{nome}: porta publicada só no IP do hulk, não em 0.0.0.0")
    checar("latest" not in txt, f"{nome}: sem latest no template")
checar(any(".compose.yml.j2" in str(t) and "gpu_dir" in str(t) for t in t1), "playbook copia os compose para gpu_dir")
regras = ler("templates/gpu/ufw_docker_user.rules.j2")
checar("gpu_clientes_cidrs" in regras and "-j RETURN" in regras and "-j DROP" in regras,
       "DOCKER-USER: RETURN para clientes, DROP para o resto")
checar("-j RETURN" in regras and "-j DROP" in regras and regras.index("-j RETURN") < regras.index("-j DROP"),
       "todo RETURN antes do DROP")
checar("ufw reload" in pb_txt, "recarrega o UFW para valer a DOCKER-USER")

# --- novos blocos entram aqui (uma tarefa por bloco) ---

if falhas:
    print(f"\n{len(falhas)} falha(s)")
    sys.exit(1)
print("\ngate do hulk OK")
