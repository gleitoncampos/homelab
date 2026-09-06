#!/usr/bin/env python3
"""Gate do CPU pinning da VM do firewall (make gates-cpu-pinning).

⚠️ O defeito que este gate existe para impedir: `00-proxmox-setup.yml` escrevia
`affinity: 0` na configuração da VM do firewall SEM conferir quantos vCPUs ela
tem. `affinity: 0` prende a VM ao núcleo 0 — um núcleo físico. Numa VM com
quatro vCPUs isso não é pinning, é contenção: os quatro disputam o mesmo
núcleo, e o sintoma é lentidão sem nenhuma mensagem que aponte para a causa.

O gate tem duas metades, e elas não são a mesma checagem em duas formas:

  1. AFIRMA A FORMA DO ALVO REAL — que as quatro tasks existem, com os nomes
     e, sobretudo, NA ORDEM certa. Asserção depois da escrita seria inútil.
  2. EXECUTA as expressões extraídas do playbook contra configurações
     sintéticas. Sem isso o gate provaria só que alguém escreveu um `assert`,
     não que ele reprova o caso ruim.

A metade (2) roda o Ansible de verdade, com as tasks copiadas VERBATIM do
playbook — nunca reescritas aqui. Um teste que reimplementa a lógica passa
mesmo com o alvo apagado.
"""
import base64
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[3]
PLAYBOOK = RAIZ / "IaC/ansible/playbooks/00-proxmox-setup.yml"

LER = "Ler a configuração da VM do firewall"
DERIVAR = "Derivar quantos vCPUs a VM do firewall tem hoje"
RECUSAR = "Recusar o pinning quando a VM tem mais de um vCPU"
ESCREVER = "Configurar CPU Pinning na VM do OPNsense (1 núcleo dedicado - Core 0)"

falhas = []


def checar(ok, descricao):
    print(f"  {'OK' if ok else 'FALHOU'} {descricao}")
    if not ok:
        falhas.append(descricao)


def achar_tasks():
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    achadas = {}
    ordem = []
    for play in plays:
        for chave in ("pre_tasks", "tasks", "post_tasks"):
            for tarefa in play.get(chave) or []:
                nome = tarefa.get("name")
                if nome in (LER, DERIVAR, RECUSAR, ESCREVER):
                    achadas[nome] = tarefa
                    ordem.append(nome)
    return achadas, ordem


print("== CAMADA 1: as quatro tasks existem, na ordem certa ==")
tasks, ordem = achar_tasks()
for nome in (LER, DERIVAR, RECUSAR, ESCREVER):
    checar(nome in tasks, f'task presente: "{nome[:48]}…"')

if len(ordem) == 4:
    checar(ordem == [LER, DERIVAR, RECUSAR, ESCREVER],
           "ordem: ler → derivar → recusar → ESCREVER (asserção antes da escrita)")
    if ordem != [LER, DERIVAR, RECUSAR, ESCREVER]:
        print(f"     ordem encontrada: {ordem}")
else:
    checar(False, "ordem: as quatro tasks precisam existir para a ordem ser avaliada")

print("\n== CAMADA 2: a asserção compara o total de vCPUs com 1 ==")
if RECUSAR in tasks:
    that = tasks[RECUSAR].get("ansible.builtin.assert", {}).get("that", [])
    texto = " ".join(str(c) for c in that)
    checar("opnsense_vcpus" in texto, "a asserção usa `opnsense_vcpus`")
    checar("== 1" in texto.replace(" ==1", " == 1"), "a asserção exige exatamente 1")
    checar(bool(tasks[RECUSAR].get("ansible.builtin.assert", {}).get("fail_msg")),
           "a asserção tem fail_msg explicando o motivo")
else:
    print("  -- não avaliada: a task de recusa não existe (ver camada 1)")

print("\n== CAMADA 3: as expressões do playbook reprovam o caso ruim ==")
# ⚠️ `sockets` e `cores` são OMITIDOS do arquivo do Proxmox quando valem 1 —
# por isso as fixtures cobrem ausência, não só valores explícitos.
FIXTURES = [
    ("cores:1", "name: fw\ncores: 1\nmemory: 4096\n", True),
    ("cores e sockets ausentes", "name: fw\nmemory: 4096\n", True),
    ("cores:4", "name: fw\ncores: 4\nmemory: 4096\n", False),
    ("cores:1 sockets:2", "name: fw\ncores: 1\nsockets: 2\n", False),
    ("cores:2 sockets:2", "name: fw\ncores: 2\nsockets: 2\n", False),
    ("cores no meio de outra linha", "name: fw\nsmbios1: cores: 9\ncores: 1\n", True),
]

if DERIVAR in tasks and RECUSAR in tasks:
    for rotulo, conteudo, deve_passar in FIXTURES:
        play = [{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "opnsense_vmid": 100,
                "opnsense_vm_conf": {"stat": {"exists": True}},
                "opnsense_vm_raw": {
                    "content": base64.b64encode(conteudo.encode()).decode()
                },
            },
            # VERBATIM do playbook: se as expressões mudarem lá, mudam aqui.
            "tasks": [tasks[DERIVAR], tasks[RECUSAR]],
        }]
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
            yaml.safe_dump(play, fh, allow_unicode=True, sort_keys=False)
            caminho = fh.name
        proc = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "-c", "local", caminho],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        Path(caminho).unlink(missing_ok=True)
        passou = proc.returncode == 0
        checar(passou == deve_passar,
               f"{rotulo}: {'passa' if deve_passar else 'REPROVA'} "
               f"(rc={proc.returncode})")
else:
    print("  -- não avaliada: as tasks não existem (ver camada 1)")

print()
if falhas:
    print(f"  {len(falhas)} FALHA(S)")
    sys.exit(1)
print("  CPU PINNING OK (asserção presente, antes da escrita, e reprova o caso ruim)")
