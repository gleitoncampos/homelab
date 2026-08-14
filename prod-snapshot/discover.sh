#!/usr/bin/env bash
#
# discover.sh — descobre os IPs da rede e sugere as linhas do hosts.ini.
#
# Existe porque os grupos [proxmox] e [omv] do inventário estão vazios: sem eles
# os playbooks 00-* casam zero host e passam em silêncio, e o collect.sh pula os
# dois. Preencher à mão exige saber os endereços — este script os levanta.
#
# NÃO faz varredura às cegas por padrão. A OPNsense é o roteador de todas as
# VLANs: a tabela ARP dela já viu todo dispositivo que falou na rede, e isso
# cobre também as VLANs 10 e 20, que são ESTÁTICAS (dhcp_enabled: false em
# network.yml) e portanto invisíveis para qualquer consulta de concessão DHCP.
# Varredura ativa existe como modo alternativo (--sweep), para quando a OPNsense
# não estiver acessível.
#
# Uso:
#   ./discover.sh                    # ARP + DHCP da OPNsense, depois identifica
#   ./discover.sh --sweep            # varre as faixas do network.yml direto
#   ./discover.sh --sweep --only 10,20   # só estas VLANs
#   ./discover.sh --no-probe         # não abre conexão nenhuma, só lista o ARP
#
# ⚠️ Só aponte para a SUA rede. A saída tem IP, MAC e hostname reais: fica em
# prod-snapshot/_discovery/, que é gitignorado, e não pode ser commitada nem
# colada em chat/issue.

set -euo pipefail

SNAP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SNAP/.." && pwd)"
OUT="$SNAP/_discovery"

INVENTORY="$REPO/ansible/inventories/prod/hosts.ini"
NETWORK_VARS="$REPO/ansible/inventories/prod/group_vars/network.yml"
MODE="arp"
ONLY_VLANS=""
CIDR=""
PROBE=1
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--inventory) INVENTORY="$2"; shift 2 ;;
    --sweep)        MODE="sweep"; shift ;;
    --only)         ONLY_VLANS="$2"; shift 2 ;;
    --cidr)         CIDR="$2"; MODE="sweep"; shift 2 ;;
    --no-probe)     PROBE=0; shift ;;
    -h|--help)      sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "opção desconhecida: $1 (use --help)" >&2; exit 2 ;;
  esac
done

# Mesma guarda do collect.sh: se a pasta deixar de ser ignorada, isto vaza
# topologia interna num repositório PÚBLICO. Falha antes de escrever nada.
if ! git -C "$REPO" check-ignore -q "$SNAP/.canary"; then
  echo "ABORTADO: $SNAP não está sendo ignorada pelo git." >&2
  exit 1
fi
[[ -f "$NETWORK_VARS" ]] || { echo "ABORTADO: não achei $NETWORK_VARS" >&2; exit 1; }

mkdir -p "$OUT"

# ---------------------------------------------------------------------------
# Fonte 1: OPNsense (ARP + DHCP)
# ---------------------------------------------------------------------------

collect_from_opnsense() {
  local target
  target="$(ansible-inventory -i "$INVENTORY" --list 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
hosts = d.get("opnsense", {}).get("hosts", [])
if not hosts:
    sys.exit(1)
hv = d.get("_meta", {}).get("hostvars", {}).get(hosts[0], {})
print(f'"'"'{hv.get("ansible_user","root")}@{hv.get("ansible_host", hosts[0])}'"'"')
' 2>/dev/null)" || { echo "  grupo [opnsense] vazio no inventário — use --sweep"; return 1; }

  echo "  fonte: $target"
  if ! ssh "${SSH_OPTS[@]}" -n "$target" true 2>/dev/null; then
    echo "  SSH inacessível — use --sweep"
    return 1
  fi

  # FreeBSD: "? (198.51.100.2) at 00:00:5e:00:53:01 on vtnet0.10 expires in 1174 seconds"
  ssh "${SSH_OPTS[@]}" -n "$target" 'arp -an 2>/dev/null' > "$OUT/arp-raw.txt" || true
  ssh "${SSH_OPTS[@]}" -n "$target" 'ifconfig -a 2>/dev/null' > "$OUT/ifconfig-raw.txt" || true

  # Concessões DHCP: o caminho mudou entre ISC dhcpd, Dnsmasq e Kea conforme a
  # versão do OPNsense. Tentar os três e aceitar que nenhum exista — as VLANs de
  # infraestrutura são estáticas de qualquer forma.
  ssh "${SSH_OPTS[@]}" -n "$target" '
    cat /var/dhcpd/var/db/dhcpd.leases 2>/dev/null
    cat /var/db/kea/kea-leases4.csv 2>/dev/null
    cat /var/db/dnsmasq.leases 2>/dev/null
  ' > "$OUT/leases-raw.txt" 2>/dev/null || true

  [[ -s "$OUT/arp-raw.txt" ]] || { echo "  ARP vazio — use --sweep"; return 1; }
  echo "  $(grep -c ' at ' "$OUT/arp-raw.txt" || echo 0) entradas ARP"
}

# ---------------------------------------------------------------------------
# Fonte 2: varredura das faixas declaradas em network.yml
# ---------------------------------------------------------------------------
# Sem nmap: um connect() de TCP em portas conhecidas responde a mesma pergunta
# ("existe host aqui e o que ele serve") usando só a stdlib do Python. Só varre
# as faixas que o próprio repositório declara — nada de faixa arbitrária.

sweep_ranges() {
  python3 - "$NETWORK_VARS" "$ONLY_VLANS" "$CIDR" > "$OUT/sweep-raw.txt" <<'PY'
import ipaddress, socket, sys, yaml
from concurrent.futures import ThreadPoolExecutor

# --cidr existe porque network.yml descreve a rede ALVO (o desenho de VLANs que
# a refatoração vai criar), não a de hoje. Para levantar o que já existe é
# preciso apontar para a faixa atual, que ainda é plana.
explicit = [c.strip() for c in sys.argv[3].split(",") if c.strip()] if len(sys.argv) > 3 else []
vlans = [] if explicit else (yaml.safe_load(open(sys.argv[1])).get("vlans", []) or [])
only = {t.strip() for t in sys.argv[2].split(",") if t.strip()}
PORTS = [22, 80, 443, 8006, 9000, 2377, 445]

def alive(ip):
    for port in PORTS:
        try:
            with socket.create_connection((str(ip), port), timeout=0.4):
                return str(ip)
        except OSError:
            continue
    return None

targets = []
for cidr in explicit:
    targets += list(ipaddress.ip_network(cidr, strict=False).hosts())
for v in vlans:
    if only and str(v.get("tag")) not in only:
        continue
    net = ipaddress.ip_network(f"{v['ip']}/{v.get('subnet', 24)}", strict=False)
    targets += list(net.hosts())

sys.stderr.write(f"  varrendo {len(targets)} endereços...\n")
with ThreadPoolExecutor(max_workers=128) as pool:
    for found in pool.map(alive, targets):
        if found:
            print(found)
PY
  echo "  $(wc -l < "$OUT/sweep-raw.txt") hosts responderam"
}

# ---------------------------------------------------------------------------
# Identificação e sugestão de inventário
# ---------------------------------------------------------------------------

echo "Descoberta de rede — $(date -Is)"
case "$MODE" in
  arp)   echo "Fonte: tabela ARP + DHCP da OPNsense"; collect_from_opnsense || { echo; echo "Caindo para varredura ativa."; MODE=sweep; sweep_ranges; } ;;
  sweep) echo "Fonte: varredura ativa das faixas de network.yml"; sweep_ranges ;;
esac

echo
echo "Identificando papéis..."

python3 - "$OUT" "$NETWORK_VARS" "$INVENTORY" "$PROBE" <<'PY'
import ipaddress, json, os, re, socket, subprocess, sys, yaml
from concurrent.futures import ThreadPoolExecutor

out, netvars, inventory, do_probe = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"

# --- entradas ---------------------------------------------------------------
hosts = {}   # ip -> {mac, iface, name, ports}

arp = os.path.join(out, "arp-raw.txt")
if os.path.exists(arp):
    # ? (198.51.100.2) at 00:00:5e:00:53:01 on vtnet0.10 expires in 1174 seconds
    pat = re.compile(r"\(([\d.]+)\) at ([0-9a-f:]{17}|\(incomplete\))(?: on (\S+))?")
    for line in open(arp):
        m = pat.search(line)
        if m and m.group(2) != "(incomplete)":
            hosts.setdefault(m.group(1), {})["mac"] = m.group(2)
            hosts[m.group(1)]["iface"] = m.group(3) or ""

sweep = os.path.join(out, "sweep-raw.txt")
if os.path.exists(sweep):
    for line in open(sweep):
        ip = line.strip()
        if ip:
            hosts.setdefault(ip, {})

# --- a que VLAN cada IP pertence -------------------------------------------
vlans = yaml.safe_load(open(netvars)).get("vlans", []) or []
nets = [(ipaddress.ip_network(f"{v['ip']}/{v.get('subnet',24)}", strict=False), v) for v in vlans]

def vlan_of(ip):
    a = ipaddress.ip_address(ip)
    for net, v in nets:
        if a in net:
            return v
    return None

# --- o que cada host serve --------------------------------------------------
# As portas são o que distingue um papel do outro: 8006 é exclusivo do Proxmox,
# 2377 só existe em nó de Swarm, 445+2049 juntos denunciam o NAS.
PORTS = {22: "ssh", 80: "http", 443: "https", 445: "smb", 2049: "nfs",
         2377: "swarm", 8006: "proxmox", 9000: "portainer", 3000: "http-alt",
         53: "dns", 1514: "syslog"}

def probe(ip):
    open_ports = []
    for port in PORTS:
        try:
            with socket.create_connection((ip, port), timeout=0.5):
                open_ports.append(port)
        except OSError:
            pass
    return ip, open_ports

if do_probe:
    with ThreadPoolExecutor(max_workers=64) as pool:
        for ip, ports in pool.map(probe, list(hosts)):
            hosts[ip]["ports"] = ports

for ip in hosts:
    hosts[ip].setdefault("ports", [])
    try:
        hosts[ip]["name"] = socket.gethostbyaddr(ip)[0]
    except OSError:
        hosts[ip]["name"] = ""

# --- palpite de papel -------------------------------------------------------
def role(ip, h):
    p, v = set(h["ports"]), vlan_of(ip)
    name = (h.get("name") or "").lower()
    if v and ip == v["ip"]:
        return "opnsense", "é o gateway da VLAN " + str(v["tag"])
    if 8006 in p:
        return "proxmox", "porta 8006 (interface do Proxmox VE)"
    if 9000 in p and 2377 in p:
        return "homelab_managers", "portas 9000 (Portainer) + 2377 (Swarm)"
    if 2377 in p:
        return "homelab_workers", "porta 2377 (Swarm), sem Portainer"
    if 445 in p and (2049 in p or 80 in p):
        return "omv", "SMB (445) com NFS/HTTP — perfil de NAS"
    if "openwrt" in name or "luci" in name:
        return "openwrt", "hostname"
    if p == {22, 80, 443} and v and v["tag"] in (10, 20):
        return "openwrt?", "ssh+http+https numa VLAN de infraestrutura"
    return "", ""

# --- o que o inventário já conhece -----------------------------------------
known = {}
try:
    inv = json.loads(subprocess.run(
        ["ansible-inventory", "-i", inventory, "--list"],
        capture_output=True, text=True).stdout)
    for host, hv in inv.get("_meta", {}).get("hostvars", {}).items():
        known[hv.get("ansible_host", host)] = host
except Exception:
    pass

# --- relatório --------------------------------------------------------------
rows, suggestions = [], {}
for ip in sorted(hosts, key=lambda x: ipaddress.ip_address(x)):
    h = hosts[ip]
    v = vlan_of(ip)
    grp, why = role(ip, h)
    inv_name = known.get(ip, "")
    rows.append((ip, h.get("mac", ""), f"{v['tag']} {v['name']}" if v else "?",
                 ",".join(PORTS.get(p, str(p)) for p in h["ports"]) or "-",
                 h.get("name", "") or "-",
                 inv_name or (grp and f"[{grp}] {why}") or ""))
    if grp and not inv_name and not grp.endswith("?"):
        suggestions.setdefault(grp, []).append((ip, h.get("name", ""), why))

w = [max(len(str(r[i])) for r in rows + [("IP", "MAC", "VLAN", "PORTAS", "DNS", "NO INVENTÁRIO / PALPITE")]) for i in range(6)]
hdr = ("IP", "MAC", "VLAN", "PORTAS", "DNS", "NO INVENTÁRIO / PALPITE")
table = ["  ".join(str(hdr[i]).ljust(w[i]) for i in range(6)),
         "  ".join("-" * w[i] for i in range(6))]
table += ["  ".join(str(r[i]).ljust(w[i]) for i in range(6)) for r in rows]
report = "\n".join(table)
print(report)
open(os.path.join(out, "hosts-table.txt"), "w").write(report + "\n")

# --- bloco pronto para o hosts.ini -----------------------------------------
if suggestions:
    lines = ["# Sugestão gerada por discover.sh — CONFIRA antes de colar.",
             "# O nome do host NÃO pode ser igual ao do grupo (o Ansible avisa",
             "# 'Found both group and host with same name' e o --limit fica ambíguo).",
             ""]
    for grp in ("proxmox", "omv", "opnsense", "openwrt", "homelab_managers", "homelab_workers"):
        if grp not in suggestions:
            continue
        lines.append(f"[{grp}]")
        for i, (ip, name, why) in enumerate(suggestions[grp], 1):
            short = (name.split(".")[0] if name else f"{grp.split('_')[0]}{i}")
            if short == grp:
                short += str(i)
            user = "root"
            lines.append(f"{short} ansible_host={ip} ansible_user={user}   # {why}")
        lines.append("")
    block = "\n".join(lines)
    open(os.path.join(out, "inventory-suggestion.ini"), "w").write(block)
    print("\n" + block)
else:
    print("\nNada novo: todo host identificado já está no inventário.")
PY

chmod -R go-rwx "$OUT" 2>/dev/null || true

echo
echo "Saída em prod-snapshot/_discovery/ (gitignorada):"
ls -1 "$OUT" | sed 's/^/  /'
echo
echo "Confira o palpite antes de colar em ansible/inventories/prod/hosts.ini."
echo "Depois: ansible -i inventories/prod/hosts.ini all -m ping"

if [[ -n "$(git -C "$REPO" status --porcelain -- "$SNAP" 2>/dev/null)" ]]; then
  echo
  echo "⚠️  git status mostra arquivos em prod-snapshot/. NÃO COMMITE."
  exit 1
fi
