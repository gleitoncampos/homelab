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
# O exportador lua não tem coletor de filesystem: o painel de disco lia
# node_filesystem_* e ficava sempre vazio (20/10/2026).
checar({"openwrt_filesystem_size_bytes", "openwrt_filesystem_avail_bytes"} <= usadas,
       "dashboard do OpenWrt mede o armazenamento pelo script")
checar("node_filesystem_" not in exprs, "dashboard do OpenWrt não consulta node_filesystem_* (o AP não publica)")

# Curvas por canal dos vizinhos (20/10/2026): plugin Business Charts instalado
# antes da partida e na MESMA versão dos painéis; métrica do painel tem que
# sair do script, senão é painel vazio sem erro.
_graf = (servicos.get("grafana") or {}).get("environment") or {}
_pin = re.fullmatch(r"volkovlabs-echarts-panel@(\d+\.\d+\.\d+)", str(_graf.get("GF_PLUGINS_PREINSTALL_SYNC", "")))
checar(_pin is not None, "Grafana instala o volkovlabs-echarts-panel com versão fixada, antes de subir (_SYNC)")
_ech = [p for p in painel["panels"] if p.get("type") == "volkovlabs-echarts-panel"]
checar(len(_ech) == 2, f"dashboard do OpenWrt tem as curvas das duas bandas ({len(_ech)})")
checar(bool(_pin) and all(p.get("pluginVersion") == _pin.group(1) for p in _ech),
       "painéis ECharts na mesma versão do plugin instalado")
# SSID é escrito por terceiros e o tooltip do ECharts faz innerHTML: nome cru
# num formatter é XSS armazenado no Grafana (Hades, 20/10/2026).
for _p in _ech:
    _js = _p["options"]["getOption"]
    checar("html(nome)" in _js and "${nome}" not in _js,
           f"painel {_p['id']}: SSID só entra no tooltip com escape de HTML")
_viz_script = set(re.findall(r"\b(wifi_neighbors?_[a-z_]+)\b",
                             (ANSIBLE / "templates/network/openwrt_wifi_neighbors.uc.j2").read_text(encoding="utf-8")))
_viz_painel = set(re.findall(r"\b(wifi_neighbors?_[a-z_]+)\b", exprs))
checar(bool(_viz_painel) and _viz_painel <= _viz_script,
       f"toda métrica de vizinhos do dashboard sai do script ({sorted(_viz_painel - _viz_script)})")
checar(usadas <= metricas_script,
       f"toda métrica openwrt_* do dashboard sai do script ({sorted(usadas - metricas_script)})")

# Painel "o que está fora" (blackbox_exporter).
_bb = servicos.get("blackbox") or {}
checar(re.fullmatch(r"quay\.io/prometheus/blackbox-exporter:v\d+\.\d+\.\d+", _bb.get("image", "")) is not None,
       f"blackbox com tag de versão fixa (veio {_bb.get('image')!r})")
checar("NET_RAW" in (_bb.get("cap_add") or []), "blackbox com NET_RAW declarado (o icmp não depende do conjunto padrão do engine)")
checar("node.role == manager" in (((_bb.get("deploy") or {}).get("placement") or {}).get("constraints") or []),
       "blackbox fixo no manager (só ele escapa do DNS forçado)")
_bb_mount = "/srv/containers/blackbox/blackbox.yml:/etc/blackbox/blackbox.yml:ro"
checar(_bb_mount in (_bb.get("volumes") or []), "blackbox monta o arquivo de módulos somente-leitura")
_comum = yaml.safe_load((ANSIBLE / "inventories/_common/all.yml").read_text(encoding="utf-8"))
checar(any(f.get("src") == "blackbox.yml" and f.get("dest") == _bb_mount.split(":")[0]
           for f in _comum.get("monitoring_sidecar_files", [])),
       "blackbox.yml é entregue pelo playbook no caminho que o compose monta")
_modulos = set(yaml.safe_load((REPO / "docker/monitoring/swarm/blackbox.yml").read_text(encoding="utf-8"))["modules"])
_prom = (REPO / "docker/monitoring/swarm/prometheus.yml.j2").read_text(encoding="utf-8")
checar('replacement: "tasks.monitoring_blackbox:9115"' in _prom, "o Prometheus raspa o blackbox pelo serviço do Swarm")
_bb_dash = (REPO / "docker/monitoring/swarm/dashboards/blackbox.json").read_text(encoding="utf-8")
_dash_mod = set(re.findall(r'job=\\"blackbox_([a-z0-9_]+)\\"', _bb_dash))
_dash_nomes = set(re.findall(r'nome=\\"([a-z0-9-]+)\\"', _bb_dash))
_dash_grupos = set(re.findall(r'grupo=\\"([a-z0-9-]+)\\"', _bb_dash))
checar(bool(_dash_mod) and _dash_mod <= _modulos, f"todo job blackbox_* do dashboard é um módulo do blackbox.yml ({sorted(_dash_mod - _modulos)})")

# http_auth sonda API que exige chave: 401 é "no ar", e só 401 — aceitar 404/5xx
# faria API quebrada passar por saudável; sem fail_if_not_ssl, TLS quebrado também.
_http_auth = (yaml.safe_load((REPO / "docker/monitoring/swarm/blackbox.yml").read_text(encoding="utf-8"))["modules"].get("http_auth") or {}).get("http") or {}
checar(_http_auth.get("valid_status_codes") == [401], f"http_auth aceita só 401 (veio {_http_auth.get('valid_status_codes')!r})")
checar(_http_auth.get("fail_if_not_ssl") is True and _http_auth.get("follow_redirects") is False,
       "http_auth exige TLS e não segue redirect")

# Painel cego não pode parecer saudável: exporter/scrape caído tem de aparecer.
checar('min(up{job=~\\"blackbox_.*\\"})' in _bb_dash, "dashboard do blackbox tem o stat da própria sonda (up dos jobs)")
checar("or vector(0)" not in _bb_dash, "dashboard do blackbox não converte sonda parada em 0 verde (or vector(0))")


def _alvos_validos(alvos, origem):
    checar(all({"nome", "grupo", "modulo", "alvo"} <= set(a) for a in alvos), f"{origem}: todo alvo tem nome, grupo, modulo e alvo")
    checar({a.get("modulo") for a in alvos} <= _modulos, f"{origem}: todo módulo existe no blackbox.yml")
    checar({a.get("grupo") for a in alvos} <= _dash_grupos, f"{origem}: todo grupo é um dos do dashboard ({sorted(_dash_grupos)})")
    checar(_dash_nomes <= {a.get("nome") for a in alvos}, f"{origem}: tem os nomes que o dashboard compara ({sorted(_dash_nomes)})")
    nomes = [a.get("nome") for a in alvos]
    checar(len(nomes) == len(set(nomes)), f"{origem}: nome de alvo sem duplicata")


_padrao = _comum.get("blackbox_alvos", [])
_alvos_validos(_padrao, "blackbox_alvos padrão")
# O padrão é público: só RFC 5737 e example.com, nunca alvo real.
checar(all(re.fullmatch(r"(192\.0\.2|198\.51\.100|203\.0\.113)\.\d+(:\d+)?|https://([a-z0-9-]+\.)?example\.com/?", a["alvo"])
           for a in _padrao), "blackbox_alvos padrão só com endereço de documentação")
# A lista real é local (gitignorada); quando existe, segue o mesmo contrato.
_local = ANSIBLE / "inventories/prod/group_vars/all/99-local.yml"
if _local.exists():
    _real = (yaml.safe_load(_local.read_text(encoding="utf-8")) or {}).get("blackbox_alvos")
    if _real is not None:
        _alvos_validos(_real, "blackbox_alvos local")

# Painel "o que está fora", fase 2 (json_exporter, status pages oficiais).
_je = servicos.get("json_exporter") or {}
checar(re.fullmatch(r"quay\.io/prometheuscommunity/json-exporter:v\d+\.\d+\.\d+", _je.get("image", "")) is not None,
       f"json_exporter com tag de versão fixa (veio {_je.get('image')!r})")
_je_mount = "/srv/containers/json_exporter/json_exporter.yml:/config.yml:ro"
checar(_je_mount in (_je.get("volumes") or []), "json_exporter monta o arquivo de módulos somente-leitura")
checar(f"--config.file={_je_mount.split(':')[1]}" in (_je.get("command") or []),
       "json_exporter lê o arquivo no mesmo caminho em que o volume o monta (--config.file)")
def _bind_local(v):
    """Sintaxe curta (`/srv/containers/x:/y`) ou longa (`type: bind`, `source: /srv/containers/x`)."""
    origem = v if isinstance(v, str) else (v.get("source") if isinstance(v, dict) else None)
    return isinstance(origem, str) and origem.startswith("/srv/containers/")


for _nome, _svc in servicos.items():
    if any(_bind_local(v) for v in (_svc.get("volumes") or [])):
        checar("node.role == manager" in (((_svc.get("deploy") or {}).get("placement") or {}).get("constraints") or []),
               f"{_nome}: bind em /srv/containers (disco local, entregue só nos managers) exige node.role == manager")
checar(any(f.get("src") == "json_exporter.yml" and f.get("dest") == _je_mount.split(":")[0]
           for f in _comum.get("monitoring_sidecar_files", [])),
       "json_exporter.yml é entregue pelo playbook no caminho que o compose monta")
checar('replacement: "tasks.monitoring_json_exporter:7979"' in _prom, "o Prometheus raspa o json_exporter pelo serviço do Swarm")
_je_cfg = yaml.safe_load((REPO / "docker/monitoring/swarm/json_exporter.yml").read_text(encoding="utf-8"))["modules"]
checar(set(_je_cfg) == {"status", "componentes", "cstate"}, f"módulos do json_exporter.yml ({sorted(_je_cfg)})")
# O json_exporter nomeia a série `<name>_<chave de values>`: derivado das chaves, não cravado.
_je_metricas = {f"{m['name']}_{k}" for mod in _je_cfg.values() for m in mod["metrics"] for k in m["values"]}
_dash_exprs = [t["expr"] for pn in json.loads(_bb_dash)["panels"] for t in pn.get("targets", [])]
_dash_metricas = {m for e in _dash_exprs for m in re.findall(r'(?<!")\b((?:statuspage|cstate)_[A-Za-z0-9_]*[A-Za-z0-9])\b', e)}
checar(bool(_dash_metricas) and _dash_metricas <= _je_metricas,
       f"toda métrica de status page do dashboard sai do json_exporter.yml ({sorted(_dash_metricas - _je_metricas)})")
checar(_je_metricas <= _dash_metricas,
       f"toda métrica do json_exporter.yml é usada pelo dashboard ({sorted(_je_metricas - _dash_metricas)})")
# Valor de rótulo que o dashboard compara tem de existir no enum de quem o publica.
_enum_ind = {"none", "maintenance", "minor", "major", "critical"}
_enum_sum = {"ok", "notice", "disrupted", "down"}
_ind = {v for e in _dash_exprs for v in re.findall(r'indicator="([^"]*)"', e)}
_sum = {v for e in _dash_exprs for v in re.findall(r'summary="([^"]*)"', e)}
checar(_ind == _enum_ind, f"o dashboard trata todo indicator do Statuspage e só eles (faltam {sorted(_enum_ind - _ind)}, sobram {sorted(_ind - _enum_ind)})")
checar(_sum == _enum_sum, f"o dashboard trata todo summary do cState e só eles (faltam {sorted(_enum_sum - _sum)}, sobram {sorted(_sum - _enum_sum)})")
# Mapeamento dos painéis 19 e 20: o 5 (DESCONHECIDO) nunca pode parecer saudável, e "sem dado" tem de estar mapeado.
for _pid in (19, 20):
    _pn = next(p for p in json.loads(_bb_dash)["panels"] if p["id"] == _pid)
    _map = _pn["fieldConfig"]["defaults"]["mappings"]
    _vals = next((m["options"] for m in _map if m["type"] == "value"), {})
    checar(_vals.get("0", {}).get("color") == "green" and _vals.get("0", {}).get("text") == "OK",
           f"painel {_pid}: severidade 0 é OK em verde")
    checar(_vals.get("5", {}).get("color") == "purple" and _vals.get("5", {}).get("text") == "DESCONHECIDO",
           f"painel {_pid}: severidade 5 é DESCONHECIDO em roxo, nunca verde")
    checar(all(_vals.get(str(k), {}).get("color") not in (None, "green") for k in (1, 2, 3, 4, 5)),
           f"painel {_pid}: nenhuma severidade acima de 0 é verde")
    _esp = {"0": ("OK", "green"), "1": ("MANUTENÇÃO / AVISO", "blue"), "2": ("DEGRADADO", "yellow"),
            "3": ("FORA PARCIAL", "orange"), "4": ("FORA", "red"), "5": ("DESCONHECIDO", "purple")}
    checar({k: (v.get("text"), v.get("color")) for k, v in _vals.items()} == _esp,
           f"painel {_pid}: mapeamento 0-5 exato (texto e cor)")
    checar(len(_pn["targets"]) == 2, f"painel {_pid}: exatamente dois alvos ({len(_pn['targets'])})")
    checar(any(m["type"] == "special" and m["options"].get("match") == "null" for m in _map),
           f"painel {_pid}: `sem dado` (null) está mapeado")
    _cores = [st["color"] for st in _pn["fieldConfig"]["defaults"]["thresholds"]["steps"]]
    checar(_cores == ["green", "blue", "yellow", "orange", "red", "purple"],
           f"painel {_pid}: degraus de cor do mais brando ao DESCONHECIDO ({_cores})")
# Instância sem indicador conhecido (HTML 200, formato novo, alvo parado) não pode sumir do painel.
for _job, _met in (("statuspage_status", "statuspage_status_info"), ("statuspage_cstate", "cstate_summary_info")):
    _ex = [e for e in _dash_exprs if _met in e]
    checar(bool(_ex) and all(f'up{{job="{_job}"}}' in e and "* 0 + 5" in e for e in _ex),
           f"dashboard: toda expressão de {_met} tem o termo DESCONHECIDO (up do job)")
checar('min(up{job=~\\"statuspage_.*\\"})' in _bb_dash, "dashboard tem o stat da própria sonda das status pages (up dos jobs)")
_sufixo = {"status": "/api/v2/status.json", "componentes": "/api/v2/components.json", "cstate": "/index.json"}


def _statuspage_validos(alvos, origem):
    checar(len(alvos) >= 1, f"{origem}: tem ao menos um alvo (lista vazia passaria por vacuidade)")
    checar(all({"nome", "modulo", "alvo"} <= set(a) for a in alvos), f"{origem}: todo alvo tem nome, modulo e alvo")
    checar({a.get("modulo") for a in alvos} <= set(_je_cfg), f"{origem}: todo módulo existe no json_exporter.yml")
    pares = [(a.get("modulo"), a.get("nome")) for a in alvos]
    checar(len(pares) == len(set(pares)), f"{origem}: nome de alvo sem duplicata dentro do módulo")
    checar(all(a.get("alvo", "").startswith("https://") and a["alvo"].endswith(_sufixo.get(a.get("modulo"), "\0"))
               for a in alvos),
           f"{origem}: alvo em https e com o caminho do seu módulo (o json_exporter não segue redirect: URL final)")


_sp_padrao = _comum.get("statuspage_alvos", [])
_statuspage_validos(_sp_padrao, "statuspage_alvos padrão")
checar(all(re.match(r"https://([a-z0-9-]+\.)?example\.com/", a["alvo"]) for a in _sp_padrao),
       "statuspage_alvos padrão só com example.com")
if _local.exists():
    _sp_real = (yaml.safe_load(_local.read_text(encoding="utf-8")) or {}).get("statuspage_alvos")
    if _sp_real is not None:
        _statuspage_validos(_sp_real, "statuspage_alvos local")
        # Componente por página só onde cabe: página com centenas de componentes usa `status`.
        checar(len([a for a in _sp_real if a["modulo"] == "componentes"]) <= 8,
               "statuspage_alvos local: poucos alvos no módulo `componentes` (páginas pequenas, sem grupos)")

if falhas:
    sys.exit(f"\n✗ gates-monitoring: {len(falhas)} falha(s)")
print("\n✓ gates-monitoring")
