#!/usr/bin/env python3
"""Confere o openwrt-wifi-neighbors (20/10/2026) com o ucode e o promtool de verdade.

SSID e BSSID vêm de terceiros e viram label: um vizinho com aspas, barra ou
quebra de linha no nome não pode quebrar o `.prom` nem injetar métrica nele.
O script roda no ucode do mesmo release do AP (imagem `openwrt/rootfs`) contra
um scan fictício, e a saída passa pelo `promtool` da versão do compose.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ANSIBLE = Path(__file__).resolve().parents[1]
REPO = ANSIBLE.parents[1]
SCRIPT = ANSIBLE / "templates/network/openwrt_wifi_neighbors.uc.j2"
# Mesmo release do AP (DISTRIB_RELEASE em 20/10/2026).
OPENWRT = "openwrt/rootfs:x86-64-25.12.5"
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


if not shutil.which("docker"):
    print("\033[33m  ! docker ausente — render-openwrt-wifi-neighbors pulado\033[0m")
    sys.exit(0)

texto = SCRIPT.read_text(encoding="utf-8")
checar(not any(m in texto for m in ("{{", "{%", "{#")), "o script não tem sintaxe que o Jinja do lookup('template') interprete")

INJECAO = 'x"} 1\nwifi_neighbors_seen{banda="2g"} 999\n#'
scan = {"results": [
    {"ssid": 'com "aspas"', "bssid": "bssid-1", "channel": 1, "signal": -50,
     "ht_operation": {"primary_channel": 1, "secondary_channel_offset": "no secondary", "channel_width": 20}},
    {"ssid": "barra\\invertida", "bssid": "bssid-2", "channel": 6, "signal": -60,
     "ht_operation": {"primary_channel": 6, "secondary_channel_offset": "above", "channel_width": 2040}},
    {"ssid": INJECAO, "bssid": "bssid-3", "channel": 11, "signal": -70},
    {"bssid": "bssid-4", "channel": 36, "signal": -80,
     "vht_operation": {"channel_width": 80, "center_freq_1": 42, "center_freq_2": 0}},
    # caso real do 2,4 GHz (20/10/2026): VHT "40" sem canal secundário no HT é 20 MHz
    {"ssid": "vht40-falso", "bssid": "bssid-6", "channel": 3, "signal": -75,
     "ht_operation": {"primary_channel": 3, "secondary_channel_offset": "no secondary", "channel_width": 2040},
     "vht_operation": {"channel_width": 40, "center_freq_1": 3, "center_freq_2": 0}},
    # mesmo BSSID, mais fraco: não pode virar série duplicada
    {"ssid": "duplicada", "bssid": "bssid-1", "channel": 1, "signal": -90},
    # sem sinal: descartada
    {"ssid": "incompleta", "bssid": "bssid-5", "channel": 3},
]}

img = re.search(r"quay\.io/prometheus/prometheus:[^\"\s]+",
                (REPO / "docker/monitoring/swarm/compose.yml").read_text(encoding="utf-8"))
checar(img is not None, "achei a imagem do Prometheus no compose")


def rodar(rotulo, scan_bytes):
    """Roda o script no ucode do AP contra o scan dado e passa a saída no promtool."""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "scan.json").write_bytes(scan_bytes)
        shutil.copy(SCRIPT, Path(tmp, "script.uc"))
        roda = subprocess.run(
            ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{tmp}:/t", OPENWRT,
             "ucode", "-S", "/t/script.uc", "phy0-ap0", "2g", "/t/out/wifi.prom", "/t/scan.json"],
            capture_output=True, text=True)
        checar(roda.returncode == 0, f"{rotulo}: o script roda no ucode do {OPENWRT} {roda.stderr.strip()}")
        saida = Path(tmp, "out/wifi.prom")
        bruto = saida.read_bytes() if saida.exists() else b""
        checar(not Path(tmp, "out/wifi.prom.tmp").exists(), f"{rotulo}: não sobra o .tmp da gravação atômica")
    if img and bruto:
        lint = subprocess.run(
            ["docker", "run", "--rm", "-i", "--entrypoint", "promtool", img.group(0), "check", "metrics"],
            input=bruto, capture_output=True)
        checar(lint.returncode == 0, f"{rotulo}: o promtool aceita a saída ({img.group(0)}) "
               f"{lint.stdout.decode(errors='replace').strip()} {lint.stderr.decode(errors='replace').strip()}")
    return bruto.decode("utf-8", errors="replace")


prom = rodar("scan fictício", json.dumps(scan).encode())

sinais = [ln for ln in prom.splitlines() if ln.startswith("wifi_neighbor_signal_dbm{")]
checar(len(sinais) == 5, f"uma série por BSSID válido, sem duplicata nem incompleta ({len(sinais)})")
contagens = [ln for ln in prom.splitlines() if ln.startswith("wifi_neighbors_seen")]
checar(contagens == ['wifi_neighbors_seen{banda="2g"} 5'],
       f"a injeção pelo SSID não cria métrica nova ({contagens})")
checar('ssid="com \\"aspas\\""' in prom, "aspas no SSID saem escapadas")
checar('ssid="barra\\\\invertida"' in prom, "barra invertida no SSID sai escapada")
checar('ssid="x\\"} 1\\nwifi_neighbors_seen{banda=\\"2g\\"} 999\\n#"' in prom,
       "quebra de linha e aspas da injeção ficam dentro do label")
checar('bssid="bssid-1"} -50' in prom, "do BSSID repetido fica o sinal mais forte")
checar('canal="6",centro="8",largura_mhz="40"' in prom, "HT40 acima: centro dois canais acima do primário")
checar('canal="36",centro="42",largura_mhz="80",ssid=""' in prom, "VHT80: centro do bloco e SSID oculto vazio")
checar('canal="11",centro="11",largura_mhz="20"' in prom, "sem HT/VHT: 20 MHz no primário")
checar('canal="3",centro="3",largura_mhz="20",ssid="vht40-falso"' in prom, "VHT 40 sem secundário no HT fica em 20 MHz")
checar('wifi_neighbor_scan_success{banda="2g"} 1' in prom, "scan com resultado marca sucesso")

# SSID são 32 bytes quaisquer: byte que não é UTF-8 válido derrubava o alvo
# inteiro no Prometheus (`invalid UTF-8 label value`, Hades 20/10/2026). O
# fixture vai em bytes porque o json.dumps não representa byte cru.
_bytes = [(b"Caf\xe9", "Caf%E9"), (b"ok\xc3", "ok%C3"), (b"\xed\xa0\x80", "%ED%A0%80"),
          (b"\xc0\xaf", "%C0%AF"), (b"\xf4\x90\x80\x80", "%F4%90%80%80"),
          (b"100% Caf\xe9", "100%25 Caf%E9"), ("Café 100%".encode(), "Café 100%")]
_cru = b'{"results":[' + b",".join(
    b'{"ssid":"' + ssid + b'","bssid":"bssid-' + str(i).encode() + b'","channel":1,"signal":-50}'
    for i, (ssid, _) in enumerate(_bytes)) + b"]}"
prom_cru = rodar("SSID em bytes crus", _cru)
for i, (ssid, esperado) in enumerate(_bytes):
    checar(f'ssid="{esperado}",bssid="bssid-{i}"' in prom_cru, f"SSID {ssid!r} sai como {esperado!r}")

# Lista vazia é o que o rpcd devolve com o scan falho ou o rádio ocupado.
prom_vazio = rodar("scan vazio", b'{"results":[]}')
checar('wifi_neighbor_scan_success{banda="2g"} 0' in prom_vazio, "scan sem nenhuma rede não marca sucesso")

if falhas:
    print(f"\n✗ render-openwrt-wifi-neighbors: {len(falhas)} falha(s)")
    sys.exit(1)
print("\n✓ render-openwrt-wifi-neighbors")
