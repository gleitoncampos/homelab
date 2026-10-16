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
# Fora do repositório, na pasta de chaves do usuário: chave privada não é
# artefato do projeto, e dentro dele dependia de uma linha do .gitignore.
SSH_KEY = Path.home() / ".ssh" / "homelab_ed25519"

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
    # Segredo compartilhado entre o Portainer e os agentes do Swarm
    # (AGENT_SECRET). Faltava: o 02-bootstrap-cluster abortava no gate do
    # vault (13/10/2026).
    ("portainer_agent_secret", "hex", False, ""),
    ("wifi_main_password", "wifi", True, "Wi-Fi principal"),
    ("wifi_iot_password", "wifi", True, "Wi-Fi IoT"),
    ("wifi_media_password", "wifi", True, "Wi-Fi mídia"),
    ("wifi_guest_password", "wifi", True, "Wi-Fi visitantes"),
    # Senha do usuário SMB que o Proxmox usa para montar o share de backup do
    # NAS. Humana porque quem a cadastra do outro lado é você, na interface do
    # OMV: sem ela, o storage aparece na UI do Proxmox e nunca monta — e o
    # vzdump falha toda noite contra um destino inativo.
    ("proxmox_backup_share_password", "senha", True, "NAS — usuário SMB do backup"),
    # Segundo administrador do NAS, além do `admin` que o OMV cria. Humana pelo
    # mesmo motivo da anterior: é com ela que uma pessoa entra na interface do
    # OMV. A conta vive no config.xml, que a recriação da VM destrói — sem esta
    # chave, uma reconstrução devolve o NAS com um administrador a menos.
    ("omv_admin_password", "senha", True, "NAS — segundo administrador"),
    # Dois usuários do NUT, duas senhas. Separar não é zelo: o usuário
    # `upsmon` primary pode declarar FSD e derrubar o parque inteiro, e o
    # secondary só obedece. Não há terceira para o exportador do Prometheus —
    # o upsd não pede credencial para ler variável.
    ("nut_monitor_password", "senha", False, ""),
    ("nut_secondary_password", "senha", False, ""),
    # Exporter do Proxmox: usuário prometheus@pve, só PVEAuditor, criado nos
    # dois hosts pelo 00-proxmox-api-token.yml com ESTA senha. Senha e não
    # token: os dois hipervisores não são cluster, e token tem valor diferente por
    # host — o exporter usa uma credencial só para os dois (13/10/2026).
    ("pve_exporter_password", "senha", False, ""),
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
        # Opcional por desenho: vazio, o partial do Tailscale grava
        # <preAuthKey/> e a autenticação é feita pela GUI. Mas a variável tem
        # de EXISTIR — o `{% if %}` do template aborta com "is undefined".
        "opnsense_tailscale_preauth_key",
        "Chave de pré-autorização do Tailscale (opcional, vazio = autentica pela GUI)",
        "https://login.tailscale.com/admin/settings/keys",
        "Chave viva no vault é credencial de admissão: prefira vazio + GUI",
    ),
    (
        "opnsense_etpro_telemetry_token",
        "Token ETPro (opcional, vazio desativa)",
        "https://www.proofpoint.com/us/products/et-pro-ruleset",
        "⚠️ O valor anterior esteve em 25 commits: gere um NOVO, não reaproveite",
    ),
]

# Senha que uma PESSOA digita é perguntada, com sugestão: Enter aceita a
# sugestão, ou digita-se outra. A sugestão usa o alfabeto do Wi-Fi (sem
# ambiguidade visual, sem símbolo): a base64 de antes não passava pelo teclado
# ABNT da console do noVNC, e a senha do admin do OPNsense ficou impossível de
# digitar lá em 12/10/2026. Limites: os do validate_vault.yml (Portainer e
# Grafana >= 16) e os do WPA2 (8 a 63).
LIMITES_SENHA = {
    "portainer_admin_password": (16, None),
    "grafana_admin_password": (16, None),
    # Contas de administrador: mínimo de 12 (proposta de 12/10/2026, aceita em
    # 14/10 — a senha do admin do OPNsense tinha saído igual ao usuário).
    "omv_admin_password": (12, None),
    "proxmox_backup_share_password": (12, None),
    "wifi_main_password": (8, 63),
    "wifi_iot_password": (8, 63),
    "wifi_media_password": (8, 63),
    "wifi_guest_password": (8, 63),
}

# Mínimo das senhas de administrador que não estão em LIMITES_SENHA (admin e
# root do OPNsense, perguntados em segredos_opnsense).
MINIMO_ADMIN = 12

# Senha recusada em QUALQUER pergunta, além do nome de usuário da conta:
# nomes de conta e padrões de fábrica que um ataque tenta primeiro.
SENHAS_PROIBIDAS = ("admin", "root", "opnsense", "password", "senha")

# Nome de cada rede Wi-Fi, perguntado como as senhas. O padrão vem do arquivo
# de respostas. Os quatro nomes de EXEMPLO são recusados: o validate_vault.yml
# os barra (reescreveriam os SSID da casa com nomes publicados no repositório),
# e a lista espelha `vault_example_ssids` de lá.
WIFI_SSIDS = [
    ("wifi_main_ssid", "main", "Nome do Wi-Fi principal"),
    ("wifi_iot_ssid", "iot", "Nome do Wi-Fi IoT"),
    ("wifi_media_ssid", "media", "Nome do Wi-Fi mídia"),
    ("wifi_guest_ssid", "guest", "Nome do Wi-Fi visitantes"),
]
SSID_EXEMPLO = {"homelab-main", "homelab-iot", "homelab-media", "homelab-guest"}

# Os seis que o 01-opnsense-setup.yml exige (validate_vault). Até 12/10/2026
# ficavam vazios "porque bcrypt não sai da stdlib" — mas sai do módulo `bcrypt`
# ou do `mkpasswd` (pacote whois), e os vazios obrigavam a um `ansible-vault
# edit` à mão. Agora: admin com senha gerada; root com a senha que você definiu
# na instalação (perguntada); chave do admin = a chave SSH do bootstrap;
# certificado da GUI autoassinado; senha PPPoE perguntada.
OPNSENSE_SEGREDOS = [
    "opnsense_root_password_hash",
    "opnsense_admin_password_hash",
    "opnsense_admin_authorizedkeys_b64",
    "opnsense_pppoe_password_b64",
    "opnsense_gui_cert_crt_b64",
    "opnsense_gui_cert_key_b64",
]


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


def bcrypt_hash(senha: str) -> str:
    """bcrypt pelo módulo `bcrypt`, senão pelo `mkpasswd` — senha sempre pelo
    stdin, nunca por argumento (apareceria no `ps`)."""
    try:
        import bcrypt

        h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        mk = shutil.which("mkpasswd")
        if not mk:
            die("sem bcrypt para os hashes do OPNsense",
                hint="Instale o pacote `whois` (traz o mkpasswd) ou `python3-bcrypt`.")
        r = subprocess.run([mk, "-m", "bcrypt", "-s"], input=senha,
                           capture_output=True, text=True)
        h = r.stdout.strip()
    if not h.startswith(("$2b$", "$2y$")):
        die("hash bcrypt em formato inesperado — firewall ficaria sem GUI")
    return h


def cert_autoassinado(dominio: str) -> tuple[str, str]:
    """Par PEM da GUI do OPNsense, em base64 (é como o config.xml guarda). A
    chave privada só toca um diretório temporário 0700, apagado na saída."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        k, c = Path(d) / "k", Path(d) / "c"
        r = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
             "ec_paramgen_curve:prime256v1", "-nodes", "-days", "825",
             "-subj", f"/CN={dominio}",
             "-addext", f"subjectAltName=DNS:{dominio},DNS:*.{dominio}",
             "-keyout", str(k), "-out", str(c)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            die("openssl falhou ao gerar o certificado da GUI", hint=r.stderr.strip()[:200])
        return (base64.b64encode(c.read_bytes()).decode(),
                base64.b64encode(k.read_bytes()).decode())


def segredos_opnsense(existente: dict | None, ssh_pub: str, dominio: str,
                      cache: dict, trocar: bool = False, admin_user: str = "",
                      ) -> tuple[dict, list[tuple[str, str, str]]]:
    """Valor já presente no vault é preservado. `cache` faz prod e dev
    compartilharem o mesmo valor — e perguntar uma vez só."""
    e = existente or {}
    out: dict[str, str] = {}
    humanas: list[tuple[str, str, str]] = []

    pw = e.get("opnsense_admin_password", "")
    tem_hash = bool(e.get("opnsense_admin_password_hash"))
    if tem_hash and not trocar:
        out["opnsense_admin_password_hash"] = e["opnsense_admin_password_hash"]
    else:
        # Hash e senha nascem juntos: senha nova com hash antigo seria
        # credencial entregue que não abre nada.
        if "admin_pw" not in cache:
            novo = perguntar_senha("OPNsense — admin nomeado (GUI, SSH e sudo)",
                                   gen("wifi"), atual=tem_hash, minimo=MINIMO_ADMIN,
                                   usuarios=(admin_user,))
            cache["admin_pw"] = pw if novo is None else novo
            cache["admin_hash"] = (e["opnsense_admin_password_hash"] if novo is None
                                   else bcrypt_hash(novo))
        pw = cache["admin_pw"]
        out["opnsense_admin_password_hash"] = cache["admin_hash"]
    out["opnsense_admin_password"] = pw
    if pw:
        humanas.append(("OPNsense — admin nomeado (GUI e sudo)", "opnsense_admin_password", pw))

    tem_root = bool(e.get("opnsense_root_password_hash"))
    if tem_root and not trocar:
        out["opnsense_root_password_hash"] = e["opnsense_root_password_hash"]
    else:
        if "root_hash" not in cache:
            print("\n  OPNsense: a senha de root que você definiu na instalação. O apply")
            print("  a grava como hash; outra senha aqui troca a de root do firewall.")
            if tem_root:
                print(f"    {_c('90', 'Enter mantém a atual.')}")
            while True:
                a = ask_secret("Senha de root do OPNsense", obrigatorio=not tem_root)
                if not a:
                    cache["root_hash"] = e["opnsense_root_password_hash"]
                    break
                if len(a) < MINIMO_ADMIN or senha_recusada(a, (admin_user,)):
                    print(f"    (mínimo {MINIMO_ADMIN} caracteres, diferente do usuário e de senha"
                          " óbvia — o apply grava esta como a nova senha de root)")
                    continue
                if a == ask_secret("Repita"):
                    cache["root_hash"] = bcrypt_hash(a)
                    break
                print("    (não conferem)")
        out["opnsense_root_password_hash"] = cache["root_hash"]

    if e.get("opnsense_pppoe_password_b64"):
        out["opnsense_pppoe_password_b64"] = e["opnsense_pppoe_password_b64"]
    else:
        if "pppoe" not in cache:
            print(f"    {_c('90', 'Conta do PROVEDOR. Enter deixa em branco; o playbook acusa depois.')}")
            v = ask_secret("Senha PPPoE do provedor", obrigatorio=False)
            cache["pppoe"] = base64.b64encode(v.encode()).decode() if v else ""
        out["opnsense_pppoe_password_b64"] = cache["pppoe"]

    out["opnsense_admin_authorizedkeys_b64"] = (
        e.get("opnsense_admin_authorizedkeys_b64")
        or base64.b64encode((ssh_pub.strip() + "\n").encode()).decode())

    if e.get("opnsense_gui_cert_crt_b64") and e.get("opnsense_gui_cert_key_b64"):
        out["opnsense_gui_cert_crt_b64"] = e["opnsense_gui_cert_crt_b64"]
        out["opnsense_gui_cert_key_b64"] = e["opnsense_gui_cert_key_b64"]
    else:
        if "cert" not in cache:
            cache["cert"] = cert_autoassinado(dominio)
        out["opnsense_gui_cert_crt_b64"], out["opnsense_gui_cert_key_b64"] = cache["cert"]

    return out, humanas


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


def senha_recusada(senha: str, usuarios: tuple[str, ...] = ()) -> bool:
    """Igual (sem distinguir caixa) ao nome de usuário ou a um padrão óbvio."""
    proibidas = {p.casefold() for p in (*SENHAS_PROIBIDAS, *usuarios) if p}
    return senha.casefold() in proibidas


def perguntar_senha(descricao: str, sugestao: str, *, atual: bool = False,
                    minimo: int = 8, maximo: int | None = None,
                    usuarios: tuple[str, ...] = ()) -> str | None:
    """Senha que uma pessoa usa. Enter aceita a sugestão — ou, com `atual`,
    mantém a do vault (devolve None, sem exibi-la). Digitada: sem eco, duas
    vezes, dentro dos limites, e diferente do nome de usuário (`usuarios`) e
    dos padrões óbvios (SENHAS_PROIBIDAS)."""
    print(f"\n  {descricao}")
    if atual:
        print(f"    {_c('90', 'Enter mantém a atual.')}")
    else:
        print(f"    sugestão: {sugestao}   {_c('90', '(Enter aceita)')}")
    while True:
        v = ask_secret("Senha", obrigatorio=False)
        if not v:
            return None if atual else sugestao
        if len(v) < minimo or (maximo and len(v) > maximo):
            faixa = f"{minimo} a {maximo}" if maximo else f"no mínimo {minimo}"
            print(f"    (precisa ter {faixa} caracteres)")
            continue
        if senha_recusada(v, usuarios):
            print("    (igual ao nome de usuário ou a uma senha óbvia — escolha outra)")
            continue
        if v == ask_secret("Repita"):
            return v
        print("    (não conferem)")


def perguntar_ssid(descricao: str, padrao: str) -> str:
    while True:
        v = ask(descricao, "" if padrao.lower() in SSID_EXEMPLO else padrao)
        if v.lower() in SSID_EXEMPLO:
            print("    (nome de exemplo do repositório — o validate_vault.yml o recusa)")
            continue
        return v


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
        # Sem padrão de propósito: é o usuário que 00-node-bootstrap.yml CRIA
        # nos nós do Swarm e o mesmo pelo qual o hosts.ini os alcança.
        "SSH_USER": ask("Usuário de sistema nos nós do Swarm"),
    }

    # O prefixo vem ANTES da rede de hoje porque os convidados (manager,
    # worker, NAS, dev) não existem antes do Terraform: num parque novo — o
    # único caminho suportado — eles nascem já no endereço-alvo, e é nele que
    # o Ansible os alcança. O padrão é esse endereço; Enter aceita.
    print(
        _c("1", "\n  Rede-ALVO — o plano de VLANs.")
        + "\n  O prefixo abaixo vira <prefixo>.<vlan>.x nos endereços-alvo."
    )
    prefixo = ask("Prefixo das VLANs (ex.: 192.168)", "192.168")
    respostas["ALVO_PREFIXO"] = prefixo

    print(
        _c("1", "\n  Rede de HOJE — é por onde o Ansible vai alcançar os hosts.")
        + "\n  Convidados criados pelo Terraform já vêm com o endereço-alvo."
    )
    respostas |= {
        "ATUAL_GATEWAY": ask("Gateway atual (quem roteia hoje)"),
        "ATUAL_MANAGER": ask("IP do nó manager do Swarm", f"{prefixo}.20.10"),
        "ATUAL_WORKER": ask("IP do worker", f"{prefixo}.20.11", obrigatorio=False),
        "ATUAL_HV1": ask("IP do hypervisor primário"),
        "ATUAL_HV2": ask("IP do hypervisor secundário", obrigatorio=False),
        "ATUAL_OMV": ask("IP do NAS/OMV", f"{prefixo}.20.15", obrigatorio=False),
        "ATUAL_OPNSENSE": ask("IP do OPNsense", obrigatorio=False),
        "ATUAL_OPENWRT": ask("IP do OpenWrt", obrigatorio=False),
        "ATUAL_DEV": ask("IP do CT de desenvolvimento", f"{prefixo}.20.20", obrigatorio=False),
    }

    # Sim ou não, e não um IP: a fonte interna de cada host é o gateway da rede
    # dele (`ansible_facts.default_ipv4.gateway`, em inventories/_common/all.yml).
    # Um IP único deixava sem NTP quem não é da LAN, bloqueado entre VLANs.
    print(
        "\n  O firewall serve NTP no gateway de cada VLAN? Não, ou ainda não"
        "\n  roteia: só as fontes públicas valem."
    )
    respostas["ATUAL_NTP"] = (
        "sim" if confirm("Usar o gateway de cada host como NTP interno") else ""
    )

    print(_c("1", "\n  Nomes dos nós Proxmox — o Terraform os usa como destino."))
    respostas |= {
        "HV1_NOME": ask("Nome do nó primário"),
        "HV2_NOME": ask("Nome do nó secundário", obrigatorio=False),
    }

    # Estas quatro eram, até aqui, edição manual do hosts.ini depois do
    # bootstrap — e o playbook do Proxmox aborta sem as duas primeiras. Toda
    # pergunta feita aqui é um passo a menos no runbook de instalação, que é o
    # motivo de o bootstrap existir.
    print(
        _c("1", "\n  Proxmox — storage de backup e NIC de WAN.")
        + "\n  Enter aceita o padrão; o hosts.ini gerado leva os valores para"
        "\n  [all:vars], onde 00-proxmox-setup.yml e 06-backup.yml os leem."
    )
    respostas |= {
        # UM id por nó: o share no NAS é o mesmo, mas cada hipervisor o
        # registra com o próprio id de storage — é o que o inventário de
        # exemplo já dizia ("os dois nós referenciam o mesmo share por nomes
        # diferentes") sem que nada no gerador tratasse. Em [all:vars], o
        # segundo nó reescrevia o próprio storage.cfg com o id do primeiro.
        "BACKUP_STORAGE_1": ask("Id do storage de backup no nó primário", "nas-backup"),
        "BACKUP_STORAGE_2": ask("Id do storage de backup no nó secundário", "nas-backup"),
        "BACKUP_SHARE": ask("Nome do share CIFS no NAS (o mesmo nos dois)", "backup"),
        "WAN_IFACE": ask(
            "Interface física de WAN (fora da bridge; vazio se não houver)",
            obrigatorio=False,
        ),
    }

    respostas["DATA"] = date.today().isoformat()

    for chave, valor in respostas.items():
        texto = texto.replace(f"@@{chave}@@", valor)
    # Resposta vazia deixa `chave: ` com espaço no fim, e o yamllint do
    # `make check` reprova o arquivo inteiro.
    return "\n".join(linha.rstrip() for linha in texto.splitlines()) + "\n"


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
def montar_segredos(existente: dict | None, ssh_pub: str, dominio: str,
                    cache: dict, resp: dict | None = None, trocar: bool = False,
                    ) -> tuple[dict, list[tuple[str, str, str]], set[str]]:
    """Devolve (segredos, credenciais_humanas, trocadas). Chave já presente no
    vault é PRESERVADA — salvo, com `trocar`, as que uma pessoa digita, que
    são perguntadas de novo (Enter mantém). `trocadas` são as que mudaram e
    precisam sobrescrever o vault."""
    step("3. Segredos")
    e = existente or {}
    usuarios = tuple(u for u in ((resp or {}).get("ssh_user", ""),
                                 (resp or {}).get("dominio_admin_user", "")) if u)
    segredos: dict[str, str] = {}
    humanas: list[tuple[str, str, str]] = []
    trocadas: set[str] = set()
    preservadas = 0

    for chave, tipo, humano, descricao in GENERATED:
        if not humano:
            if e.get(chave):
                segredos[chave] = e[chave]
                preservadas += 1
            else:
                segredos[chave] = gen(tipo)
            continue
        if e.get(chave) and not trocar:
            valor = e[chave]
            preservadas += 1
        else:
            if chave not in cache:
                minimo, maximo = LIMITES_SENHA.get(chave, (8, None))
                novo = perguntar_senha(descricao, gen("wifi"), atual=bool(e.get(chave)),
                                       minimo=minimo, maximo=maximo, usuarios=usuarios)
                cache[chave] = e[chave] if novo is None else novo
            valor = cache[chave]
            if valor != e.get(chave):
                trocadas.add(chave)
        segredos[chave] = valor
        humanas.append((descricao, chave, valor))

    ok(f"{len(GENERATED) - preservadas} definidos agora, {preservadas} preservados do vault atual")

    wifi_resp = (resp or {}).get("wifi", {}) or {}
    for chave, campo, descricao in WIFI_SSIDS:
        if e.get(chave) and not trocar:
            segredos[chave] = e[chave]
            continue
        if chave not in cache:
            cache[chave] = perguntar_ssid(descricao, e.get(chave) or wifi_resp.get(campo, ""))
        segredos[chave] = cache[chave]
        if segredos[chave] != e.get(chave):
            trocadas.add(chave)

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

    opn, opn_humanas = segredos_opnsense(existente, ssh_pub, dominio, cache, trocar,
                                         admin_user=(resp or {}).get("dominio_admin_user", ""))
    segredos.update(opn)
    humanas += opn_humanas
    for k in ("opnsense_admin_password", "opnsense_admin_password_hash",
              "opnsense_root_password_hash"):
        if opn.get(k) and opn[k] != e.get(k):
            trocadas.add(k)
    return segredos, humanas, trocadas


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
        "# Rodar o bootstrap de novo só COMPLETA este arquivo (chave vazia ou",
        "# ausente); valor preenchido só muda com `make bootstrap ARGS=--senhas`.",
        "",
        f"homelab_domain: {d}",
        f"ssh_public_key: {ssh_pub!r}",
        "",
        "# Usuário só leitura (PVEAuditor) do exporter do Proxmox; a senha vem abaixo.",
        "pve_exporter_user: 'prometheus@pve'",
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
        f"wifi_main_ssid: {seg.get('wifi_main_ssid') or resp.get('wifi', {}).get('main', 'homelab')!r}",
        f"wifi_iot_ssid: {seg.get('wifi_iot_ssid') or resp.get('wifi', {}).get('iot', 'homelab-iot')!r}",
        f"wifi_media_ssid: {seg.get('wifi_media_ssid') or resp.get('wifi', {}).get('media', 'homelab-media')!r}",
        f"wifi_guest_ssid: {seg.get('wifi_guest_ssid') or resp.get('wifi', {}).get('guest', 'homelab-guest')!r}",
        "",
        "# OPNsense — hashes bcrypt e valores em base64 (como o config.xml guarda).",
        f"opnsense_admin_username: {resp['dominio_admin_user']!r}",
        "opnsense_admin_fullname: 'Administrador do homelab'",
        f"opnsense_admin_password: {seg.get('opnsense_admin_password', '')!r}",
    ]
    for chave in OPNSENSE_SEGREDOS:
        linhas.append(f"{chave}: {seg.get(chave, '')!r}")
    linhas += [
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


def render_local_all(resp: dict) -> str:
    """group_vars/all/99-local.yml: o que é do operador e não pode morar no
    00-common.yml versionado. `system_user` precisa ser o mesmo `ssh_user` do
    hosts.ini — um cria a conta, o outro entra por ela.

    `mgmt_allowed_cidrs`: quem chega às portas administrativas dos nós do
    Swarm (9000 do Portainer, 1514 do syslog) — tasks/docker_user_rules.yml.
    O syslog vem do firewall e do NAS (VLAN 20) e do Proxmox (LAN), e o
    operador está na LAN: uma faixa só fechava a porta a alguém (13/10/2026).
    LAN = gateway atual /24 (máscara fixa no template do OPNsense); homelab =
    gateway-alvo /cidr; gerência = <prefixo>.10.0/24, pela convenção
    <prefixo>.<vlan>.x do prefixo perguntado no passo de rede.

    `traefik_admin_cidrs`: quem chega às GUIs de equipamento e ao Portainer
    publicados pelo Traefik (middleware `rede-admin`, rotas.yml.j2). É a
    `mgmt_allowed_cidrs` mais a MAIN (<prefixo>.30.0/24), de onde o operador
    acessa. Sem a lista, o Traefik na VLAN 20 fazia a ponte de qualquer VLAN
    que o alcança (MEDIA, IoT) até a gerência (15/10/2026)."""
    import ipaddress

    alvo = resp["rede_alvo"]
    prefixo = ".".join(alvo["gateway"].split(".")[:2])
    redes = [
        ipaddress.ip_interface(f"{resp['rede_atual']['gateway']}/24").network,
        ipaddress.ip_network(f"{prefixo}.10.0/24"),
        ipaddress.ip_interface(f"{alvo['gateway']}/{alvo['cidr']}").network,
    ]
    linhas = "".join(f"  - {r}\n" for r in redes)
    admin = "".join(f"  - {r}\n" for r in redes + [ipaddress.ip_network(f"{prefixo}.30.0/24")])
    return (
        f"# Gerado por IaC/bootstrap/bootstrap.py em {date.today().isoformat()}.\n"
        "# NÃO versionado. Vence o 00-common.yml ao lado (ordem alfabética).\n"
        f"system_user: {resp['ssh_user']!r}\n"
        "# LAN, gerência (VLAN 10) e homelab (VLAN 20): ver render_local_all.\n"
        f"mgmt_allowed_cidrs:\n{linhas}"
        "# A lista acima mais a MAIN (VLAN 30): ver render_local_all.\n"
        f"traefik_admin_cidrs:\n{admin}"
    )


# O NAS nasce da imagem cloud do Debian: o cloud-init põe a chave no usuário
# padrão `debian` (sudo sem senha) e recusa root ("Please login as the user
# debian"). O 00-omv-setup.yml usa `become`, então entra por ele.
def render_hosts(env: str, resp: dict) -> str:
    a = resp["rede_atual"]
    n = resp["nos"]
    usuario = resp["ssh_user"]

    def linha(nome: str, ip: str, user: str = "", extra: str = "") -> str:
        if not ip:
            return f"# {nome} ansible_host=<PREENCHER> ansible_user={user or usuario}"
        return f"{nome} ansible_host={ip} ansible_user={user or usuario}{extra}"

    return f"""\
# Gerado por IaC/bootstrap/bootstrap.py em {date.today().isoformat()}
#
# Os IPs abaixo são os da rede COMO ELA É HOJE — é por aqui que o Ansible
# alcança os hosts. O plano de VLANs (rede-alvo) vive em group_vars/network/
# e descreve o destino, não o presente. Confundir os dois produz timeout de SSH,
# cujo sintoma não diz nada sobre a causa.

[homelab_managers]
{linha("node1", a["manager"])}

[homelab_workers]
{linha("node2", a.get("worker", ""))}

[opnsense]
{linha("opnsense", a.get("opnsense", ""), resp["dominio_admin_user"])}

[openwrt]
{linha("openwrt", a.get("openwrt", ""), "root", " ansible_shell_type=sh")}

[proxmox]
{linha(n["primario"]["nome"], a.get("hypervisor1", ""), "root", _wan(resp) + _storage(resp, 1) + _pci(resp, 1))}
{linha(n.get("secundario", {}).get("nome", "hv2"), a.get("hypervisor2", ""), "root", _storage(resp, 2) + _pci(resp, 2))}

[omv]
{linha("omv", a.get("omv", ""), "debian")}

[homelab:children]
homelab_managers
homelab_workers

[network:children]
opnsense
openwrt

[all:vars]
{_ntp(a)}
{_proxmox_vars(resp)}"""


def _ntp(atual: dict) -> str:
    """NTP interno: liga ou não. Opcional de propósito: `inventories/_common/all.yml`
    acrescenta as públicas do ntp.br como fallback, porque usar o firewall
    pressupõe que ele já roteia E tem o serviço de tempo ligado. Sem isso o host
    fica sem fonte, e relógio sem fonte não dá erro — derrapa.

    Qualquer valor não vazio liga: respostas antigas guardavam o IP do servidor,
    e continuam valendo sem pergunta nova."""
    ligado = bool(atual.get("ntp_interno"))
    cabecalho = (
        "# NTP interno = gateway da rede de cada host (o firewall). As públicas do\n"
        "# ntp.br entram como fallback via inventories/_common/all.yml.\n"
    )
    if not ligado:
        return cabecalho + "# ntp_interno=true   (desligado: só as fontes públicas)\n"
    return cabecalho + "ntp_interno=true\n"


def _url_secundaria(resp: dict) -> str:
    """Sem o IP do segundo hipervisor, a variável sai COMENTADA em vez de
    receber `https://:8006/`. Uma URL sem host é uma string não-vazia: passaria
    por qualquer checagem de "preenchido" e só falharia no apply, com erro de
    conexão que não aponta para o tfvars. Ausente, o Terraform cobra a variável
    obrigatória pelo nome — que é o desfecho desejado."""
    ip = resp["rede_atual"].get("hypervisor2", "")
    if not ip:
        return ('# Segundo hipervisor não informado no bootstrap. O Terraform vai\n'
                '# cobrar esta variável obrigatória; preencha antes do plan.\n'
                '# proxmox_api_url_secondary = "https://<IP-DO-NO-2>:8006/"')
    return f'proxmox_api_url_secondary = "https://{ip}:8006/"'


def _wan(resp: dict) -> str:
    """A NIC de WAN é atributo DO NÓ PRIMÁRIO, não do parque: é ele que faz o
    passthrough para o firewall. Como variável de host ela exclui a interface
    da bridge só onde isso é verdade — em `[all:vars]` excluiria também uma NIC
    homônima e em uso no secundário, que ficaria fora da bridge sem erro."""
    wan = (resp.get("proxmox", {}) or {}).get("wan_interface") or ""
    return f" proxmox_wan_interface={wan}" if wan else ""


def _pci(resp: dict, no: int) -> str:
    """Endereço PCI de cada dispositivo em passthrough, na linha do nó que o
    tem: as WANs no primário, a controladora SATA do NAS no secundário. Quem lê
    é o `00-proxmox-api-token.yml`, que cria o PCI mapping como root — o token
    do Terraform só pode usar hostpci por mapping. Valores reais, não defaults:
    um endereço errado aqui passa o dispositivo errado para a VM."""
    chaves = ("opnsense_wan_pci_id", "opnsense_wan2_pci_id") if no == 1 else ("omv_hba_pci_id",)
    return "".join(f" {k}={resp[k]}" for k in chaves if resp.get(k))


def _storage(resp: dict, no: int) -> str:
    """Id do storage de backup DESTE nó. Per-host porque os dois hipervisores
    registram o mesmo share do NAS com nomes diferentes — em `[all:vars]`, o
    `storage.cfg` do secundário sairia declarando o id do primário, e o vzdump
    apontaria para um storage que não existe ali."""
    px = resp.get("proxmox", {}) or {}
    valor = px.get(f"backup_storage_{'primario' if no == 1 else 'secundario'}") or ""
    return f" backup_storage={valor}" if valor else ""


def _proxmox_vars(resp: dict) -> str:
    """Variáveis que `playbooks/00-proxmox-setup.yml` e `playbooks/06-backup.yml`
    exigem do inventário. Nenhuma delas tem default no playbook: as duas
    primeiras abortam o play, e o gateway errado tira o hipervisor da rede.

    `proxmox_gateway_ip` é o gateway de HOJE, e não o da rede-alvo, porque
    acompanha o `ansible_host` de hoje no mesmo arquivo — misturar as duas
    redes num mesmo /etc/network/interfaces é o erro que o README do bootstrap
    descreve em "As duas redes"."""
    px = resp.get("proxmox", {}) or {}
    atual = resp["rede_atual"]
    vmids = px.get("backup_vmids") or []
    linhas = [
        "",
        "# Gateway que este hipervisor usa hoje. Escrito em /etc/network/interfaces",
        "# por 00-proxmox-setup.yml — ver a nota sobre `vlans` naquele playbook.",
        f"proxmox_gateway_ip={atual.get('gateway', '')}",
        "",
        "# O share é o mesmo nos dois nós; o ID DO STORAGE não — ele vai na",
        "# linha de cada host, porque cada hipervisor registra o share com o",
        "# próprio id. Ver `backup_storage` em hosts.ini.example.",
        f"proxmox_backup_share={px.get('backup_share', '')}",
    ]
    if vmids:
        linhas.append(f"backup_vmids=[{','.join(str(v) for v in vmids)}]")
    else:
        linhas += [
            "# Sem VMIDs declarados: 06-backup.yml aborta até esta lista existir,",
            "# o que é melhor do que agendar vzdump contra o convidado errado.",
            "# backup_vmids=[<VMID-1>,<VMID-2>]",
        ]
    # `proxmox_wan_interface` NÃO entra aqui: ela é per-host, e vai na linha do
    # nó primário em render_hosts. Em [all:vars] ela valeria também para o
    # secundário — que pode ter uma NIC de mesmo nome, em uso, e a veria
    # excluída da bridge sem erro nenhum.
    return "\n".join(linhas) + "\n"


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

# Dois endpoints e dois pares de token: os hipervisores são independentes, não
# um cluster. Ver IaC/terraform/proxmox/providers.tf.
proxmox_api_url_primary   = "https://{resp["rede_atual"].get("hypervisor1", "")}:8006/"
{_url_secundaria(resp)}

# Os QUATRO tokens de API NÃO ficam aqui. Quem os cria e escreve é
# `IaC/ansible/playbooks/00-proxmox-api-token.yml`, em `tokens.auto.tfvars` —
# arquivo que o Terraform carrega sozinho pelo sufixo `.auto.tfvars`. Dois
# lugares gerando o mesmo segredo seria a falha de "duas verdades": a cópia que
# ficasse para trás só apareceria como 401 no `terraform plan`.

proxmox_ssh_username = "root"

proxmox_node_primary   = "{n["primario"]["nome"]}"
proxmox_node_secondary = "{n.get("secundario", {}).get("nome", "")}"

ssh_public_key = "{ssh_pub}"

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
        ok(f"já existe: {pub}")
        return pub.read_text().strip()
    if dry:
        info("[dry-run] geraria par ed25519")
        return "ssh-ed25519 AAAA<dry-run>"

    print(
        "\n  ⚠️ Chave SEM passphrase dá acesso root à frota inteira num arquivo.\n"
        "     Com passphrase, use ssh-agent para não digitar a cada play.\n"
    )
    com_passphrase = confirm("Proteger a chave com passphrase?", default=True)
    SSH_KEY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    cmd = ["ssh-keygen", "-t", "ed25519", "-f", str(SSH_KEY), "-C", "homelab"]
    if not com_passphrase:
        cmd += ["-N", ""]
    # Sem capture_output: o ssh-keygen precisa do terminal para pedir a
    # passphrase, e capturar deixaria o prompt invisível.
    if subprocess.run(cmd).returncode != 0:
        die("ssh-keygen falhou")
    os.chmod(SSH_KEY, 0o600)
    ok(f"par gerado em {SSH_KEY}")
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


def completar_vault(destino: Path, existente: dict, conteudo: str, dry: bool,
                    sobrescrever: set[str] | frozenset = frozenset()) -> None:
    """Vault existente: só entra chave AUSENTE ou VAZIA nele. Valor preenchido
    nunca é tocado — rotação é decisão, não efeito colateral. Antes, vault
    existente era pulado inteiro: chave nova no bootstrap só entrava com
    `ansible-vault edit`, e o segredo externo digitado na reexecução era
    perguntado e descartado."""
    import yaml

    desejado = yaml.safe_load(conteudo) or {}
    # Ausente entra mesmo vazia: variável opcional precisa EXISTIR para o
    # template (`{% if x %}` aborta em indefinida). Vazia só é trocada por valor.
    faltam = {
        k: v for k, v in desejado.items()
        if isinstance(v, str) and (k not in existente or (v and existente[k] in (None, ""))
                                   or (k in sobrescrever and v != existente.get(k)))
    }
    if not faltam:
        ok(f"{destino.relative_to(REPO)} já completo — nada a acrescentar")
        return
    if dry:
        info(f"[dry-run] completaria {destino.relative_to(REPO)}: {', '.join(sorted(faltam))}")
        return
    r = subprocess.run(
        ["ansible-vault", "view", "--vault-password-file", str(VAULT_PASS), str(destino)],
        capture_output=True, text=True, cwd=ANSIBLE,
    )
    if r.returncode != 0:
        die("não consegui abrir o vault para completar", hint=r.stderr.strip()[:200])
    texto = r.stdout
    for k, v in faltam.items():
        # Aspas simples de YAML, não yaml_valor: safe_dump de escalar fecha
        # com `...`, que no meio do arquivo encerra o documento.
        linha = f"{k}: '" + v.replace("'", "''") + "'"
        texto, n = re.subn(rf"(?m)^{re.escape(k)}:.*$", lambda _m: linha, texto)
        if n == 0:
            texto = texto.rstrip("\n") + "\n" + linha + "\n"
    # Cifra pelo stdin para um temporário ao lado e troca no fim: nem texto
    # em claro no disco, nem vault pela metade se algo falhar no meio.
    tmp = destino.with_suffix(".yml.novo")
    r = subprocess.run(
        ["ansible-vault", "encrypt", "--vault-password-file", str(VAULT_PASS),
         "--encrypt-vault-id", "default", "--output", str(tmp)],
        input=texto, capture_output=True, text=True, cwd=ANSIBLE,
    )
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        die("falha ao cifrar o vault completado", hint=r.stderr.strip()[:200])
    os.chmod(tmp, 0o600)
    os.replace(tmp, destino)
    ok(f"{destino.relative_to(REPO)} completado: {', '.join(sorted(faltam))}")


def cifrar_vault(env: str, conteudo: str, dry: bool, existente: dict | None = None,
                 sobrescrever: set[str] | frozenset = frozenset()) -> None:
    destino = ANSIBLE / "inventories" / env / "group_vars" / "all" / "vault.yml"
    if destino.exists():
        if existente is None:
            warn(f"{destino.relative_to(REPO)} existe mas não abriu — NÃO tocado")
            return
        completar_vault(destino, existente, conteudo, dry, sobrescrever)
        return
    if dry:
        info(f"[dry-run] cifraria {destino.relative_to(REPO)}")
        return
    if not gitignored(destino):
        die(f"{destino.relative_to(REPO)} não está no .gitignore")

    destino.parent.mkdir(parents=True, exist_ok=True)
    # `--encrypt-vault-id default` é obrigatório: com cwd=ANSIBLE o
    # ansible.cfg já declara `vault_password_file`, e somado à flag o
    # ansible-vault enxerga dois vault-ids `default` e recusa cifrar ("The
    # vault-ids default,default are available to encrypt"). Ler não sofre disso.
    # Passa pelo stdin do ansible-vault em vez de gravar texto puro e cifrar
    # depois: sem arquivo intermediário, não há janela para Ctrl-C deixar o
    # segredo em claro no disco.
    r = subprocess.run(
        [
            "ansible-vault", "encrypt_string",
            "--vault-password-file", str(VAULT_PASS),
            "--encrypt-vault-id", "default",
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
        ["ansible-vault", "encrypt", "--vault-password-file", str(VAULT_PASS),
         "--encrypt-vault-id", "default", str(destino)],
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
    p.add_argument("--senhas", action="store_true",
                   help="repergunta as senhas de pessoa e os nomes de Wi-Fi (Enter mantém o atual)")
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

    resp = carregar_respostas()
    if not resp.get("ssh_user"):
        die(
            f"ssh_user ausente em {ANSWERS.relative_to(REPO)}",
            hint="Rode `make bootstrap ARGS=--reconfigure` para responder de novo.",
        )
    resp.setdefault("dominio_admin_user", resp.get("admin_user", "netadmin"))
    resp.setdefault("alerta_email", resp.get("email_alerta", ""))

    garantir_senha_vault(dry)
    ssh_pub = garantir_chave_ssh(dry)

    cache_opnsense: dict = {}
    for env in ENVS:
        existente = vault_existente(env)
        seg, humanas, trocadas = montar_segredos(existente, ssh_pub, resp["dominio"],
                                                 cache_opnsense, resp, args.senhas)
        # dev tem senhas próprias para o que isola ambiente (render_vault gera
        # outras a cada execução): trocar em prod não pode reescrevê-las em dev.
        if env == "dev":
            trocadas -= {"portainer_admin_password", "grafana_admin_password"}

        step(f"5. Gerando alvos — {env}")
        cifrar_vault(env, render_vault(env, resp, seg, ssh_pub), dry, existente, trocadas)
        write(ANSIBLE / "inventories" / env / "hosts.ini", render_hosts(env, resp), mode=0o600, dry=dry)
        write(
            ANSIBLE / "inventories" / env / "group_vars" / "all" / "99-local.yml",
            render_local_all(resp), mode=0o600, dry=dry,
        )
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

  3. Instale a chave pública nos hosts, e confira:
       cd IaC/ansible && ansible -m ping all

  4. Valide o repositório:
       make check
"""
    )
    ok("bootstrap concluído")


if __name__ == "__main__":
    main()
