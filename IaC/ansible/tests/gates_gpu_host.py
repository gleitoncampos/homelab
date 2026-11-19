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
checar(set(ferr) == {"ollama", "whispercpp", "speaches", "whisperasr"}, f"ferramentas: ollama, whispercpp, speaches e whisperasr (achado: {sorted(ferr)})")
for nome, f in ferr.items():
    tag = str(f.get("image", "")).rsplit(":", 1)[-1]
    checar(":" in str(f.get("image", "")) and tag != "latest" and not tag.startswith("latest"),
           f"{nome}: imagem com tag fixa (achado: {f.get('image')})")
    checar(isinstance(f.get("pre_pull"), bool), f"{nome}: pre_pull explícito (true/false)")
    checar(isinstance(f.get("porta"), int) and isinstance(f.get("porta_container"), int) and f.get("dados"),
           f"{nome}: porta, porta_container e pasta de dados definidas")
checar(len({f["porta"] for f in ferr.values()}) == len(ferr), "portas publicadas distintas entre as ferramentas")
checar([n for n, f in ferr.items() if f.get("pre_pull")] == ["ollama"],
       "só o ollama baixa no apply: o disco do hulk (~38 GB) não comporta as imagens de todas")
wcpp = ferr.get("whispercpp", {}).get("image", "")
checar(re.search(r":main-cuda-[0-9a-f]{40}$", wcpp) is not None,
       "whisper.cpp fixado na tag por commit (`main-cuda-<sha>`), não na `main-cuda` flutuante")

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
checar("gpu_mac == ansible_facts.default_ipv4.macaddress" in pre, "o MAC é o da placa em uso (rota padrão), não o de qualquer placa do host")
checar(all("always" in (t.get("tags") or []) for t in play1.get("pre_tasks", [])),
       "toda pre_task com tag always (senão --tags rede pula o assert do MAC)")
checar(any((t.get("ansible.builtin.file") or {}).get("path") == "/run/hulk-keepalive.ansible"
           and (t.get("ansible.builtin.file") or {}).get("state") == "touch" for t in play1.get("pre_tasks", [])),
       "pre_task segura o desligamento (lock) durante a execução")
checar(any((t.get("ansible.builtin.file") or {}).get("path") == "/run/hulk-keepalive.ansible"
           and (t.get("ansible.builtin.file") or {}).get("state") == "absent" for t in play1.get("post_tasks", [])),
       "post_task libera o lock")

# --- NAS (kamartaj) por CIFS ---
checar("kamartaj_smb_password" in pre, "exige kamartaj_smb_password do vault")
cred = tarefa("Gravar a credencial do NAS (root, 0600)")
checar(cred.get("no_log") is True and cred.get("diff") is False,
       "a credencial do NAS não aparece na saída nem no --diff (no_log e diff: false)")
checar((cred.get("ansible.builtin.copy") or {}).get("mode") == "0600"
       and (cred.get("ansible.builtin.copy") or {}).get("owner") == "root",
       "credencial do NAS: dono root, modo 0600")
nas_mount = (tarefa("Montar o NAS (CIFS, leitura e escrita, sem travar o boot)").get("ansible.posix.mount") or {})
opts_nas = str(nas_mount.get("opts"))
checar(all(o in opts_nas for o in ("nofail", "x-systemd.automount", "_netdev", "x-systemd.mount-timeout")),
       "mount do NAS não trava o boot (nofail, automount, _netdev, timeout)")
checar("password=" not in opts_nas and "credentials=/etc/hulk/kamartaj.cred" in opts_nas,
       "mount do NAS usa arquivo de credencial, nunca a senha na linha do fstab")
checar("{{ ansible_user }}" in opts_nas and "user_uid" not in pb_txt.replace("`ansible_facts.user_uid`", ""),
       "uid/gid do mount por nome (ansible_facts.user_uid daria 0 sob become)")

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
for nome in ("ollama", "whispercpp", "speaches", "whisperasr"):
    txt = ler(f"templates/gpu/{nome}.compose.yml.j2")
    c = yaml.safe_load(re.sub(r"\{\{[^}]*\}\}", "x", txt)) if txt else {}
    svc_c = (c.get("services") or {}).get(nome, {})
    checar(svc_c.get("restart") in ("no", False), f"{nome}: restart 'no' (sob demanda, nada sobe no boot)")
    dev = (((svc_c.get("deploy") or {}).get("resources") or {}).get("reservations") or {}).get("devices") or [{}]
    checar(dev[0].get("driver") == "nvidia" and "gpu" in dev[0].get("capabilities", []), f"{nome}: reserva a GPU")
    checar("{{ gpu_ip_estatico }}:" in txt, f"{nome}: porta publicada só no IP do hulk, não em 0.0.0.0")
    checar("latest" not in txt, f"{nome}: sem latest no template")
wc_txt = ler("templates/gpu/whispercpp.compose.yml.j2")
wc = yaml.safe_load(re.sub(r"\{\{[^}]*\}\}", "x", wc_txt))["services"]["whispercpp"]
cmd = wc.get("command")
checar(isinstance(cmd, list) and len(cmd) == 1 and isinstance(cmd[0], str),
       "whisper.cpp: command é UMA string em lista de um item (o ENTRYPOINT da imagem é `bash -c`)")
cmd_s = " ".join(str(cmd[0]).split()) if isinstance(cmd, list) and cmd else ""
for flag in ("whisper-server", "--inference-path /v1/audio/transcriptions", "--model /models/ggml-large-v3-turbo.bin",
             "--beam-size 5", "--best-of 5", "--threads 4", "--vad-model /models/ggml-silero-v5.1.2.bin", "--vad"):
    checar(flag in cmd_s, f"whisper.cpp: flag `{flag}` (mesmo modelo e decodificação do Vibe)")
checar(any(v.endswith(":/models:ro") for v in wc.get("volumes", [])), "whisper.cpp: modelos montados somente leitura")
checar(any(".compose.yml.j2" in str(t) and "gpu_dir" in str(t) for t in t1), "playbook copia os compose para gpu_dir")
pull = tarefa("Baixar as imagens das ferramentas")
checar("pre_pull" in str(pull.get("loop")), "o pré-pull só roda nas ferramentas com pre_pull: true")
checar("gpu_ferramentas | list" in str(tarefa("Copiar o compose de cada ferramenta para gpu_dir").get("loop")),
       "um compose por ferramenta do dicionário, sem lista paralela escrita à mão")
checar(tarefa("Remover a pasta da ferramenta antiga (whisper, hoje speaches)").get("ansible.builtin.file", {}).get("state") == "absent",
       "remove a pasta do `whisper` antigo (virou speaches)")
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

# --- Endurecimento pós-revisão do Hades (2ª rodada): conteúdo e comportamento, não só presença ---
import subprocess
import tempfile
import textwrap

pre_assert = next((t for t in play1.get("pre_tasks", []) if "ansible.builtin.assert" in t and "gpu_mac" in str(t)), {})
that = (pre_assert.get("ansible.builtin.assert") or {}).get("that") or []
checar(any("gpu_mac | lower == gpu_mac" == x for x in that) and any("is match(" in x for x in that)
       and "gpu_mac is defined" in that, "assert do MAC: lista `that` completa (formato, minúsculas, definido)")
checar(pre_assert.get("ignore_errors") is None and "or true" not in str(pre_assert), "assert do MAC sem ignore_errors nem `or true`")

dock_assert = next((t for t in t1 if "ansible.builtin.assert" in t and "_drops" in str(t)), {})
that_d = (dock_assert.get("ansible.builtin.assert") or {}).get("that") or []
checar(any("_drops | length == gpu_ferramentas | length" in x for x in that_d)
       and any("_rets | length ==" in x for x in that_d)
       and any("_linhas.index(_drops[0]) > _linhas.index(_rets[-1])" in x for x in that_d),
       "assert da DOCKER-USER: contagem de DROP, de RETURN e ordem, as três")

sudo_c = (tarefa("Sudo sem senha para o usuário de automação").get("ansible.builtin.copy") or {}).get("content", "")
checar(re.fullmatch(r"\{\{ ansible_user \}\} ALL=\(ALL\) NOPASSWD:ALL\n", sudo_c) is not None,
       "sudoers é UMA linha exata para o ansible_user (nada de %grupo nem comentário)")

for nome_critico in ("Baixar as imagens das ferramentas", "Recriar o lock depois do reboot do driver"):
    checar("when" not in tarefa(nome_critico), f"'{nome_critico}' sem `when` (task crítica não pode ser neutralizada)")
checar(all("when" not in t for t in play1.get("pre_tasks", []) if "file" in str(t) and "keepalive" in str(t))
       and all("when" not in t for t in play1.get("post_tasks", [])), "lock de pre/post_tasks sem `when`")
ufw_reload = tarefa("Recarregar o UFW para valer a DOCKER-USER")
checar(ufw_reload.get("when") == "_after_gpu is changed" and "ufw reload" in str(ufw_reload.get("ansible.builtin.command")),
       "ufw reload condicionado só à mudança do bloco")
checar(nomes1.index("Recriar o lock depois do reboot do driver") > nomes1.index("Reiniciar para carregar o driver"),
       "o lock é recriado DEPOIS do reboot (que apaga o /run)")
checar(nomes1.index("Recriar o lock depois do reboot do driver") < nomes1.index("Baixar as imagens das ferramentas"),
       "e antes do pré-pull de GBs")
checar("link-local: []" in tpl and _lan0.get("link-local") == [],
       "a placa sem IPv6 link-local: o Ubuntu não aparece na MAIN nem por IPv6")

# Monitor: tráfego de rede conta como atividade; locks do chamador e do playbook
if script.is_file():
    checar(idle.decidir(0.0, 1.0, 29, False, True) == (0, False), "tráfego de rede zera o contador (pull de modelo longo)")
    checar(idle.decidir(0.0, 1.0, 5, False, False) == (6, False), "sem rede o contador segue")
    checar(isinstance(idle.ler_rede(), int) and idle.REDE_MIN >= 1024 * 1024, "ler_rede devolve bytes; limiar de rede >= 1 MiB por amostra")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "hulk-keepalive").write_text("")
        (Path(tmp) / "hulk-keepalive.ansible").write_text("")
        agora = _t.time()
        checar(idle.algum_lock_valido(f"{tmp}/hulk-keepalive*", agora) is True, "o glob enxerga o lock do chamador e o do playbook")
        os.remove(Path(tmp) / "hulk-keepalive")
        checar(idle.algum_lock_valido(f"{tmp}/hulk-keepalive*", agora) is True, "o lock do playbook sozinho segura o desligamento")
        os.remove(Path(tmp) / "hulk-keepalive.ansible")
        checar(idle.algum_lock_valido(f"{tmp}/hulk-keepalive*", agora) is False, "sem nenhum lock, não segura")

# hulk-wake: COMPORTAMENTO com nc e wakeonlan simulados (rc 0/2/3/4, reenvio, sem WoL se já no ar)
def rodar_wake(nc_ok, wol_ok, env_extra=None):
    with tempfile.TemporaryDirectory() as tmp:
        bin_ = Path(tmp) / "bin"
        bin_.mkdir()
        log = Path(tmp) / "wol.log"
        (bin_ / "nc").write_text("#!/bin/sh\nexit %d\n" % (0 if nc_ok else 1))
        (bin_ / "wakeonlan").write_text('#!/bin/sh\necho x >> "%s"\nexit %d\n' % (log, 0 if wol_ok else 1))
        for b in ("nc", "wakeonlan"):
            (bin_ / b).chmod(0o755)
        envj = jinja2.Environment(keep_trailing_newline=True)
        txt = envj.from_string(wake).render(hostvars={"hulk": {
            "gpu_mac": ":".join(["0a"] * 6), "gpu_ip_estatico": "192.0.2.12", "gpu_wol_broadcast": "192.0.2.255"}})
        sh = Path(tmp) / "hulk-wake"
        sh.write_text(txt)
        env = {"PATH": f"{bin_}:/usr/bin:/bin", **(env_extra or {})}
        r = subprocess.run(["bash", str(sh)], env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, (len(log.read_text().split()) if log.exists() else 0)

rc, n = rodar_wake(True, True)
checar((rc, n) == (0, 0), f"hulk já no ar: exit 0 e NENHUM WoL enviado (achado: rc={rc}, envios={n})")
rc, n = rodar_wake(False, False)
checar(rc == 4, f"WoL que falha ao enviar: exit 4 (achado: rc={rc})")
rc, n = rodar_wake(False, True, {"HULK_ESPERA": "6", "HULK_REENVIO": "1"})
checar(rc == 3 and n >= 2, f"não acorda: exit 3 no prazo e WoL REENVIADO (achado: rc={rc}, envios={n})")
rc, n = rodar_wake(False, True, {"HULK_ESPERA": "abc"})
checar(rc == 2 and n == 0, f"espera inválida: exit 2 antes de enviar qualquer WoL (achado: rc={rc}, envios={n})")

# --- Curva da ventoinha: 40 C parte, 60 C a 100 % ---
fan_script = RAIZ / "files" / "gpu" / "hulk-fan-control.py"
checar(fan_script.is_file(), "existe files/gpu/hulk-fan-control.py")
if fan_script.is_file():
    spec = importlib.util.spec_from_file_location("hulk_fan", fan_script)
    fan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fan)
    checar((fan.LIGA_C, fan.CHEIA_C) == (40.0, 60.0), "curva: parte a 40 C e chega ao máximo a 60 C")
    checar(fan.alvo_pct(60, 30) == 100 and fan.alvo_pct(75, 30) == 100, "a 60 C e acima: 100 %")
    checar(fan.alvo_pct(41, 30) > 30 and fan.alvo_pct(40, 30) == 30, "a 40 C o mínimo; acima dele a rampa sobe")
    checar(fan.alvo_pct(50, 30) == 65, "ponto médio (50 C) a meio caminho entre 30 e 100 % (65 %)")
    checar(all(fan.alvo_pct(t, 30) <= fan.alvo_pct(t + 1, 30) for t in range(30, 70)), "rampa monótona: nunca cai quando a temperatura sobe")
    checar(fan.decidir(40, False, 30) is None and fan.decidir(36, False, 30) is None, "<= 40 C sem manual: automático do driver")
    checar(fan.decidir(41, False, 30) == fan.alvo_pct(41, 30), "acima de 40 C assume o manual")
    checar(fan.decidir(39, True, 30) == 30, "histerese: depois de assumir, entre 38 e 40 C segura o mínimo")
    checar(fan.decidir(38, True, 30) is None, "<= 38 C devolve o automático")
    checar(fan.decidir(None, False, 30) == 100 and fan.decidir(None, True, 30) == 100, "temperatura ilegível: 100 % (fail-safe)")
fan_svc = ler("templates/gpu/hulk-fan.service.j2")
checar("ExecStopPost=" in fan_svc and "--restaurar" in fan_svc, "unit devolve a ventoinha ao automático ao parar (ExecStopPost)")
checar("Restart=always" in fan_svc and "WantedBy=multi-user.target" in fan_svc, "unit reinicia sozinha e sobe no boot")
fan_hab = tarefa("Habilitar e iniciar a curva da ventoinha").get("ansible.builtin.systemd") or {}
checar(fan_hab.get("name") == "hulk-fan.service" and fan_hab.get("enabled") is True,
       "playbook habilita o hulk-fan.service no boot")
checar("restarted" in str(fan_hab.get("state")) and "_fan_script" in str(fan_hab.get("state")),
       "script ou unit alterados reiniciam o serviço (senão o código novo não entra em uso)")

# --- novos blocos entram aqui (uma tarefa por bloco) ---

if falhas:
    print(f"\n{len(falhas)} falha(s)")
    sys.exit(1)
print("\ngate do hulk OK")
