#!/usr/bin/env bash
#
# collect.sh — coleta as configurações REAIS de produção, separadas por host.
#
# Existe porque os arquivos de config em docker/monitoring/swarm/ nunca foram
# implantados por automação nenhuma: o prometheus.yml do repo tem sintaxe Alloy
# River, não YAML do Prometheus. O arquivo que o Prometheus lê hoje no host é
# outro e não existe no git. Templatar a partir da cópia do repo seria templatar
# ficção — daí a coleta antes da Fase 3.
#
# Os hosts NÃO são hardcoded: saem do inventário do Ansible. Isso mantém IP real
# fora de arquivo rastreado (o repositório é público) e faz o mesmo script servir
# prod e dev.
#
# Uso:
#   ./collect.sh                          # tudo, inventário de prod
#   ./collect.sh -i ../IaC/ansible/inventories/dev/hosts.ini
#   ./collect.sh -l node1                 # só um host
#   ./collect.sh -g proxmox,omv           # só alguns grupos
#   ./collect.sh -n                       # dry-run: mostra o que faria
#   ./collect.sh --no-portainer           # pula a parte de API
#
# Requisitos: ansible-inventory, ssh, tar, python3. Acesso SSH por chave aos
# hosts, com sudo NOPASSWD nos nós do Swarm (o 00-node-bootstrap.yml configura).
#
# ⚠️ O que este script traz é dado REAL: senhas em texto puro no Env das stacks
# do Portainer, hashes de senha e chaves no config.xml da OPNsense, IPs e
# hostnames internos. A saída vai para `.local/snapshot/`, fora do controle de
# versão; o script se recusa a rodar se esse caminho não estiver ignorado.

set -euo pipefail

# O script vive em prod-snapshot/ (versionado), mas escreve em .local/snapshot/
# (ignorado). Separar os dois é o que permite versionar a ferramenta sem nunca
# arriscar versionar o que ela coleta.
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$BASE/.." && pwd)"
SNAP="$REPO/.local/snapshot"
mkdir -p "$SNAP"

INVENTORY="$REPO/IaC/ansible/inventories/prod/hosts.ini"
ONLY_HOSTS=""
ONLY_GROUPS=""
DRY_RUN=0
DO_PORTAINER=1
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)

usage() { sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--inventory)   INVENTORY="$2"; shift 2 ;;
    -l|--limit)       ONLY_HOSTS="$2"; shift 2 ;;
    -g|--groups)      ONLY_GROUPS="$2"; shift 2 ;;
    -n|--dry-run)     DRY_RUN=1; shift ;;
    --no-portainer)   DO_PORTAINER=0; shift ;;
    -h|--help)        usage ;;
    *) echo "opção desconhecida: $1 (use --help)" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Guardas
# ---------------------------------------------------------------------------

# Guarda mais importante do script: se `.local/` deixar de ser ignorada, o
# próximo `git add -A` vaza tudo isto num repositório PÚBLICO. Falha antes de
# escrever o primeiro byte, não depois.
if ! git -C "$REPO" check-ignore -q "$SNAP/.canary"; then
  echo "ABORTADO: $SNAP não está sendo ignorada pelo git." >&2
  echo "Conserte o .gitignore (linha '.local/') antes de coletar." >&2
  exit 1
fi

command -v ansible-inventory >/dev/null || { echo "ABORTADO: ansible-inventory não encontrado." >&2; exit 1; }
[[ -f "$INVENTORY" ]] || { echo "ABORTADO: inventário não encontrado: $INVENTORY" >&2; exit 1; }

LOG="$SNAP/MANIFEST.txt"
STARTED="$(date -Is)"

say()  { printf '%s\n' "$*"; }
note() { (( DRY_RUN )) || printf '%s\n' "$*" >> "$LOG"; }
ok()   { printf '  OK       %s\n' "$1"; note "OK       $1"; }
miss() { printf '  AUSENTE  %s\n' "$1"; note "AUSENTE  $1"; }
err()  { printf '  ERRO     %s (%s)\n' "$1" "$2"; note "ERRO     $1 ($2)"; }

# ---------------------------------------------------------------------------
# Inventário -> hosts
# ---------------------------------------------------------------------------
# Uma linha por host: nome<TAB>endereço<TAB>usuário<TAB>grupo1,grupo2
# `ansible_host` pode não existir (aí o próprio nome é o endereço).

read_inventory() {
  ansible-inventory -i "$INVENTORY" --list 2>/dev/null | python3 -c '
import json, sys

data = json.load(sys.stdin)
hostvars = data.get("_meta", {}).get("hostvars", {})

# Um host pertence a um grupo se aparece em group["hosts"]. Grupos-pai
# ("children") são resolvidos para que `-g network` pegue opnsense/openwrt.
direct = {}
for group, body in data.items():
    if group == "_meta":
        continue
    for host in body.get("hosts", []) or []:
        direct.setdefault(host, set()).add(group)

children = {g: set(b.get("children", []) or []) for g, b in data.items() if g != "_meta"}

def expand(host, groups):
    out = set(groups)
    changed = True
    while changed:
        changed = False
        for parent, kids in children.items():
            if parent not in out and (kids & out):
                out.add(parent)
                changed = True
    return out

for host, groups in sorted(direct.items()):
    hv = hostvars.get(host, {})
    addr = hv.get("ansible_host") or host
    user = hv.get("ansible_user") or "root"
    allg = sorted(expand(host, groups) - {"all", "ungrouped"})
    print("\t".join([host, str(addr), str(user), ",".join(allg)]))
'
}

in_list() { # in_list <agulha> <lista-separada-por-virgula>
  local needle="$1" list="$2" item
  [[ -z "$list" ]] && return 0
  IFS=',' read -ra _items <<< "$list"
  for item in "${_items[@]}"; do [[ "$needle" == "$item" ]] && return 0; done
  return 1
}

has_group() { # has_group <grupo> <grupos-do-host>
  local needle="$1" groups="$2" item
  IFS=',' read -ra _g <<< "$groups"
  for item in "${_g[@]}"; do [[ "$needle" == "$item" ]] && return 0; done
  return 1
}

# ---------------------------------------------------------------------------
# Primitivas de coleta
# ---------------------------------------------------------------------------
# Por que não `scp` puro: os nós do Swarm são acessados como usuário comum e
# /srv/containers é do root. scp não escala privilégio. `ssh sudo tar` resolve
# os dois casos (arquivo e diretório) e preserva permissão e dono, que é
# exatamente o que a tabela de ownership da Fase 3.3 precisa comparar.

SUDO=""    # definido por host no loop principal
SSH_OK=1   # idem; 0 quando o host só é alcançável por API

remote_sh() { # remote_sh <user@addr> <comando>
  ssh "${SSH_OPTS[@]}" -n "$1" "$2"
}

fetch_path() { # fetch_path <user@addr> <caminho-remoto> <destino-local-raiz> <rótulo>
  local target="$1" path="$2" dest="$3" label="$4"
  if (( DRY_RUN )); then say "  [dry-run] $label  <- $path"; return 0; fi

  if ! remote_sh "$target" "$SUDO test -e '$path'" 2>/dev/null; then
    miss "$label"; return 0
  fi
  mkdir -p "$dest"
  # --warning=no-file-changed silencia o ruído do GNU tar em arquivo de log que
  # muda durante a leitura, mas é flag exclusiva do GNU: o BusyBox do OpenWrt
  # aborta com ela. Tentar a versão GNU e cair para a portátil.
  local gnu="$SUDO tar -C / --warning=no-file-changed -cf - '${path#/}' 2>/dev/null"
  local busybox="$SUDO tar -C / -cf - '${path#/}' 2>/dev/null"
  if ssh "${SSH_OPTS[@]}" -n "$target" "$gnu" | tar -C "$dest" -xf - 2>/dev/null; then
    ok "$label"
  elif ssh "${SSH_OPTS[@]}" -n "$target" "$busybox" | tar -C "$dest" -xf - 2>/dev/null; then
    ok "$label (tar portátil)"
  else
    err "$label" "falha no tar/ssh"
  fi
}

capture_cmd() { # capture_cmd <user@addr> <comando> <arquivo-destino> <rótulo>
  local target="$1" cmd="$2" out="$3" label="$4"
  if (( DRY_RUN )); then say "  [dry-run] $label  <- \$($cmd)"; return 0; fi
  mkdir -p "$(dirname "$out")"
  if remote_sh "$target" "$cmd" > "$out" 2>/dev/null && [[ -s "$out" ]]; then
    ok "$label"
  else
    rm -f "$out"; miss "$label"
  fi
}

# ---------------------------------------------------------------------------
# Módulos por papel de host
# ---------------------------------------------------------------------------

collect_swarm_node() { # <target> <dir-do-host> <é-manager 0|1>
  local target="$1" hdir="$2" is_manager="$3"

  # 1. Configs sidecar montadas nos containers. Estes são os arquivos que a
  #    Fase 3 vai templatar — o conteúdo real deles é o objetivo da coleta.
  local f
  for f in prometheus/prometheus.yml grafana/datasource.yml grafana/datasources.yml \
           grafana/provisioning loki/loki-config.yml alloy/config.alloy \
           traefik/config traefik/traefik.yml; do
    fetch_path "$target" "/srv/containers/$f" "$hdir/srv-containers" "srv/containers/$f"
  done

  # 2. Qual caminho é ARQUIVO e qual virou DIRETÓRIO. O Docker materializa
  #    diretório quando o bind mount aponta para caminho inexistente. Onde isso
  #    já aconteceu, o módulo `template` da Fase 3 falha com "Destination is a
  #    directory" — e só se descobre isso olhando.
  capture_cmd "$target" \
    "$SUDO file /srv/containers/prometheus/prometheus.yml /srv/containers/grafana/datasource*.yml /srv/containers/loki/loki-config.yml /srv/containers/alloy/config.alloy 2>&1" \
    "$hdir/FILE-TYPES.txt" "FILE-TYPES.txt"

  # 3. Ownership real, para conferir contra a tabela de UIDs da Fase 3.3.
  capture_cmd "$target" \
    "$SUDO stat -c '%n %U:%G %a' /srv/containers/*/ /srv/containers/*/* 2>/dev/null" \
    "$hdir/OWNERSHIP.txt" "OWNERSHIP.txt"

  # 4. rsyslog do host: quem manda syslog para o Alloy na 1514.
  fetch_path "$target" "/etc/rsyslog.d" "$hdir/etc" "etc/rsyslog.d"

  # 5. Estado do Docker/Swarm.
  capture_cmd "$target" "docker node ls 2>&1"    "$hdir/docker/node-ls.txt"    "docker/node-ls.txt"
  capture_cmd "$target" "docker stack ls 2>&1"   "$hdir/docker/stack-ls.txt"   "docker/stack-ls.txt"
  capture_cmd "$target" "docker service ls 2>&1" "$hdir/docker/service-ls.txt" "docker/service-ls.txt"
  capture_cmd "$target" "docker network ls 2>&1" "$hdir/docker/network-ls.txt" "docker/network-ls.txt"
  capture_cmd "$target" "docker network inspect traefik_public 2>&1" \
    "$hdir/docker/traefik_public.json" "docker/traefik_public.json"

  # As portas realmente publicadas por serviço. Confere se o `ports:` que o repo
  # declara é o que está no ar, e em que modo (ingress vs host).
  capture_cmd "$target" \
    "docker service ls --format '{{.Name}}' 2>/dev/null | while read -r s; do docker service inspect \"\$s\" --format '{{.Spec.Name}} {{json .Endpoint.Ports}}'; done" \
    "$hdir/docker/published-ports.txt" "docker/published-ports.txt"

  # Regras do Traefik como o Swarm as enxerga. É a prova direta de Host("")
  # quando o env não foi injetado.
  capture_cmd "$target" \
    "docker service ls --format '{{.Name}}' 2>/dev/null | while read -r s; do docker service inspect \"\$s\" --format '{{.Spec.Name}} {{json .Spec.Labels}}'; done" \
    "$hdir/docker/service-labels.txt" "docker/service-labels.txt"

  capture_cmd "$target" "$SUDO ufw status numbered 2>&1" "$hdir/ufw-status.txt" "ufw-status.txt"

  (( is_manager )) || return 0
  capture_cmd "$target" "docker info --format '{{.Swarm.Cluster.ID}} {{.Swarm.LocalNodeState}}'" \
    "$hdir/docker/swarm-id.txt" "docker/swarm-id.txt"
}

collect_proxmox() { # <target> <dir-do-host>
  local target="$1" hdir="$2" p
  for p in /etc/network/interfaces /etc/hostname /etc/hosts \
           /etc/pve/storage.cfg /etc/pve/datacenter.cfg /etc/pve/user.cfg \
           /etc/pve/corosync.conf; do
    fetch_path "$target" "$p" "$hdir" "${p#/}"
  done
  # /etc/pve/{lxc,qemu-server} são symlinks para nodes/<nó>/... — seguir o link
  # real evita trazer o texto do symlink, que foi o que aconteceu antes.
  capture_cmd "$target" "cat /etc/pve/nodes/*/lxc/*.conf 2>/dev/null" \
    "$hdir/pve-lxc-configs.txt" "pve-lxc-configs.txt"
  capture_cmd "$target" "cat /etc/pve/nodes/*/qemu-server/*.conf 2>/dev/null" \
    "$hdir/pve-qemu-configs.txt" "pve-qemu-configs.txt"
  capture_cmd "$target" "pvesh get /cluster/resources --output-format json 2>/dev/null" \
    "$hdir/cluster-resources.json" "cluster-resources.json"
  capture_cmd "$target" "pvesh get /nodes --output-format json 2>/dev/null" \
    "$hdir/nodes.json" "nodes.json"
  capture_cmd "$target" "pveversion -v 2>&1" "$hdir/pveversion.txt" "pveversion.txt"
}

collect_omv() { # <target> <dir-do-host>
  local target="$1" hdir="$2"
  fetch_path "$target" "/etc/openmediavault/config.xml" "$hdir" "etc/openmediavault/config.xml"
  fetch_path "$target" "/etc/samba/smb.conf"            "$hdir" "etc/samba/smb.conf"
  fetch_path "$target" "/etc/exports"                   "$hdir" "etc/exports"
  fetch_path "$target" "/etc/fstab"                     "$hdir" "etc/fstab"
  capture_cmd "$target" "$SUDO blkid 2>/dev/null"  "$hdir/blkid.txt"  "blkid.txt"
  capture_cmd "$target" "$SUDO lsblk -f 2>/dev/null" "$hdir/lsblk.txt" "lsblk.txt"
}

collect_opnsense() { # <target> <dir-do-host>
  local target="$1" hdir="$2"

  # Caminho SSH. Só roda se o host respondeu — na configuração recomendada o
  # firewall não tem chave nenhuma instalada e tudo vem por API.
  if (( SSH_OK )); then
    capture_cmd "$target" "ifconfig -a 2>/dev/null"         "$hdir/ifconfig.txt" "ifconfig.txt"
    capture_cmd "$target" "opnsense-version -v 2>/dev/null" "$hdir/version.txt"  "version.txt"

    # /conf/config.xml é 0600 root:wheel e `pfctl` exige root. O OpenSense não
    # traz sudo (precisa do plugin os-sudo), então como usuário nomeado esses
    # dois são inalcançáveis por SSH — e reportá-los como "AUSENTE" mentiria:
    # eles existem, só não podem ser lidos por aqui. Vêm pela API.
    if [[ -z "$SUDO" ]]; then
      fetch_path "$target" "/conf/config.xml" "$hdir" "conf/config.xml"
      capture_cmd "$target" "pfctl -sr 2>/dev/null" "$hdir/pf-rules.txt" "pf-rules.txt"
    else
      say "  (config.xml e pfctl exigem root; virão pela API)"
    fi
  else
    say "  (sem SSH — usando só a API)"
  fi

  # Sem credencial de API não há config.xml: por SSH ele é 0600 root:wheel e o
  # OPNsense não traz sudo. Perguntar em vez de sair calado — a versão anterior
  # deste script retornava em silêncio quando o SSH funcionava, e o operador só
  # descobria pelo "AUSENTE conf/config.xml" no fim.
  if [[ -z "${OPNSENSE_KEY:-}" || -z "${OPNSENSE_SECRET:-}" ]]; then
    if (( DRY_RUN )); then
      say "  (sem OPNSENSE_KEY/OPNSENSE_SECRET — o bloco de API seria pulado)"
      return 0
    fi
    say "  Sem OPNSENSE_KEY/OPNSENSE_SECRET no ambiente."
    say "  Crie em System > Access > Users > seu usuário > API keys. Enter vazio pula."
    [[ -t 0 ]] || { err "opnsense" "sem credencial de API e sem terminal para perguntar"; return 0; }
    read -rsp "  API key: "    OPNSENSE_KEY;    echo
    read -rsp "  API secret: " OPNSENSE_SECRET; echo
    if [[ -z "$OPNSENSE_KEY" || -z "$OPNSENSE_SECRET" ]]; then
      err "opnsense" "sem credencial de API — config.xml NÃO foi coletado"
      return 0
    fi
  fi

  local url="${OPNSENSE_URL:-https://$OPN_ADDR}"
  say "  API: $url"

  # opn_api <rota> <arquivo> <rótulo>
  # A API do OPNsense mistura rotas GET e POST conforme o módulo, e o conjunto
  # muda entre versões. Tentar GET, cair para POST, e tratar rota inexistente
  # como AUSENTE em vez de erro — a ausência também é informação.
  opn_api() {
    local route="$1" out="$2" label="$3" code
    if (( DRY_RUN )); then say "  [dry-run] $label  <- $url/$route"; return 0; fi
    mkdir -p "$(dirname "$out")"
    code="$(curl -sk --max-time 45 -u "$OPNSENSE_KEY:$OPNSENSE_SECRET" \
              -o "$out" -w '%{http_code}' "$url/$route" || echo 000)"
    if [[ "$code" != "200" || ! -s "$out" ]]; then
      code="$(curl -sk --max-time 45 -u "$OPNSENSE_KEY:$OPNSENSE_SECRET" -X POST \
                -H 'Content-Type: application/json' -d '{}' \
                -o "$out" -w '%{http_code}' "$url/$route" || echo 000)"
    fi
    if [[ "$code" == "200" && -s "$out" ]]; then
      chmod 600 "$out"; ok "$label"
    else
      rm -f "$out"
      case "$code" in
        404) miss "$label" ;;
        401|403) err "$label" "HTTP $code — key/secret sem permissão" ;;
        000) err "$label" "sem resposta (URL/TLS?)" ;;
        *)   err "$label" "HTTP $code" ;;
      esac
    fi
  }

  # O backup devolve o config.xml inteiro — mesmo arquivo que viria por SSH.
  opn_api "api/core/backup/download/this" "$hdir/config.xml" "config.xml (API)"

  # Sem SSH, estas rotas são o que substitui arp/ifconfig/pfctl. O ARP é o que
  # mapeia a rede de hoje; as concessões DHCP completam com quem é dinâmico.
  opn_api "api/diagnostics/interface/getArp"        "$hdir/arp.json"        "arp.json"
  opn_api "api/diagnostics/interface/getInterfaceNames" "$hdir/ifnames.json" "ifnames.json"
  opn_api "api/diagnostics/interface/getInterfaceConfig" "$hdir/ifconfig.json" "ifconfig.json"
  opn_api "api/dhcpv4/leases/searchLease"           "$hdir/dhcp-leases.json" "dhcp-leases.json"
  opn_api "api/core/firmware/status"                "$hdir/firmware.json"   "firmware.json"
  opn_api "api/unbound/settings/get"                "$hdir/unbound.json"    "unbound.json"
  opn_api "api/firewall/alias/searchItem"           "$hdir/aliases.json"    "aliases.json"

  unset -f opn_api
}

collect_rsyslog() { # <target> <dir-do-host>
  local target="$1" hdir="$2"

  # Coletor de syslog central. O desenho-alvo elimina este host: o Alloy passa a
  # receber syslog direto na 1514. O que está aqui é a referência do que não
  # pode se perder nessa troca — quem manda log, para qual arquivo, com qual
  # rotação. É também o que responde se `remote-logs.conf`/`rotate_log.sh`
  # (deletados do repositório) tinham função que o Alloy ainda não cobre.
  fetch_path "$target" "/etc/rsyslog.conf" "$hdir" "etc/rsyslog.conf"
  fetch_path "$target" "/etc/rsyslog.d"    "$hdir" "etc/rsyslog.d"
  fetch_path "$target" "/etc/logrotate.d"  "$hdir" "etc/logrotate.d"

  # Há um SEGUNDO Alloy aqui, fora do Swarm: binário em /usr/bin/alloy, unit
  # systemd ativa, escutando na 12345. É quase certo que `local.config.alloy` e
  # `container.config.alloy` (deletados do repositório como "arquitetura de log
  # duplicada") descreviam este host e o do Swarm, respectivamente — ou seja,
  # não eram código morto. Coletar antes de concluir qualquer coisa.
  fetch_path "$target" "/etc/alloy" "$hdir" "etc/alloy"
  capture_cmd "$target" "systemctl cat alloy 2>/dev/null" "$hdir/alloy.service" "alloy.service"

  # Que árvore de log existe de fato — o config.alloy do repositório aponta para
  # /var/log/rsyslog/{opnsense,openwrt,omv,pve}/*.log e ninguém tinha conferido
  # se esses caminhos batem.
  capture_cmd "$target" "$SUDO find /var/log -maxdepth 3 -type d 2>/dev/null | sort" \
    "$hdir/log-tree.txt" "log-tree.txt"
  capture_cmd "$target" "$SUDO du -sh /var/log/* 2>/dev/null | sort -h" \
    "$hdir/log-sizes.txt" "log-sizes.txt"

  # Em que portas ele escuta hoje (514/udp, 514/tcp, 1514?) — define para onde
  # os dispositivos precisam ser reapontados na migração.
  capture_cmd "$target" "$SUDO ss -lntup 2>/dev/null || $SUDO netstat -lntup 2>/dev/null" \
    "$hdir/listening-ports.txt" "listening-ports.txt"
  capture_cmd "$target" "$SUDO rsyslogd -N1 2>&1" "$hdir/rsyslogd-check.txt" "rsyslogd-check.txt"
}

collect_openwrt() { # <target> <dir-do-host>
  local target="$1" hdir="$2"
  # OpenWrt não tem Python nem sudo por padrão; o acesso já é root e o tar do
  # BusyBox aceita a mesma sintaxe.
  fetch_path "$target" "/etc/config" "$hdir" "etc/config"
  capture_cmd "$target" "ubus call system board 2>/dev/null" "$hdir/board.json" "board.json"
  capture_cmd "$target" "iwinfo 2>/dev/null"                 "$hdir/iwinfo.txt" "iwinfo.txt"
  capture_cmd "$target" "logread -l 50 2>/dev/null"          "$hdir/logread.txt" "logread.txt"
}

# ---------------------------------------------------------------------------
# Portainer (API) — o item de maior valor da coleta
# ---------------------------------------------------------------------------
# stacks.json é onde vivem os FQDNs reais e os segredos digitados no formulário
# da stack: eles só existem no BoltDB em /srv/containers/portainer/, não estão
# no git e não têm rotação. É essa lista que fecha o conteúdo do vault.

collect_portainer() { # <endereço-do-manager>
  local addr="$1" dir="$SNAP/_portainer"
  local url="${PORTAINER_URL:-http://$addr:9000}"

  if (( DRY_RUN )); then say "  [dry-run] _portainer/{stacks,endpoints}.json <- $url/api"; return 0; fi

  local pw jwt
  if [[ -n "${PORTAINER_PASSWORD:-}" ]]; then
    pw="$PORTAINER_PASSWORD"
  else
    read -rsp "Senha do admin do Portainer ($url), vazio para pular: " pw; echo
  fi
  [[ -z "$pw" ]] && { say "  (pulado)"; note "PULADO   portainer (sem senha)"; return 0; }

  jwt="$(curl -sk --max-time 20 "$url/api/auth" \
          -H 'Content-Type: application/json' \
          -d "$(python3 -c 'import json,sys;print(json.dumps({"Username":"admin","Password":sys.argv[1]}))' "$pw")" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin).get("jwt",""))' 2>/dev/null || true)"
  pw=""; unset pw

  [[ -z "$jwt" ]] && { err "portainer" "login falhou"; return 0; }

  mkdir -p "$dir"
  local ep
  for ep in stacks endpoints; do
    if curl -sk --max-time 30 -H "Authorization: Bearer $jwt" "$url/api/$ep" \
       | python3 -m json.tool > "$dir/$ep.json" 2>/dev/null; then
      chmod 600 "$dir/$ep.json"; ok "_portainer/$ep.json"
    else
      rm -f "$dir/$ep.json"; err "_portainer/$ep.json" "API recusou"
    fi
  done
  jwt=""; unset jwt

  # Resumo SEM valores: é isto que pode ser colado num chat ou issue.
  if [[ -s "$dir/stacks.json" ]]; then
    python3 - "$dir/stacks.json" > "$dir/env-keys.txt" <<'PY'
import json, sys
for s in json.load(open(sys.argv[1])):
    print(s.get("Name"), "->", sorted(e["name"] for e in (s.get("Env") or [])))
PY
    ok "_portainer/env-keys.txt (só nomes de chave, seguro para compartilhar)"
  fi
}

# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

(( DRY_RUN )) || : > "$LOG"
note "# coleta iniciada em $STARTED"
note "# inventário: ${INVENTORY#$REPO/}"
note ""

say "Inventário: ${INVENTORY#$REPO/}"
(( DRY_RUN )) && say "MODO DRY-RUN — nada será escrito."

MANAGER_ADDR=""
FOUND=0

while IFS=$'\t' read -r host addr user groups; do
  [[ -z "$host" ]] && continue
  in_list "$host" "$ONLY_HOSTS" || continue
  if [[ -n "$ONLY_GROUPS" ]]; then
    matched=0
    IFS=',' read -ra want <<< "$ONLY_GROUPS"
    for g in "${want[@]}"; do has_group "$g" "$groups" && matched=1 && break; done
    (( matched )) || continue
  fi

  FOUND=1
  target="$user@$addr"
  hdir="$SNAP/$host"
  say ""
  say "== $host ($groups) via $user@… =="
  note ""
  note "## $host [$groups]"

  # OpenWrt e OPNsense já entram como root; nos nós Debian o acesso é usuário
  # comum e /srv/containers é do root.
  if [[ "$user" == "root" ]]; then SUDO=""; else SUDO="sudo -n"; fi

  # SSH indisponível não é necessariamente fim de linha: a OPNsense é coletada
  # por API justamente porque o desenho-alvo não instala chave nenhuma nela
  # (root desabilitado, permitrootlogin 0). Para os demais papéis não há
  # alternativa, então aí sim o host é pulado.
  SSH_OK=1
  if (( ! DRY_RUN )) && ! remote_sh "$target" true 2>/dev/null; then
    SSH_OK=0
    if has_group opnsense "$groups" && [[ -n "${OPNSENSE_KEY:-}" && -n "${OPNSENSE_SECRET:-}" ]]; then
      say "  SSH inacessível — seguindo pela API"
      note "AVISO    $host: SSH inacessível, coletado por API"
    else
      err "$host" "SSH inacessível"; continue
    fi
  fi

  has_group homelab_managers "$groups" && { MANAGER_ADDR="$addr"; collect_swarm_node "$target" "$hdir" 1; }
  has_group homelab_workers  "$groups" && collect_swarm_node "$target" "$hdir" 0
  has_group proxmox          "$groups" && collect_proxmox    "$target" "$hdir"
  has_group omv              "$groups" && collect_omv        "$target" "$hdir"
  has_group openwrt          "$groups" && collect_openwrt    "$target" "$hdir"
  has_group rsyslog          "$groups" && collect_rsyslog    "$target" "$hdir"
  if has_group opnsense "$groups"; then OPN_ADDR="$addr"; collect_opnsense "$target" "$hdir"; fi
done < <(read_inventory)

if (( ! FOUND )); then
  say ""
  say "Nenhum host casou com os filtros. Grupos disponíveis:"
  read_inventory | cut -f1,4 | sed 's/^/  /'
  exit 1
fi

if (( DO_PORTAINER )); then
  say ""
  say "== Portainer (API) =="
  note ""
  note "## _portainer"
  if [[ -n "$MANAGER_ADDR" ]]; then
    collect_portainer "$MANAGER_ADDR"
  else
    say "  (nenhum host em homelab_managers nesta seleção — defina PORTAINER_URL e rode com -g homelab_managers)"
  fi
fi

# ---------------------------------------------------------------------------
# Fecho
# ---------------------------------------------------------------------------

if (( ! DRY_RUN )); then
  # Tudo que veio de produção é sensível por padrão. 600/700 em toda a coleta.
  find "$SNAP" -mindepth 1 -type f -exec chmod 600 {} + 2>/dev/null || true
  find "$SNAP" -mindepth 1 -type d -exec chmod 700 {} + 2>/dev/null || true

  note ""
  note "# coleta terminada em $(date -Is)"

  say ""
  say "Manifesto: ${LOG#$REPO/}"
  say "Resumo:  $(grep -c '^OK ' "$LOG" || true) OK, $(grep -c '^AUSENTE ' "$LOG" || true) ausentes, $(grep -c '^ERRO ' "$LOG" || true) erros"
fi

# Última guarda: prova de que nada disto entrou no controle de versão.
say ""
if [[ -n "$(git -C "$REPO" status --porcelain -- "$SNAP" 2>/dev/null)" ]]; then
  say "⚠️  ATENÇÃO: git status mostra arquivos em .local/. NÃO COMMITE."
  say "    Conserte o .gitignore antes de qualquer git add."
  exit 1
fi
say "✅ git status limpo — nada disto será commitado."
