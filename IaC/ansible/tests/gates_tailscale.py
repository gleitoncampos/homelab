#!/usr/bin/env python3
"""Protege a VPN de acesso remoto contra ser apagada pelo próprio apply.

O `01-opnsense-setup.yml` escreve `/conf/config.xml` INTEIRO, sem merge. Tudo
que a implantação de 03/09/2026 construiu — plugin, elemento de configuração,
interface e regra de firewall — some no próximo apply se não estiver declarado
aqui. E some em silêncio: o playbook não tem como saber que aquilo existia.

O roteiro está em `.local/docs/historico/planos/2026-09-03-vpn-tailscale.md`;
a decisão, no §3.7 do roadmap.

CAMADA 1 — `os-tailscale` declarado em `opnsense_plugins`.
  Declarar não instala: quem prova a instalação é o `pkg info -e` do playbook.
  Mas NÃO declarar garante que o plugin saia do alvo, e com ele a VPN.

CAMADA 2 — o partial existe E a skeleton o inclui.
  Um sem o outro rende um template que parece cobrir Tailscale e não emite uma
  linha sobre ele.

CAMADA 3 — nenhuma chave de autenticação literal no template.
  `tskey-` é prefixo literal da Tailscale, então a busca não tem falso
  positivo. A chave é segredo e vem do vault, como a senha do admin.

CAMADA 4 — existe regra `pass` na interface do Tailscale.
  O plugin cria a `tailscale0` SEM regra nenhuma, e o padrão do OPNsense é
  bloquear: sem a regra o túnel sobe e nada passa.

CAMADA 5 — o bloqueio de blocklist cobre as DUAS WANs.
  Não é sobre Tailscale, e está aqui porque foi descoberto no mesmo diff: a
  regra `cd96ee46` valia só para `wan`, deixando a segunda WAN receber tráfego
  de IP em blocklist. Corrigido na produção em 03/09/2026; sem esta camada, o
  apply devolve para `wan` e reabre o buraco sem aviso.

Sai 0 só quando as cinco passam.
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML.")

ANSIBLE = Path(__file__).resolve().parents[1]
COMUM = ANSIBLE / "inventories" / "_common" / "opnsense.yml"
TEMPLATES = ANSIBLE / "templates" / "network" / "opnsense"
PARTIAL = TEMPLATES / "_tailscale.xml.j2"
SKELETON = TEMPLATES / "config.xml.j2"
INCLUDE = "{% include '_tailscale.xml.j2' %}"
REGRA_BLOCKLIST = "cd96ee46-46bf-429f-ba88-7cc0c3d66e4d"

falhas = []
dados = yaml.safe_load(COMUM.read_text(encoding="utf-8"))

print("== CAMADA 1: o plugin está declarado ==")
plugins = dados.get("opnsense_plugins") or []
if "os-tailscale" in plugins:
    print("  OK os-tailscale em opnsense_plugins")
else:
    falhas.append(
        "opnsense_plugins não declara os-tailscale: o apply removeria o plugin "
        "e derrubaria a VPN de acesso remoto"
    )

print("\n== CAMADA 2: o partial existe e a skeleton o inclui ==")
if not PARTIAL.is_file():
    falhas.append(f"{PARTIAL.name} não existe")
else:
    print(f"  OK {PARTIAL.name} existe")
    if INCLUDE in SKELETON.read_text(encoding="utf-8"):
        print(f"  OK config.xml.j2 inclui {PARTIAL.name}")
    else:
        falhas.append(f"config.xml.j2 não inclui {PARTIAL.name}")

print("\n== CAMADA 3: nenhuma chave literal no template ==")
# ⚠️ Sem o `is_file()` explícito aqui, esta camada imprimiria OK quando o
# partial NÃO existe — verde por não ter sido avaliada, que é o defeito que
# este arquivo inteiro existe para evitar.
if not PARTIAL.is_file():
    print("  -- não avaliada: o partial não existe (ver camada 2)")
elif "tskey-" in PARTIAL.read_text(encoding="utf-8"):
    falhas.append(f"{PARTIAL.name} contém uma chave de autenticação literal")
else:
    print("  OK nenhum prefixo tskey- no partial")

print("\n== CAMADA 4: a tailnet tem regra de passagem ==")
# A regra tem que apontar para o MESMO identificador que _interfaces.xml.j2 dá
# à tailscale0 — hoje `opt2`. Descobrir o nome lendo o template, em vez de
# fixá-lo aqui, é o que liga as duas peças: renomear a interface sem renomear a
# regra deixa este gate vermelho, que é exatamente o acidente a evitar. Fixar
# "opt2" no teste faria ele aprovar uma regra órfã.
IFACES = TEMPLATES / "_interfaces.xml.j2"
chave_ts = None
anterior = None
for linha in IFACES.read_text(encoding="utf-8").splitlines():
    if "<if>tailscale0</if>" in linha and anterior:
        chave_ts = anterior.strip().lstrip("<").rstrip(">")
        break
    if linha.strip().startswith("<") and not linha.strip().startswith("</"):
        anterior = linha
regras = dados.get("opnsense_rules") or []
if chave_ts is None:
    falhas.append(
        "_interfaces.xml.j2 não atribui tailscale0: sem interface atribuída não "
        "há regra de firewall possível, e a geração automática de NAT de saída "
        "deixa de cobrir a tailnet"
    )
elif any(
    chave_ts in [i.strip() for i in str(r.get("interface", "")).split(",")]
    and r.get("action") == "pass"
    for r in regras
):
    print(f"  OK regra pass na interface do Tailscale (`{chave_ts}`)")
else:
    falhas.append(
        f"opnsense_rules não tem regra pass em `{chave_ts}`, que é a interface "
        "que _interfaces.xml.j2 dá à tailscale0: o túnel subiria e nenhum "
        "tráfego passaria"
    )

print("\n== CAMADA 5: a blocklist cobre as duas WANs ==")
bloqueio = next((r for r in regras if r.get("uuid") == REGRA_BLOCKLIST), None)
if bloqueio is None:
    falhas.append(f"a regra de blocklist {REGRA_BLOCKLIST} sumiu de opnsense_rules")
else:
    ifaces = {i.strip() for i in str(bloqueio.get("interface", "")).split(",")}
    if {"wan", "opt1"} <= ifaces:
        print(f"  OK a regra de blocklist cobre {sorted(ifaces)}")
    else:
        falhas.append(
            f"a regra de blocklist cobre só {sorted(ifaces)}: a segunda WAN "
            "voltaria a receber tráfego de IP em blocklist"
        )

if falhas:
    print()
    for f in falhas:
        print(f"FALHA: {f}", file=sys.stderr)
    sys.exit(1)

print("\n  TAILSCALE OK (plugin declarado; partial incluído; sem chave literal; "
      "tráfego liberado; blocklist nas duas WANs)")
