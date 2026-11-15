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
checar("ansible_facts" in pre and "gpu_mac in _macs_do_host" in pre, "o MAC tem de ser de uma placa do próprio hulk (fatos)")
checar(all("always" in (t.get("tags") or []) for t in play1.get("pre_tasks", [])),
       "toda pre_task com tag always (senão --tags rede pula o assert do MAC)")
checar(any((t.get("ansible.builtin.file") or {}).get("path") == "/run/hulk-keepalive"
           and (t.get("ansible.builtin.file") or {}).get("state") == "touch" for t in play1.get("pre_tasks", [])),
       "pre_task segura o desligamento (lock) durante a execução")
checar(any((t.get("ansible.builtin.file") or {}).get("path") == "/run/hulk-keepalive"
           and (t.get("ansible.builtin.file") or {}).get("state") == "absent" for t in play1.get("post_tasks", [])),
       "post_task libera o lock")

REDE_GRAVA = ["Gravar o netplan estático do hulk", "Impedir o cloud-init de regenerar a rede",
              "Listar os outros arquivos de netplan (instalador e cloud-init)",
              "Remover os outros netplans (DHCP do instalador somaria ao estático)"]
for nome_rede in REDE_GRAVA:
    checar("gpu_rede_estatica" in str(tarefa(nome_rede).get("when")),
           f"'{nome_rede[:45]}' só com gpu_rede_estatica (na fase A apagaria o netplan DHCP)")
checar(nomes1.index("Gravar o netplan estático do hulk") > nomes1.index("Reiniciar para carregar o driver") if "Reiniciar para carregar o driver" in nomes1 else False,
       "rede só depois do reboot do driver (senão reinicia com o IP novo na porta errada)")
checar("netplan apply" not in pb_txt.replace("`netplan apply`", ""),
       "o Ansible nunca roda netplan apply (cortaria a própria conexão)")
checar("netplan generate" in pb_txt, "valida o netplan com netplan generate")
tpl = ler("templates/gpu/netplan.yaml.j2")
checar("wakeonlan: true" in tpl, "netplan liga o wakeonlan na placa")
checar("dhcp4: false" in tpl and "accept-ra: false" in tpl, "sem DHCP e sem RA (IPv6 fora de escopo)")
checar("macaddress: {{ gpu_mac }}" in tpl, "casa a placa por MAC (nome da interface é instável)")
checar("set-name" not in tpl, "sem renomear interface")
import json as _json
import jinja2
_env = jinja2.Environment(keep_trailing_newline=True)
_env.filters["to_json"] = _json.dumps
_rend = yaml.safe_load(_env.from_string(tpl).render(
    gpu_mac=":".join(["0a"] * 6), gpu_ip_estatico="192.0.2.12", gpu_prefixo=24,
    gpu_gateway="192.0.2.1", gpu_dns=["192.0.2.1"], gpu_vlan_id=20))["network"]
_lan0 = _rend["ethernets"]["lan0"]
_v = _rend.get("vlans", {}).get("vlan20", {})
checar("addresses" not in _lan0 and _lan0.get("dhcp4") is False,
       "placa sem IP e sem DHCP: o Ubuntu não pode aparecer na MAIN (a porta é híbrida)")
checar(_v.get("id") == 20 and _v.get("link") == "lan0", "vlan20 (id 20) em cima da placa lan0")
checar(_v.get("addresses") == ["192.0.2.12/24"] and _v.get("routes", [{}])[0].get("via") == "192.0.2.1",
       "IP, rota e gateway ficam na subinterface de VLAN")
checar(d.get("gpu_vlan_id") == 20, "gpu_vlan_id = 20")
ssh = tarefa("Fechar o SSH para login por senha")
checar("PasswordAuthentication no" in str(ssh), "SSH sem senha")
checar(any("sshd -T" in str(t) for t in t1), "confere o sshd efetivo, não só o arquivo")
checar(any("unattended-upgrades" in str(t) for t in t1), "unattended-upgrades presente")
sudoers = tarefa("Sudo sem senha para o usuário de automação")
sc = sudoers.get("ansible.builtin.copy") or {}
checar("NOPASSWD" in str(sc.get("content")) and "ansible_user" in str(sc.get("content")),
       "sudoers só para o ansible_user")
checar("visudo -cf" in str(sc.get("validate")) and sc.get("mode") == "0440",
       "sudoers validado por visudo e em 0440 (sudoers inválido tranca o sudo)")
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
repo_nv = tarefa("Declarar o repositório do nvidia-container-toolkit")
conteudo_repo = str((repo_nv.get("ansible.builtin.copy") or {}).get("content"))
checar("Signed-By:" in conteudo_repo and "nvidia.github.io/libnvidia-container" in conteudo_repo,
       "repo do nvidia-container-toolkit com chave assinada (Signed-By no conteúdo da task)")
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
hab = tarefa("Habilitar e iniciar o timer de ociosidade").get("ansible.builtin.systemd") or {}
checar(hab.get("name") == "hulk-idle.timer" and hab.get("state") == "started" and hab.get("enabled") is True,
       "task habilita E inicia o timer (a de instalação não conta)")
checar(idle.lock_valido.__name__ == "lock_valido" if script.is_file() else False, "monitor tem lock_valido")
if script.is_file():
    import os
    import tempfile
    import time as _t
    with tempfile.TemporaryDirectory() as tmp:
        lk = Path(tmp) / "lock"
        checar(idle.lock_valido(lk, _t.time()) is False, "sem arquivo: sem lock")
        lk.write_text("")
        checar(idle.lock_valido(lk, _t.time()) is True, "lock recente segura o desligamento")
        os.utime(lk, (_t.time() - 5 * 3600, _t.time() - 5 * 3600))
        checar(idle.lock_valido(lk, _t.time()) is False, "lock com mais de 4 h expira (não prende o hulk ligado)")

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
checar("-j RETURN" in regras and "-j DROP" in regras and regras.rindex("-j RETURN") < regras.index("-j DROP"),
       "todo RETURN antes do DROP")
checar("ufw reload" in pb_txt, "recarrega o UFW para valer a DOCKER-USER")
checar("porta_container" in regras, "DOCKER-USER usa a porta do container (pós-DNAT), não a publicada")
checar(any("iptables -S DOCKER-USER" in str(t) for t in t1) and any("_docker_user" in str(t.get("vars")) and "ansible.builtin.assert" in t for t in t1),
       "confere a chain DOCKER-USER carregada (ufw reload sai 0 com o bloco perdido)")
checar(any("docker pull" in str(t.get("ansible.builtin.command", "")) for t in t1), "pré-pull das imagens (senão o 1º up baixa GBs ocioso)")

# --- Play 2: hulk-wake no node1 (Tarefa 6) ---
play2 = pb[1] if len(pb) > 1 else {}
checar(play2.get("hosts") == "homelab_managers[0]", "play 2 mira só o primeiro manager (mesma VLAN do hulk)")
wake = ler("templates/gpu/hulk-wake.sh.j2")
checar("wakeonlan -i" in wake and "{{ hostvars['hulk'].gpu_mac }}" in wake, "envia o pacote mágico ao MAC do hulk")
checar("HULK_ESPERA:-180" in wake and "exit 3" in wake, "espera 180 s e sai com 3, sem retry infinito")
checar("exit 4" in wake and "hostvars['hulk'].gpu_ip_estatico" in wake,
       "falha do WoL sai 4 (não 3) e o IP é o final (gpu_ip_estatico), não o do DHCP da fase A")
checar("hulk-keepalive" in wake, "documenta o contrato do lock para o chamador")
checar("export LC_ALL=C" in wake and "22 >/dev/null 2>&1" in wake, "saída limpa: sem aviso de locale do perl e sem eco do nc")
checar("nc -z" in wake and "exit 0" in wake, "já ligado: sai 0 sem reenviar")
t2 = list(achatar(play2.get("tasks")))
checar(any("hulk-wake" in str(t) for t in t2), "play 2 instala o hulk-wake")
checar(any("wakeonlan" in str(t.get("ansible.builtin.apt", "")) for t in t2), "play 2 instala o wakeonlan")
checar("'hulk' in groups['gpu']" in str(play2.get("pre_tasks")), "play 2 aborta se o hulk não está no inventário")

# --- novos blocos entram aqui (uma tarefa por bloco) ---

if falhas:
    print(f"\n{len(falhas)} falha(s)")
    sys.exit(1)
print("\ngate do hulk OK")
