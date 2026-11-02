#!/usr/bin/env python3
"""Gate do spindown dos HDDs do NAS (00-omv-setup.yml).

O OMV 8 não lê o /etc/hdparm.conf: o spindown vai por disco no banco do OMV
(`DiskMgmt setHdParm`) e chega ao disco pelo deploy do smartmontools. Até
01/11/2026 o play gravava uma seção inválida no hdparm.conf e nenhum disco
dormia. Confere:

  - os parâmetros alvo: spindown 240 (20 min) e writecache ligado — o script
    do OMV sempre manda `wcache`, e o padrão do modelo é desligado;
  - HDD = rotacional com link `ata-`, sem serial escrito no repo;
  - registro por `setHdParm`, idempotente contra o banco, seguido do deploy
    do smartmontools e da conferência do cache de escrita;
  - o play não volta a escrever conteúdo próprio no /etc/hdparm.conf: só
    restaura o do pacote.
"""
import re
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
PB_TXT = (RAIZ / "ansible" / "playbooks" / "00-omv-setup.yml").read_text(encoding="utf-8")
PB = yaml.safe_load(PB_TXT)
VARS = PB[0].get("vars", {})
TASKS = [t for play in PB for t in play.get("tasks", [])]
NOMES = [t.get("name") for t in TASKS]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def task(nome):
    achadas = [t for t in TASKS if t.get("name") == nome]
    checar(len(achadas) == 1, f"task '{nome}' existe uma vez")
    return achadas[0] if achadas else {}


def antes(a, b):
    return a in NOMES and b in NOMES and NOMES.index(a) < NOMES.index(b)


# Parâmetros alvo
alvo = VARS.get("omv_hdparm_alvo", {})
checar(alvo.get("spindowntime") == 240, f"spindowntime 240 = 20 min (achado: {alvo.get('spindowntime')})")
checar(alvo.get("writecache") is True, f"writecache true (achado: {alvo.get('writecache')!r})")
checar(alvo.get("apm") == 0 and alvo.get("aam") == 0, "apm e aam 0 (off)")
checar(set(alvo) == {"spindowntime", "writecache", "apm", "aam"},
       f"alvo tem só os 4 campos do setHdParm além de uuid/devicefile (achado: {sorted(alvo)})")

# Seleção dos HDDs
hdds = " ".join(str(VARS.get("omv_hdds", "")).split())
checar("selectattr('value.rotational', 'equalto', '1')" in hdds, "omv_hdds filtra rotacional")
checar("select('match', '^ata-')" in hdds, "omv_hdds usa o link ata- (o que o OMV grava)")
checar(not re.search(r"ata-[A-Z0-9]", PB_TXT), "nenhum id ata- de disco escrito no play")
guarda = task("Exigir HDD detectado para o spindown")
checar((guarda.get("ansible.builtin.assert") or {}).get("that") == "omv_hdds | length > 0",
       "assert exige ao menos um HDD")

# Registro no OMV
ler = task("Ler a configuração de disco (hdparm) do OMV")
checar(ler.get("ansible.builtin.command") == "omv-confdbadm read conf.system.hdparm",
       "lê conf.system.hdparm antes de gravar")
reg = task("Registrar no OMV o spindown e o cache de escrita ligado de cada HDD")
argv = (reg.get("ansible.builtin.command") or {}).get("argv", [])
checar(argv[:5] == ["omv-rpc", "-u", "admin", "DiskMgmt", "setHdParm"], "grava por DiskMgmt setHdParm")
payload = " ".join(str(argv[5]).split()) if len(argv) > 5 else ""
checar(payload.startswith("{{ omv_hdparm_alvo | combine(") and "'devicefile': item" in payload
       and "default(mergerfs_new_object_uuid)" in payload,
       "payload = alvo + uuid existente (ou objeto novo) + devicefile")
checar(reg.get("loop") == "{{ omv_hdds }}", "um registro por HDD")
cond = " ".join(" ".join(map(str, reg.get("when", []))).split())
checar("item not in omv_hdparm_atual" in cond
       and "(omv_hdparm_alvo.keys() | map('extract', omv_hdparm_atual[item]) | list) "
           "!= (omv_hdparm_alvo.values() | list)" in cond,
       "só grava o que diverge do banco (idempotente)")
wc = task("Conferir que o cache de escrita continua ligado nos HDDs")
checar(wc.get("ansible.builtin.command") == "hdparm -W {{ item }}" and wc.get("loop") == "{{ omv_hdds }}"
       and "search('write-caching\\s*=\\s*1')" in str(wc.get("failed_when", ""))
       and "ignore_errors" not in wc, "confere wcache=1 em cada HDD depois do deploy")
# O deploy é do applyChanges (o módulo smartmontools se marca pendente).
apl = task("Aplicar os módulos pendentes do OMV")
checar("Config applyChanges" in str(apl.get("ansible.builtin.command", "")), "applyChanges é quem faz o deploy")
checar(not any("deploy run smartmontools" in str(t.get("ansible.builtin.command", "")) for t in TASKS),
       "sem deploy explícito do smartmontools (o applyChanges já faz)")
ordem = ["Ler a configuração de disco (hdparm) do OMV", "Exigir HDD detectado para o spindown",
         "Registrar no OMV o spindown e o cache de escrita ligado de cada HDD",
         "Aplicar os módulos pendentes do OMV", "Conferir que não sobrou módulo pendente no OMV",
         "Conferir que o cache de escrita continua ligado nos HDDs"]
checar(all(antes(a, b) for a, b in zip(ordem, ordem[1:])),
       "ler → exigir → gravar → applyChanges → conferir pendência → conferir wcache, nessa ordem")
# M2: o que grava ou depende do que foi gravado não roda no --check.
for nome, t in (("registro", reg), ("conferência do wcache", wc),
                ("restauração do hdparm.conf", task("Restaurar o /etc/hdparm.conf do pacote")),
                ("conferência do hdparm.conf", task("Conferir que o /etc/hdparm.conf é o do pacote"))):
    w = t.get("when", [])
    w = [w] if isinstance(w, str) else w
    checar("not ansible_check_mode" in w, f"{nome} tem guarda `not ansible_check_mode`")

# Coletor SMART espaçado para depois do spindown
tim = task("Espaçar o coletor SMART para depois do spindown (30 min)")
cp = tim.get("ansible.builtin.copy") or {}
checar(cp.get("dest") == "/etc/systemd/system/prometheus-node-exporter-smartmon.timer.d/override.conf",
       "drop-in no timer do coletor SMART")
linhas = [ln.strip() for ln in str(cp.get("content", "")).splitlines() if ln.strip()]
iv = re.fullmatch(r"OnUnitActiveSec=(\d+)min", linhas[-1]) if linhas else None
checar(linhas[:2] == ["[Timer]", "OnUnitActiveSec="] and len(linhas) == 4 and iv is not None
       and int(iv.group(1)) * 60 > alvo.get("spindowntime", 0) * 5,
       f"timer zera o intervalo do pacote e coleta depois do spindown (achado: {linhas})")
# A linha vazia zera todos os gatilhos: o OnBootSec do pacote tem de voltar
# depois dela, senão após reboot o timer nunca dispara.
checar(len(linhas) == 4 and linhas[2] == "OnBootSec=0",
       "OnBootSec=0 redeclarado depois da linha que zera os gatilhos")
checar(tim.get("notify") == "Recarregar o timer do coletor SMART", "drop-in recarrega o timer")
hand = [h for play in PB for h in play.get("handlers", [])
        if h.get("name") == "Recarregar o timer do coletor SMART"]
sd = (hand[0].get("ansible.builtin.systemd") or {}) if hand else {}
checar(sd.get("name") == "prometheus-node-exporter-smartmon.timer" and sd.get("daemon_reload") is True
       and sd.get("state") == "restarted", "handler faz daemon-reload e restart do timer")

# /etc/hdparm.conf: só o do pacote
for t in TASKS:
    mod = t.get("ansible.builtin.copy") or t.get("ansible.builtin.template") or {}
    checar(mod.get("dest") != "/etc/hdparm.conf", f"'{t.get('name')}' não escreve conteúdo no /etc/hdparm.conf")
checar("command_line_options" not in PB_TXT.replace("`command_line_options {`", ""),
       "seção inválida command_line_options fora do play")
div = task("Decidir se o /etc/hdparm.conf diverge do pacote")
expr = " ".join(str((div.get("ansible.builtin.set_fact") or {}).get("hdparm_conf_diverge", "")).split())
checar(expr == "{{ hdparm_conf_md5.stdout_lines | length != 2 "
               "or hdparm_conf_md5.stdout_lines | unique | length != 1 }}",
       f"diverge = arquivo ausente ou md5 diferente do conffile (achado: {expr!r})")
rest = task("Restaurar o /etc/hdparm.conf do pacote")
checar("--force-confmiss" in " ".join((rest.get("ansible.builtin.command") or {}).get("argv", []))
       and "hdparm_conf_diverge | bool" in rest.get("when", []), "restaura com --force-confmiss quando diverge")
conf = task("Conferir que o /etc/hdparm.conf é o do pacote")
checar("length != 2" in str(conf.get("failed_when", "")) and "unique | length != 1" in str(conf.get("failed_when", "")),
       "confere md5 = conffile depois")

if falhas:
    sys.exit(f"\n✗ gates-omv-spindown: {len(falhas)} falha(s)")
print("\n✓ gates-omv-spindown")
