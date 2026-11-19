#!/usr/bin/env python3
"""hulk-gpu-metrics: métricas da GPU no formato textfile do node_exporter.

Só leitura, via NVML (`libnvidia-ml.so.1`, ctypes). Chamado pelo
hulk-gpu-metrics.timer (15 s). A escrita é atômica (tmp + rename): o coletor
textfile nunca pode ler arquivo pela metade. Se o NVML falhar, REMOVE o arquivo
(série ausente, não valor velho parado) e sai com 1.
"""
import ctypes
import os
import sys
from pathlib import Path

DIR = Path(os.environ.get("HULK_TEXTFILE_DIR", "/var/lib/prometheus/node-exporter"))
ARQUIVO = "hulk_gpu.prom"


class _Util(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Mem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


def formatar(gpu, util_pct, mem_usada, mem_total, temp_c, fan_pct):
    """Texto no formato de exposição do Prometheus, uma GPU."""
    rot = f'{{gpu="{gpu}"}}'
    linhas = [
        ("gpu_utilization_ratio", "Utilizacao do nucleo da GPU (0 a 1).", util_pct / 100),
        ("gpu_memory_used_bytes", "VRAM ocupada (total - livre): inclui a reserva do driver, que o nvidia-smi nao soma em memory.used.", mem_usada),
        ("gpu_memory_total_bytes", "VRAM total.", mem_total),
        ("gpu_temperature_celsius", "Temperatura do nucleo da GPU.", temp_c),
        ("gpu_fan_speed_ratio", "Velocidade da ventoinha (0 a 1).", fan_pct / 100),
    ]
    saida = []
    for nome, ajuda, valor in linhas:
        saida += [f"# HELP {nome} {ajuda}", f"# TYPE {nome} gauge", f"{nome}{rot} {valor!r}"]
    return "\n".join(saida) + "\n"


def ler():
    lib = ctypes.CDLL("libnvidia-ml.so.1")
    lib.nvmlErrorString.restype = ctypes.c_char_p

    def ok(nome, *args):
        rc = getattr(lib, nome)(*args)
        if rc != 0:
            raise OSError(f"{nome}: rc={rc} ({lib.nvmlErrorString(rc).decode()})")

    ok("nvmlInit_v2")
    try:
        h = ctypes.c_void_p()
        ok("nvmlDeviceGetHandleByIndex_v2", 0, ctypes.byref(h))
        util, mem = _Util(), _Mem()
        temp, fan = ctypes.c_uint(), ctypes.c_uint()
        ok("nvmlDeviceGetUtilizationRates", h, ctypes.byref(util))
        ok("nvmlDeviceGetMemoryInfo", h, ctypes.byref(mem))
        ok("nvmlDeviceGetTemperature", h, 0, ctypes.byref(temp))
        ok("nvmlDeviceGetFanSpeed_v2", h, 0, ctypes.byref(fan))
        return formatar(0, util.gpu, mem.used, mem.total, temp.value, fan.value)
    finally:
        lib.nvmlShutdown()


def main():
    destino = DIR / ARQUIVO
    try:
        texto = ler()
    except OSError as e:
        destino.unlink(missing_ok=True)
        print(f"NVML falhou, métricas removidas: {e}", file=sys.stderr)
        return 1
    tmp = DIR / f".{ARQUIVO}.tmp"
    tmp.write_text(texto)
    tmp.chmod(0o644)
    os.replace(tmp, destino)
    return 0


if __name__ == "__main__":
    sys.exit(main())
