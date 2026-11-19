#!/usr/bin/env python3
"""hulk-fan-control: curva da ventoinha da GPU no hulk (Ubuntu Server, sem X).

Sem X não há `nvidia-settings`/Coolbits: o controle vai direto pelo NVML
(`libnvidia-ml.so.1`, via ctypes, sem instalar pacote), com o driver aberto.

Curva (a 40 °C a ventoinha parte do mínimo da placa e a 60 °C chega a 100 %):
  <= 40 °C   automático do driver (zero-RPM de fábrica)
  40 -> 60   linear, do mínimo do NVML (30 % na RTX 5060 Ti) até 100 %
  >= 60 °C   100 %
Histerese: depois de assumir o manual, só devolve o automático em <= 38 °C
(entre 38 e 40 segura o mínimo), para não ligar e desligar a 40 °C.

Fail-safe: temperatura ilegível = 100 %. Ao parar (SIGTERM, erro, systemd), o
controle volta ao automático do driver (`--restaurar` roda também no
ExecStopPost do hulk-fan.service).
"""
import ctypes
import math
import os
import signal
import sys
import time

LIGA_C = float(os.environ.get("HULK_FAN_LIGA_C", "40"))
CHEIA_C = float(os.environ.get("HULK_FAN_CHEIA_C", "60"))
HISTERESE_C = float(os.environ.get("HULK_FAN_HISTERESE_C", "2"))
PERIODO_S = float(os.environ.get("HULK_FAN_PERIODO_S", "2"))

NVML_TEMPERATURE_GPU = 0
NVML_FAN_POLICY_MANUAL = 1


def alvo_pct(temp, minimo, maximo=100):
    """Rampa linear: LIGA_C -> minimo, CHEIA_C -> maximo (arredonda para cima)."""
    if temp >= CHEIA_C:
        return maximo
    if temp <= LIGA_C:
        return minimo
    return min(maximo, math.ceil(minimo + (maximo - minimo) * (temp - LIGA_C) / (CHEIA_C - LIGA_C)))


def decidir(temp, manual, minimo, maximo=100):
    """(temp, manual já assumido) -> % alvo, ou None = devolver ao automático."""
    if temp is None:
        return maximo
    if temp > LIGA_C:
        return alvo_pct(temp, minimo, maximo)
    if manual and temp > LIGA_C - HISTERESE_C:
        return minimo
    return None


class Nvml:
    def __init__(self):
        self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        self.lib.nvmlErrorString.restype = ctypes.c_char_p
        self._ok("nvmlInit_v2")
        self.h = ctypes.c_void_p()
        self._ok("nvmlDeviceGetHandleByIndex_v2", 0, ctypes.byref(self.h))
        n = ctypes.c_uint()
        self._ok("nvmlDeviceGetNumFans", self.h, ctypes.byref(n))
        self.fans = n.value
        lo, hi = ctypes.c_uint(), ctypes.c_uint()
        self._ok("nvmlDeviceGetMinMaxFanSpeed", self.h, ctypes.byref(lo), ctypes.byref(hi))
        self.minimo, self.maximo = lo.value, hi.value
        if self.fans < 1:
            raise OSError("NVML não enxerga ventoinha controlável")

    def _ok(self, nome, *args):
        rc = getattr(self.lib, nome)(*args)
        if rc != 0:
            raise OSError(f"{nome}: rc={rc} ({self.lib.nvmlErrorString(rc).decode()})")

    def temperatura(self):
        t = ctypes.c_uint()
        self._ok("nvmlDeviceGetTemperature", self.h, NVML_TEMPERATURE_GPU, ctypes.byref(t))
        return float(t.value)

    def definir(self, pct):
        for i in range(self.fans):
            self._ok("nvmlDeviceSetFanControlPolicy", self.h, i, NVML_FAN_POLICY_MANUAL)
            self._ok("nvmlDeviceSetFanSpeed_v2", self.h, i, pct)

    def automatico(self):
        for i in range(self.fans):
            self._ok("nvmlDeviceSetDefaultFanSpeed_v2", self.h, i)


def main(argv):
    nvml = Nvml()
    if "--restaurar" in argv:
        nvml.automatico()
        print("ventoinha devolvida ao automático do driver", flush=True)
        return 0

    parar = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: parar.append(1))
    print(f"curva: {LIGA_C:.0f} C = {nvml.minimo}% -> {CHEIA_C:.0f} C = {nvml.maximo}% "
          f"(histerese {HISTERESE_C:.0f} C, ventoinhas={nvml.fans})", flush=True)

    manual, ultimo = False, None
    try:
        while not parar:
            try:
                temp = nvml.temperatura()
            except OSError as e:
                print(f"temperatura ilegível ({e}): 100% por segurança", flush=True)
                temp = None
            pct = decidir(temp, manual, nvml.minimo, nvml.maximo)
            if pct is None:
                if manual:
                    nvml.automatico()
                    manual, ultimo = False, None
                    print(f"temp={temp:.0f} C: automático do driver", flush=True)
            elif pct != ultimo or not manual:
                nvml.definir(pct)
                manual, ultimo = True, pct
                print(f"temp={'?' if temp is None else format(temp, '.0f')} C: ventoinha em {pct}%", flush=True)
            time.sleep(PERIODO_S)
    finally:
        try:
            nvml.automatico()
        except OSError as e:
            print(f"falha ao devolver o automático: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
