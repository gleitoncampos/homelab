#!/usr/bin/env python3
"""hulk-idle-check: desliga o hulk após 30 amostras (1/min) seguidas ociosas.

Ocioso = utilização da GPU < GPU_MAX e da CPU < CPU_MAX. Memória não conta.
Fail-safe: GPU ilegível, CPU sem amostra anterior ou lock de keepalive zeram o
contador — nunca desligam. Chamado pelo hulk-idle.timer (systemd).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

GPU_MAX = 5.0
CPU_MAX = 10.0
AMOSTRAS = int(os.environ.get("HULK_AMOSTRAS", "30"))
ESTADO = Path(os.environ.get("HULK_ESTADO", "/var/lib/hulk-idle/estado.json"))
LOCK = Path(os.environ.get("HULK_LOCK", "/run/hulk-keepalive"))


def decidir(gpu, cpu, contador, lock):
    """(gpu%, cpu%, contador atual, lock existe) -> (novo contador, desligar?)."""
    if lock or gpu is None or cpu is None or gpu >= GPU_MAX or cpu >= CPU_MAX:
        return 0, False
    contador += 1
    return contador, contador >= AMOSTRAS


def ler_cpu():
    """(total, ocioso) acumulados de /proc/stat; ocioso = idle + iowait."""
    campos = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    return sum(campos), campos[3] + campos[4]


def ler_gpu():
    """Maior utilização entre as GPUs, ou None se o nvidia-smi falhar."""
    try:
        saida = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
        return max(float(linha) for linha in saida.split())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def main():
    try:
        estado = json.loads(ESTADO.read_text())
    except (OSError, ValueError):
        estado = {}
    total, ocioso = ler_cpu()
    cpu = None
    if "total" in estado and total > estado["total"]:
        cpu = 100.0 * (1 - (ocioso - estado["ocioso"]) / (total - estado["total"]))
    gpu = ler_gpu()
    contador, desligar = decidir(gpu, cpu, estado.get("contador", 0), LOCK.exists())
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps({"total": total, "ocioso": ocioso, "contador": contador}))
    print(f"gpu={gpu} cpu={cpu if cpu is None else round(cpu, 1)} "
          f"ocioso_seguido={contador}/{AMOSTRAS} lock={LOCK.exists()}", flush=True)
    if desligar:
        print("ocioso pelo tempo limite: desligando", flush=True)
        if os.environ.get("HULK_DRY") == "1":
            print("HULK_DRY=1: poweroff não executado", flush=True)
        else:
            subprocess.run(["systemctl", "poweroff"], check=False)


if __name__ == "__main__":
    sys.exit(main())
