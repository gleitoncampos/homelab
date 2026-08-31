#!/usr/bin/env python3
"""Renderiza o daemon led-health e seu init procd, e checa o que o gate não vê.

Motivo: `shellcheck` só olha `*.sh` e `yamllint` ignora `.j2`. Estes dois
templates viram SHELL no equipamento, e um erro de sintaxe do Jinja ou um laço
mal fechado só apareceria no AP — onde o sintoma é um painel de LEDs que nunca
acende, indistinguível de "está tudo bem, nada a sinalizar".

O que este teste afirma, e por que cada afirmação existe:

1. Os dois renders passam em `sh -n`. É o mínimo, e é o que pega Jinja
   quebrado virando shell inválido.
2. `START` do init é MAIOR que 96. A ordem de boot do OpenWrt é
   `done`(95) → `led`(96) → led-health; subir antes faz o boot sobrescrever o
   estado e o painel mente até a próxima transição. É um número mágico cuja
   justificativa é externa ao repositório, então o teste a congela.
3. Os dois ritmos de piscada ficam ACIMA de 200ms. O OpenWrt de fábrica pisca
   o mesmo LED amarelo em 50/100/200ms, sendo 200ms "gravando firmware".
   Um valor nosso nessa faixa faz sysupgrade e queda de internet parecerem
   iguais. É a única regra do desenho que um ajuste inocente de variável
   quebraria sem deixar rastro.
"""
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

RAIZ = Path(__file__).resolve().parents[3]
TEMPLATES = RAIZ / "IaC" / "ansible" / "templates" / "network"

# Nomes de LED e alvos anycast públicos — nomenclatura de hardware, não
# topologia. Nada aqui é dado real deste lab.
CONTEXTO = {
    "openwrt_led_blue": "blue:status",
    "openwrt_led_yellow": "yellow:status",
    "openwrt_led_radios": ["radio0", "radio1"],
    "openwrt_led_ping_targets": ["192.0.2.53", "198.51.100.53"],
    "openwrt_led_ping_timeout": 2,
    "openwrt_led_poll_interval": 3,
    "openwrt_led_net_grace": 10,
    "openwrt_led_blink_radio_ms": 1000,
    "openwrt_led_blink_net_ms": 600,
    "openwrt_led_reaffirm_cycles": 20,
    "openwrt_led_health_path": "/usr/bin/led-health",
}

falhas = []


def checar(condicao, mensagem):
    if condicao:
        print(f"  OK {mensagem}")
    else:
        print(f"  FALHOU {mensagem}")
        falhas.append(mensagem)


def render(nome):
    # trim_blocks/lstrip_blocks iguais ao que o Ansible usa, senão o render
    # daqui não é o render de lá.
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    return env.get_template(nome).render(**CONTEXTO)


def sh_valido(texto, rotulo):
    proc = subprocess.run(
        ["sh", "-n"], input=texto, text=True, capture_output=True, check=False
    )
    checar(proc.returncode == 0, f"{rotulo}: sh -n aceita o render")
    if proc.returncode != 0:
        print(f"    {proc.stderr.strip()}")


print("Renderizando os templates do led-health...")
daemon = render("openwrt_led_health.sh.j2")
init = render("openwrt_led_health_init.j2")

sh_valido(daemon, "daemon")
# O init tem shebang `#!/bin/sh /etc/rc.common`; o corpo ainda é sh válido.
sh_valido(init, "init procd")

print("\nAfirmações sobre o init:")
linha_start = [ln for ln in init.splitlines() if ln.startswith("START=")]
checar(len(linha_start) == 1, "declara exatamente um START")
if linha_start:
    valor = int(linha_start[0].split("=")[1])
    checar(
        valor > 96,
        f"START={valor} é maior que 96 (done=95, led=96 precisam vir antes)",
    )

print("\nAfirmações sobre os ritmos de piscada:")
for var, rotulo in (
    ("BLINK_RADIO_MS", "rádio"),
    ("BLINK_NET_MS", "internet"),
):
    linha = [ln for ln in daemon.splitlines() if ln.startswith(f"{var}=")]
    checar(len(linha) == 1, f"{var} aparece uma vez")
    if linha:
        ms = int(linha[0].split("=")[1])
        checar(
            ms > 200,
            f"{var}={ms}ms fica acima da faixa de fábrica "
            f"(50/100/200ms), então não se confunde com {rotulo}",
        )

print("\nAfirmações sobre o daemon:")
checar(
    "FABRICA)" in daemon and "trap " in daemon,
    "o trap restaura o estado de fábrica no encerramento",
)
checar(
    "REAFIRMAR_A_CADA" in daemon,
    "reafirma o estado periodicamente (painel não fica sequestrável)",
)
# ⚠️ Invariante negativa, e ela vale mais que as positivas: o daemon NÃO pode
# escrever nos LEDs mt76-phy*. São acionados pelo chip MediaTek, e escrever no
# trigger deles é a única coisa aqui que alcança registrador do rádio. Numa
# investigação de 31/08/2026 essa escrita não pôde ser descartada como causa
# de uma queda de Wi-Fi, e não há ganho que a justifique.
# A checagem ignora comentários de propósito: a invariante é sobre CÓDIGO
# executável. O cabeçalho do template cita `mt76-phy*` justamente para
# explicar por que não se deve escrever nele.
codigo = "\n".join(
    ln for ln in daemon.splitlines() if not ln.lstrip().startswith("#")
)
checar(
    "mt76" not in codigo,
    "nenhuma linha executável do daemon referencia mt76-phy*",
)
checar(
    "interfaces[*].ifname" in daemon,
    "a checagem de rádio não confia só em up=true",
)
checar(
    "{{" not in daemon and "{%" not in daemon,
    "não sobrou Jinja não resolvido no daemon",
)
checar(
    "{{" not in init and "{%" not in init,
    "não sobrou Jinja não resolvido no init",
)

if falhas:
    print(f"\n✗ render-openwrt-led: {len(falhas)} falha(s)")
    sys.exit(1)
print("\n✓ render-openwrt-led")
