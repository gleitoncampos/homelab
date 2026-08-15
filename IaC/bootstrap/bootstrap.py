#!/usr/bin/env python3
"""
bootstrap.py — gera os pré-requisitos que o Ansible e o Terraform consomem.

NÃO substitui nenhum dos dois. Ele produz as *entradas* — vault, inventário,
tfvars, chave SSH — e para aí. O `bootstrap.sh` anterior deste repositório foi
deletado por duplicar o `01-bootstrap-os.yml`; a lição está registrada no
CLAUDE.md e vale aqui: se este script começar a instalar pacote ou configurar
host, a duplicação volta.

O desenho central é uma FONTE ÚNICA. O mesmo valor era pedido em vários lugares:
o gateway vivia em três arquivos (`swarm_gateway` no Terraform, `mgmt_gateway_ip`
no inventário, e o plano de VLANs), e a chave SSH pública em dois. Cada duplicata
é uma chance de divergência silenciosa. Aqui as respostas ficam num arquivo só e
se espalham para os alvos.

Consequência prática: rodar de novo NÃO repergunta nada. Regenera a partir do
arquivo de respostas, o que torna a operação idempotente por construção. Para
corrigir um IP, edite uma linha e rode de novo.

    make bootstrap              # primeira vez: pergunta e gera
    make bootstrap              # depois: regenera a partir das respostas
    make bootstrap ARGS=--dry-run
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
IAC = HERE.parent
REPO = IAC.parent
ANSIBLE = IAC / "ansible"
TERRAFORM = IAC / "terraform" / "proxmox"

ANSWERS = IAC / "homelab.local.yml"
ANSWERS_TEMPLATE = HERE / "templates" / "answers.yml.tmpl"
CREDENTIALS = HERE / "CREDENCIAIS.local.txt"
VAULT_PASS = ANSIBLE / ".ansible_vault_pass"
SSH_KEY = REPO / ".local" / "ssh" / "homelab_ed25519"

ENVS = ("prod", "dev")

# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------
TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def step(msg: str) -> None:
    print(_c("1", f"\n{msg}"))


def ok(msg: str) -> None:
    print(f"  {_c('32', '✓')} {msg}")


def warn(msg: str) -> None:
    print(f"  {_c('33', '!')} {msg}")


def info(msg: str) -> None:
    print(f"  {_c('90', '–')} {msg}")


def die(msg: str, *, hint: str = "") -> None:
    print(f"\n  {_c('31', '✗')} {msg}", file=sys.stderr)
    if hint:
        print(f"      {hint}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Catálogo de segredos
# ---------------------------------------------------------------------------
# `humano` marca o segredo que VOCÊ precisa conhecer para conseguir logar. Esses
# vão para o arquivo de credenciais; os demais nunca são exibidos, porque quem
# os lê é o Ansible.
#
# (chave, gerador, humano, descrição)
GENERATED = [
    ("portainer_admin_password", "senha", True, "Portainer — admin do cluster"),
    ("grafana_admin_password", "senha", True, "Grafana — admin"),
    ("postgres_paperless_password", "senha", False, ""),
    ("postgres_vikunja_password", "senha", False, ""),
    ("postgres_linkwarden_password", "senha", False, ""),
    ("paperless_secret_key", "hex", False, ""),
    ("cronicle_secret_key", "hex", False, ""),
    ("bytestash_jwt_secret", "hex", False, ""),
    ("opnsense_snmp_ro_community", "token", False, ""),
    ("wifi_main_password", "wifi", True, "Wi-Fi principal"),
    ("wifi_iot_password", "wifi", True, "Wi-Fi IoT"),
    ("wifi_media_password", "wifi", True, "Wi-Fi mídia"),
    ("wifi_guest_password", "wifi", True, "Wi-Fi visitantes"),
]

# Impossíveis de gerar: vêm do console de um terceiro. O valor está em imprimir
# a URL exata, porque procurar onde se obtém cada uma é o que consome tempo.
EXTERNAL = [
    (
        "cloudflare_email",
        "E-mail da conta Cloudflare",
        "https://dash.cloudflare.com/",
        "",
    ),
    (
        "cloudflare_api_token",
        "Token de API da Cloudflare",
        "https://dash.cloudflare.com/profile/api-tokens",
        "Escopo: Zone:DNS:Edit + Zone:Zone:Read, restrito à sua zona",
    ),
    (
        "pve_exporter_token_name",
        "Nome do token do Proxmox para o exporter",
        "",
        "pveum user token add prometheus@pve exporter --privsep 1",
    ),
    (
        "pve_exporter_token_value",
        "Valor do token do Proxmox",
        "",
        "Exibido uma única vez, na saída do comando acima",
    ),
    (
        "proxmox_api_token_id",
        "Token de API do Proxmox para o Terraform",
        "",
        "pveum user token add terraform@pve terraform --privsep 0",
    ),
    (
        "proxmox_api_token_secret",
        "Segredo do token do Terraform",
        "",
        "Exibido uma única vez, na saída do comando acima",
    ),
    (
        "opnsense_pppoe_username",
        "Usuário PPPoE do provedor",
        "",
        "Conta do PROVEDOR, não do lab — rotacionar exige contato com ele",
    ),
    (
        "geoip_license_key",
        "Chave GeoLite2 (opcional, vazio desativa)",
        "https://www.maxmind.com/en/accounts/current/license-key",
        "",
    ),
    (
        "opnsense_etpro_telemetry_token",
        "Token ETPro (opcional, vazio desativa)",
        "https://www.proofpoint.com/us/products/et-pro-ruleset",
        "⚠️ O valor anterior esteve em 25 commits: gere um NOVO, não reaproveite",
    ),
]

# O OPNsense exige bcrypt, que não sai de openssl nem da stdlib do Python. A
# senha é gerada aqui; o hash tem de ser calculado no próprio firewall. Deixar
# um marcador é melhor que gerar um hash de algoritmo errado, que produziria
# firewall sem acesso à GUI depois do apply.
PENDING_HASHES = ["opnsense_root_password_hash", "opnsense_admin_password_hash"]


# ---------------------------------------------------------------------------
# Geradores
# ---------------------------------------------------------------------------
def gen(kind: str) -> str:
    if kind == "senha":
        # base64 de 32 bytes: ~43 caracteres. Bem acima do mínimo de 16 que o
        # vault.yml.example exige para Portainer e Grafana.
        return base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    if kind == "hex":
        return secrets.token_hex(32)
    if kind == "token":
        return secrets.token_urlsafe(24)
    if kind == "wifi":
        # WPA2 aceita até 63 caracteres, mas senha de Wi-Fi é digitada à mão em
        # TV, câmera e lâmpada. Alfabeto sem ambiguidade visual e comprimento
        # que ainda dá 24 caracteres — o problema real é digitação, não força.
        alfabeto = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alfabeto) for _ in range(24))
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def ask(label: str, default: str = "", *, obrigatorio: bool = True) -> str:
    sufixo = f" [{default}]" if default else ""
    while True:
        try:
            v = input(f"  {label}{sufixo}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            die("interrompido — nada foi escrito")
        v = v or default
        if v or not obrigatorio:
            return v
        print("    (obrigatório)")


def ask_secret(label: str, *, obrigatorio: bool = True) -> str:
    """Lê sem eco. NUNCA passe segredo como argumento de comando: argumento
    aparece em `ps` para qualquer usuário da máquina."""
    import getpass

    while True:
        try:
            v = getpass.getpass(f"  {label}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            die("interrompido — nada foi escrito")
        if v or not obrigatorio:
            return v
        print("    (obrigatório, ou Enter duas vezes para deixar vazio)")


def confirm(label: str, *, default: bool = True) -> bool:
    d = "S/n" if default else "s/N"
    v = input(f"  {label} [{d}]: ").strip().lower()
    return default if not v else v.startswith("s")


# ---------------------------------------------------------------------------
# Escrita segura
# ---------------------------------------------------------------------------
def gitignored(path: Path) -> bool:
    """O repositório já foi contaminado uma vez por arquivo gerado em caminho
    errado. Nenhuma saída é escrita sem esta checagem passar."""
    r = subprocess.run(
        ["git", "check-ignore", "-q", str(path)], cwd=REPO, capture_output=True
    )
    return r.returncode == 0


def write(path: Path, content: str, *, mode: int = 0o600, dry: bool = False) -> None:
    rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
    if not gitignored(path):
        die(
            f"{rel} NÃO está no .gitignore — recusando escrever",
            hint="Acrescente a regra ao .gitignore ANTES de gerar o arquivo.",
        )
    if dry:
        info(f"[dry-run] escreveria {rel} ({len(content)} bytes, modo {oct(mode)})")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Escreve com o modo restrito desde a criação. Criar 0644 e ajustar depois
    # deixa uma janela em que o segredo é legível por qualquer usuário.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
    except BaseException:
        # Falha no meio da escrita deixaria um arquivo truncado; melhor não
        # deixar nada do que deixar um vault pela metade.
        path.unlink(missing_ok=True)
        raise
    os.chmod(path, mode)
    ok(f"{rel}")


# ---------------------------------------------------------------------------
# Fase 1 — pré-voo
# ---------------------------------------------------------------------------
OBRIGATORIAS = {
    "ansible-vault": "pip install ansible-core",
    "ansible-playbook": "pip install ansible-core",
    "ssh-keygen": "pacote openssh-client / openssh",
}
OPCIONAIS = {
    "terraform": "https://developer.hashicorp.com/terraform/install",
    "docker": "https://docs.docker.com/engine/install/",
    "yamllint": "pip install yamllint",
    "ansible-lint": "pip install ansible-lint",
    "shellcheck": "pacote shellcheck / ShellCheck",
}


def preflight(dry: bool) -> None:
    step("1. Pré-voo")

    if sys.version_info < (3, 9):
        die(f"Python 3.9+ necessário (rodando {sys.version.split()[0]})")
    ok(f"python {sys.version.split()[0]}")

    try:
        import yaml  # noqa: F401
    except ImportError:
        die(
            "PyYAML ausente",
            hint="pip install pyyaml  (vem junto com o ansible-core)",
        )
    ok("PyYAML")

    faltando = [t for t in OBRIGATORIAS if not shutil.which(t)]
    if faltando:
        for t in faltando:
            print(f"      {t}: {OBRIGATORIAS[t]}", file=sys.stderr)
        die(f"ferramenta obrigatória ausente: {', '.join(faltando)}")
    ok(f"obrigatórias presentes ({', '.join(OBRIGATORIAS)})")

    for t, como in OPCIONAIS.items():
        if not shutil.which(t):
            warn(f"{t} ausente — {como}")

    # Só um aviso: o gate é assunto do CLAUDE.md, mas quem roda o bootstrap está
    # justamente montando o clone, e é o melhor momento para lembrar.
    r = subprocess.run(
        ["git", "config", "core.hooksPath"], cwd=REPO, capture_output=True, text=True
    )
    if r.stdout.strip() != ".githooks":
        warn("gate de pre-commit inativo — rode `make hooks`")


# ---------------------------------------------------------------------------
# Fase 2 — respostas
# ---------------------------------------------------------------------------
def perguntar_respostas() -> str:
    """Monta o arquivo de respostas a partir do template, substituindo os
    marcadores. Devolve o texto; quem grava é o chamador."""
    texto = ANSWERS_TEMPLATE.read_text()

    print(
        "\n  Estas respostas ficam em IaC/homelab.local.yml (gitignorado).\n"
        "  Nenhum segredo entra nele — senhas e tokens vão direto para o vault.\n"
    )

    respostas = {
        "DOMINIO": ask("Domínio do lab (ex.: homelab.example)"),
        "ADMIN_USER": ask("Usuário administrativo do OPNsense", "netadmin"),
        "ADMIN_EMAIL": ask("E-mail para alertas do Proxmox"),
    }

    print(_c("1", "\n  Rede de HOJE — é por onde o Ansible vai alcançar os hosts."))
    respostas |= {
        "ATUAL_GATEWAY": ask("Gateway atual"),
        "ATUAL_MANAGER": ask("IP do nó manager do Swarm"),
        "ATUAL_WORKER": ask("IP do worker (vazio se não houver)", obrigatorio=False),
        "ATUAL_HV1": ask("IP do hypervisor primário"),
        "ATUAL_HV2": ask("IP do hypervisor secundário", obrigatorio=False),
        "ATUAL_OMV": ask("IP do NAS/OMV", obrigatorio=False),
        "ATUAL_OPNSENSE": ask("IP do OPNsense", obrigatorio=False),
        "ATUAL_OPENWRT": ask("IP do OpenWrt", obrigatorio=False),
        "ATUAL_DEV": ask("IP do CT de desenvolvimento", obrigatorio=False),
    }

    print(_c("1", "\n  Nomes dos nós Proxmox — o Terraform os usa como destino."))
    respostas |= {
        "HV1_NOME": ask("Nome do nó primário"),
        "HV2_NOME": ask("Nome do nó secundário", obrigatorio=False),
    }

    print(
        _c("1", "\n  Rede-ALVO — o plano de VLANs.")
        + "\n  O prefixo abaixo vira 192.168.<vlan>.x no arquivo gerado;"
        "\n  revise o plano inteiro no editor ao final."
    )
    respostas["ALVO_PREFIXO"] = ask("Prefixo das VLANs (ex.: 192.168)", "192.168")

    respostas["DATA"] = date.today().isoformat()

    for chave, valor in respostas.items():
        texto = texto.replace(f"@@{chave}@@", valor)
    return texto


def carregar_respostas() -> dict:
    import yaml

    dados = yaml.safe_load(ANSWERS.read_text()) or {}
    for secao in ("dominio", "rede_atual", "rede_alvo", "nos"):
        if secao not in dados:
            die(
                f"seção '{secao}' ausente em {ANSWERS.relative_to(REPO)}",
                hint="Apague o arquivo e rode de novo para recriá-lo.",
            )
    return dados


# ---------------------------------------------------------------------------
# Fase 3 — segredos
# ---------------------------------------------------------------------------
def montar_segredos(existente: dict | None) -> tuple[dict, list[tuple[str, str, str]]]:
    """Devolve (segredos, credenciais_humanas). Chave já presente no vault
    anterior é PRESERVADA — este script nunca rotaciona sozinho."""
    step("3. Segredos")
    segredos: dict[str, str] = {}
    humanas: list[tuple[str, str, str]] = []
    preservadas = 0

    for chave, tipo, humano, descricao in GENERATED:
        if existente and existente.get(chave):
            segredos[chave] = existente[chave]
            preservadas += 1
            if humano:
                humanas.append((descricao, chave, existente[chave]))
            continue
        valor = gen(tipo)
        segredos[chave] = valor
        if humano:
            humanas.append((descricao, chave, valor))

    ok(f"{len(GENERATED) - preservadas} gerados, {preservadas} preservados do vault atual")

    pendentes = [
        (c, l, u, n) for c, l, u, n in EXTERNAL if not (existente or {}).get(c)
    ]
    if pendentes:
        print(
            f"\n  {len(pendentes)} valores vêm de um console externo e não têm"
            " como ser gerados.\n  Enter deixa em branco; o validate_vault.yml"
            " avisa depois do que faltou.\n"
        )
        for chave, label, url, nota in pendentes:
            if url:
                print(f"    {_c('90', url)}")
            if nota:
                print(f"    {_c('90', nota)}")
            segredos[chave] = ask_secret(label, obrigatorio=False)
            print()
    for chave, *_ in EXTERNAL:
        segredos.setdefault(chave, (existente or {}).get(chave, ""))

    return segredos, humanas


# ---------------------------------------------------------------------------
# Fase 4 — renderização
# ---------------------------------------------------------------------------
def yaml_valor(v) -> str:
    import yaml

    return yaml.safe_dump(v, allow_unicode=True, default_flow_style=True).strip()


def render_vault(env: str, resp: dict, seg: dict, ssh_pub: str) -> str:
    d = resp["dominio"]
    # dev e prod usam segredos DIFERENTES para os valores que isolam ambiente;
    # reusar o de prod em dev anula o isolamento. Só o que é identidade
    # (domínio, chave SSH, usuário) é compartilhado.
    def por_ambiente(chave: str) -> str:
        v = seg[chave]
        if env == "dev" and chave in {
            "cloudflare_api_token",
            "postgres_paperless_password",
            "postgres_vikunja_password",
            "postgres_linkwarden_password",
            "portainer_admin_password",
            "grafana_admin_password",
        }:
            return gen("senha") if "password" in chave else v
        return v

    linhas = [
        "---",
        f"# Gerado por IaC/bootstrap/bootstrap.py em {date.today().isoformat()}",
        f"# Ambiente: {env}. Cifrado com ansible-vault; a senha está em",
        "# ansible/.ansible_vault_pass (gitignorado — faça backup dele!).",
        "#",
        "# Editar:  ansible-vault edit inventories/%s/group_vars/all/vault.yml" % env,
        "# Rodar o bootstrap de novo NÃO sobrescreve este arquivo.",
        "",
        f"homelab_domain: {d}",
        f"ssh_public_key: {ssh_pub!r}",
        "",
    ]
    for chave, _t, _h, _d in GENERATED:
        linhas.append(f"{chave}: {por_ambiente(chave)!r}")
    linhas.append("")
    for chave, _l, _u, _n in EXTERNAL:
        linhas.append(f"{chave}: {seg.get(chave, '')!r}")
    linhas += [
        "",
        "# SSIDs — nome da rede, não segredo, mas ficam junto da senha.",
        f"wifi_main_ssid: {resp.get('wifi', {}).get('main', 'homelab')!r}",
        f"wifi_iot_ssid: {resp.get('wifi', {}).get('iot', 'homelab-iot')!r}",
        f"wifi_media_ssid: {resp.get('wifi', {}).get('media', 'homelab-media')!r}",
        f"wifi_guest_ssid: {resp.get('wifi', {}).get('guest', 'homelab-guest')!r}",
        "",
        "# OPNsense",
        f"opnsense_admin_username: {resp['dominio_admin_user']!r}",
        "opnsense_admin_fullname: 'Administrador do homelab'",
        "",
        "# ⚠️ bcrypt NÃO sai de openssl nem da stdlib do Python. Gere no próprio",
        "#    firewall e cole aqui com `ansible-vault edit`:",
        "#      ssh root@<opnsense> -- php -r 'echo password_hash(\"SENHA\","
        " PASSWORD_BCRYPT).\"\\n\";'",
        "#    Deve começar com $2y$ ou $2b$. Formato errado = firewall sem GUI.",
    ]
    for chave in PENDING_HASHES:
        linhas.append(f"{chave}: ''  # PENDENTE")
    linhas += [
        "",
        "# Base64 do PEM/valor inteiro. Vazio desativa a funcionalidade.",
        "opnsense_admin_authorizedkeys_b64: ''",
        "opnsense_pppoe_password_b64: ''",
        "opnsense_gui_cert_crt_b64: ''",
        "opnsense_gui_cert_key_b64: ''",
        "",
        "# Topologia consumida pelo template do OPNsense.",
        "dhcp_static_maps: []",
        "dns_redirect_bypass_ips: []",
        "opnsense_dns_redirect_enabled: true",
        f"opnsense_dns_redirect_target: {resp['rede_alvo']['gateway']!r}",
        "unbound_host_overrides_extra: []",
        "",
        f"proxmox_alert_email: {resp['alerta_email']!r}",
        "",
    ]
    return "\n".join(linhas)


def render_hosts(env: str, resp: dict) -> str:
    a = resp["rede_atual"]
    n = resp["nos"]
    usuario = resp.get("ssh_user", "nuvidio")

    def linha(nome: str, ip: str, user: str = "", extra: str = "") -> str:
        if not ip:
            return f"# {nome} ansible_host=<PREENCHER> ansible_user={user or usuario}"
        return f"{nome} ansible_host={ip} ansible_user={user or usuario}{extra}"

    return f"""\
# Gerado por IaC/bootstrap/bootstrap.py em {date.today().isoformat()}
#
# Os IPs abaixo são os da rede COMO ELA É HOJE — é por aqui que o Ansible
# alcança os hosts. O plano de VLANs (rede-alvo) vive em group_vars/network.yml
# e descreve o destino, não o presente. Confundir os dois produz timeout de SSH,
# cujo sintoma não diz nada sobre a causa.

[homelab_managers]
{linha("node1", a["manager"])}

[homelab_workers]
{linha("node2", a.get("worker", ""))}

[opnsense]
{linha("opnsense", a.get("opnsense", ""), "root")}

[openwrt]
{linha("openwrt", a.get("openwrt", ""), "root", " ansible_shell_type=sh")}

[proxmox]
{linha(n["primario"]["nome"], a.get("hypervisor1", ""), "root")}
{linha(n.get("secundario", {}).get("nome", "hv2"), a.get("hypervisor2", ""), "root")}

[omv]
{linha("omv", a.get("omv", ""), "root")}

[homelab:children]
homelab_managers
homelab_workers

[network:children]
opnsense
openwrt

[all:vars]
# Gateway da rede de gerência, usado como servidor NTP pelos hosts Proxmox e
# OMV. Os playbooks 00-* não têm default para esta variável: sem ela abortam
# com erro claro, em vez de apontar o chrony para um host inexistente.
mgmt_gateway_ip={a["gateway"]}
"""


def render_tfvars(resp: dict, seg: dict, ssh_pub: str) -> str:
    alvo = resp["rede_alvo"]
    n = resp["nos"]
    cidr = alvo.get("cidr", 24)

    def ip(chave: str, default: str = "") -> str:
        v = alvo.get(chave, default)
        return f"{v}/{cidr}" if v else ""

    return f"""\
# Gerado por IaC/bootstrap/bootstrap.py em {date.today().isoformat()}
#
# ⚠️ Os IPs aqui são os da rede-ALVO: o Terraform CRIA as VMs, então ele as cria
# já onde elas devem ficar. O inventário do Ansible usa os IPs atuais, porque
# precisa alcançar o que já existe. Os dois estarem diferentes é esperado até a
# migração de rede acontecer.

proxmox_api_url          = "https://{resp["rede_atual"].get("hypervisor1", "")}:8006/"
proxmox_api_token_id     = "{seg.get("proxmox_api_token_id", "")}"
proxmox_api_token_secret = "{seg.get("proxmox_api_token_secret", "")}"
proxmox_ssh_username     = "root"

proxmox_node_primary   = "{n["primario"]["nome"]}"
proxmox_node_secondary = "{n.get("secundario", {}).get("nome", "")}"

ssh_public_key = "{ssh_pub}"

# Endereço PCI (lspci no nó primário) da NIC de WAN em passthrough.
opnsense_wan_pci_id = "{resp.get("opnsense_wan_pci_id", "0000:05:00.0")}"

docker_swarm_node01_ip = "{ip("manager")}"
docker_swarm_node02_ip = "{ip("worker")}"
omv_nas_ip             = "{ip("omv")}"
dev_node_ip            = "{ip("dev")}"
swarm_gateway          = "{alvo["gateway"]}"
"""


def render_patterns(resp: dict) -> str:
    nomes = [resp["nos"]["primario"]["nome"]]
    if resp["nos"].get("secundario", {}).get("nome"):
        nomes.append(resp["nos"]["secundario"]["nome"])
    nomes += resp.get("outros_hostnames", [])
    alt = "|".join(re.escape(x) for x in nomes if x)
    dom = re.escape(resp["dominio"])
    return f"""\
# Gerado por IaC/bootstrap/bootstrap.py em {date.today().isoformat()}.
# NÃO versionado — ver o cabeçalho de .githooks/secret-patterns.txt.
#
# ⚠️ Ao mudar hostname ou domínio, rode `make bootstrap` de novo ou edite aqui.
# Padrão desatualizado é pior que padrão nenhum: dá a sensação de proteção.

hostname de dispositivo%%\\b({alt})\\b%%^\\.githooks/
domínio real%%\\b({dom})\\b%%^\\.githooks/
"""


def render_credenciais(humanas: list[tuple[str, str, str]]) -> str:
    linhas = [
        "CREDENCIAIS GERADAS — mova para o gerenciador de senhas e APAGUE este arquivo.",
        "",
        "Só aparecem aqui as senhas que VOCÊ precisa conhecer para logar. As demais",
        "(JWT, chaves de assinatura, senhas de banco) existem apenas para o Ansible",
        "e nunca são exibidas — estão no vault, acessíveis com `ansible-vault view`.",
        "",
        f"Gerado em {date.today().isoformat()}. Arquivo 0600 e gitignorado.",
        "=" * 78,
        "",
    ]
    for descricao, chave, valor in humanas:
        linhas += [f"{descricao}", f"  ({chave})", f"  {valor}", ""]
    linhas += [
        "=" * 78,
        "Depois de salvar:  shred -u IaC/bootstrap/CREDENCIAIS.local.txt",
        "",
    ]
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Fase 5 — chave SSH e vault
# ---------------------------------------------------------------------------
def garantir_chave_ssh(dry: bool) -> str:
    step("4. Chave SSH")
    pub = SSH_KEY.with_suffix(".pub") if SSH_KEY.suffix else Path(f"{SSH_KEY}.pub")
    if pub.exists():
        ok(f"já existe: {pub.relative_to(REPO)}")
        return pub.read_text().strip()
    if dry:
        info("[dry-run] geraria par ed25519")
        return "ssh-ed25519 AAAA<dry-run>"

    print(
        "\n  ⚠️ Chave SEM passphrase dá acesso root à frota inteira num arquivo.\n"
        "     Com passphrase, use ssh-agent para não digitar a cada play.\n"
    )
    com_passphrase = confirm("Proteger a chave com passphrase?", default=True)
    SSH_KEY.parent.mkdir(parents=True, exist_ok=True)
    if not gitignored(SSH_KEY.parent):
        die(f"{SSH_KEY.parent.relative_to(REPO)} não está no .gitignore")

    cmd = ["ssh-keygen", "-t", "ed25519", "-f", str(SSH_KEY), "-C", "homelab"]
    if not com_passphrase:
        cmd += ["-N", ""]
    # Sem capture_output: o ssh-keygen precisa do terminal para pedir a
    # passphrase, e capturar deixaria o prompt invisível.
    if subprocess.run(cmd).returncode != 0:
        die("ssh-keygen falhou")
    os.chmod(SSH_KEY, 0o600)
    ok(f"par gerado em {SSH_KEY.relative_to(REPO)}")
    return Path(f"{SSH_KEY}.pub").read_text().strip()


def garantir_senha_vault(dry: bool) -> None:
    if VAULT_PASS.exists():
        ok(f"{VAULT_PASS.relative_to(REPO)} já existe (preservado)")
        return
    senha = "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(40)
    )
    write(VAULT_PASS, senha + "\n", dry=dry)
    if not dry:
        warn("⚠️ Sem esta senha os vaults são IRRECUPERÁVEIS. Faça backup dela agora.")


def vault_existente(env: str) -> dict | None:
    """Lê o vault atual, se houver, para preservar segredo já em uso. Este
    script nunca rotaciona sozinho: rotação é decisão, não efeito colateral."""
    import yaml

    caminho = ANSIBLE / "inventories" / env / "group_vars" / "all" / "vault.yml"
    if not caminho.exists() or not VAULT_PASS.exists():
        return None
    r = subprocess.run(
        ["ansible-vault", "view", "--vault-password-file", str(VAULT_PASS), str(caminho)],
        capture_output=True,
        text=True,
        cwd=ANSIBLE,
    )
    if r.returncode != 0:
        return None
    try:
        return yaml.safe_load(r.stdout) or {}
    except yaml.YAMLError:
        return None


def cifrar_vault(env: str, conteudo: str, dry: bool) -> None:
    destino = ANSIBLE / "inventories" / env / "group_vars" / "all" / "vault.yml"
    if destino.exists():
        warn(f"{destino.relative_to(REPO)} já existe — NÃO sobrescrito")
        info("Para acrescentar chave nova: ansible-vault edit no arquivo")
        return
    if dry:
        info(f"[dry-run] cifraria {destino.relative_to(REPO)}")
        return
    if not gitignored(destino):
        die(f"{destino.relative_to(REPO)} não está no .gitignore")

    destino.parent.mkdir(parents=True, exist_ok=True)
    # Passa pelo stdin do ansible-vault em vez de gravar texto puro e cifrar
    # depois: sem arquivo intermediário, não há janela para Ctrl-C deixar o
    # segredo em claro no disco.
    r = subprocess.run(
        [
            "ansible-vault", "encrypt_string",
            "--vault-password-file", str(VAULT_PASS),
            "--stdin-name", "_",
        ],
        input="x", capture_output=True, text=True, cwd=ANSIBLE,
    )
    if r.returncode != 0:
        die("ansible-vault indisponível", hint=r.stderr.strip()[:200])

    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(conteudo)
    r = subprocess.run(
        ["ansible-vault", "encrypt", "--vault-password-file", str(VAULT_PASS), str(destino)],
        capture_output=True, text=True, cwd=ANSIBLE,
    )
    if r.returncode != 0:
        destino.unlink(missing_ok=True)
        die("falha ao cifrar o vault", hint=r.stderr.strip()[:200])
    os.chmod(destino, 0o600)
    ok(f"{destino.relative_to(REPO)} (cifrado)")


# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="mostra o que faria, sem escrever")
    p.add_argument("--reconfigure", action="store_true", help="repergunta e reescreve o arquivo de respostas")
    args = p.parse_args()
    dry = args.dry_run

    os.umask(0o077)
    print(_c("1", "\nbootstrap — pré-requisitos do IaC"))
    if dry:
        warn("dry-run: nada será escrito")

    preflight(dry)

    step("2. Respostas")
    if ANSWERS.exists() and not args.reconfigure:
        ok(f"{ANSWERS.relative_to(REPO)} — reaproveitando (não repergunta)")
    else:
        texto = perguntar_respostas()
        write(ANSWERS, texto, mode=0o600, dry=dry)
        if dry:
            info("[dry-run] parando: sem arquivo de respostas não há o que gerar")
            return
        editor = os.environ.get("EDITOR", "")
        print(
            "\n  O plano de VLANs foi pré-preenchido e precisa da sua revisão —\n"
            "  48 valores não se preenchem por prompt, mas se revisam de uma vez."
        )
        if editor and confirm(f"Abrir {ANSWERS.name} no {editor}?", default=True):
            subprocess.run([editor, str(ANSWERS)])
        else:
            info(f"Revise à mão: {ANSWERS.relative_to(REPO)}")

    resp = carregar_respostas()
    resp.setdefault("dominio_admin_user", resp.get("admin_user", "netadmin"))
    resp.setdefault("alerta_email", resp.get("email_alerta", ""))

    garantir_senha_vault(dry)
    ssh_pub = garantir_chave_ssh(dry)

    for env in ENVS:
        existente = vault_existente(env)
        seg, humanas = montar_segredos(existente)

        step(f"5. Gerando alvos — {env}")
        cifrar_vault(env, render_vault(env, resp, seg, ssh_pub), dry)
        write(ANSIBLE / "inventories" / env / "hosts.ini", render_hosts(env, resp), mode=0o600, dry=dry)
        if env == "prod":
            write(TERRAFORM / "terraform.tfvars", render_tfvars(resp, seg, ssh_pub), mode=0o600, dry=dry)
            write(REPO / ".githooks" / "secret-patterns.local.txt", render_patterns(resp), mode=0o644, dry=dry)
            if humanas:
                write(CREDENTIALS, render_credenciais(humanas), mode=0o600, dry=dry)

    step("6. O que falta")
    print(
        f"""
  1. Salve as credenciais e apague o arquivo:
       cat {CREDENTIALS.relative_to(REPO)}   →  gerenciador de senhas
       shred -u {CREDENTIALS.relative_to(REPO)}

  2. Faça backup de {VAULT_PASS.relative_to(REPO)}.
     Sem ela os vaults são irrecuperáveis, e não há segunda cópia.

  3. Os dois hashes bcrypt do OPNsense ficaram PENDENTES. bcrypt não sai de
     openssl nem da stdlib; gere no próprio firewall e cole com
     `ansible-vault edit`. Formato errado = firewall sem acesso à GUI.

  4. Instale a chave pública nos hosts, e confira:
       cd IaC/ansible && ansible -m ping all

  5. Valide o repositório:
       make check
"""
    )
    ok("bootstrap concluído")


if __name__ == "__main__":
    main()
