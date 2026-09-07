#!/usr/bin/env python3
"""Gate da tag `repositorios` do 00-proxmox-setup.yml.

⚠️ O PERIGO QUE ESTE GATE EXISTE PARA IMPEDIR

As tasks de repositório do Proxmox formam uma UNIDADE, e a ordem entre elas
não é livre:

  1. a primeira ESVAZIA /etc/apt/sources.list;
  2. as três últimas DECLARAM os repositórios em deb822.

Rodar (1) sem (2) deixa o hipervisor sem repositório Debian nenhum — e o
sintoma só aparece no próximo `apt install`, longe daqui. Como a seleção é por
`--tags`, marcar só parte do bloco produz exatamente esse estado, em silêncio:
o playbook reporta sucesso e o host fica quebrado.

Não basta olhar a task de esvaziar. O gate afirma que TODA task marcada com a
tag continua marcada, que a de esvaziar vem ANTES das que declaram, e que o
`upgrade: dist` continua FORA da tag — porque trocar declaração de repositório
e atualizar o sistema inteiro têm riscos diferentes e não podem voltar a
viajar juntos.

O gate EFETIVO é o playbook; isto prova que a forma dele não regrediu.
Controle negativo: tire a tag de uma task que declara repositório, ou mova a
de esvaziar para depois, e este teste fica vermelho.
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML.")

TAG = "repositorios"
PLAYBOOK = Path(__file__).resolve().parent.parent / "playbooks" / "00-proxmox-setup.yml"

falhas = []


def ok(msg):
    print(f"  \033[32mOK\033[0m {msg}")


def erro(msg):
    falhas.append(msg)
    print(f"  \033[31mX\033[0m  {msg}")


def tags_de(task):
    t = task.get("tags", [])
    return [t] if isinstance(t, str) else list(t)


def main() -> int:
    if not PLAYBOOK.is_file():
        return erro_fatal(f"playbook não encontrado: {PLAYBOOK}")

    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    play = next((p for p in plays if p.get("hosts") == "proxmox"), None)
    if play is None:
        return erro_fatal("nenhum play com hosts: proxmox")

    tasks = play.get("tasks") or []
    print(f"\nGate da tag `{TAG}` — {len(tasks)} tasks no play\n")

    # --- CAMADA 1: identificar as tasks pelo QUE ELAS FAZEM, não pelo nome.
    # Casar por nome faria o gate passar a mentir no dia em que alguém
    # renomear a task sem mexer no comportamento.
    def esvazia_sources_list(t):
        c = t.get("ansible.builtin.copy") or t.get("copy") or {}
        return c.get("dest") == "/etc/apt/sources.list"

    def declara_repositorio(t):
        return ("ansible.builtin.deb822_repository" in t
                or "deb822_repository" in t)

    def faz_dist_upgrade(t):
        a = t.get("ansible.builtin.apt") or t.get("apt") or {}
        return a.get("upgrade") == "dist"

    i_esvazia = [i for i, t in enumerate(tasks) if esvazia_sources_list(t)]
    i_declara = [i for i, t in enumerate(tasks) if declara_repositorio(t)]
    i_upgrade = [i for i, t in enumerate(tasks) if faz_dist_upgrade(t)]

    if len(i_esvazia) != 1:
        erro(f"esperava 1 task esvaziando /etc/apt/sources.list, achei {len(i_esvazia)}")
        return relatorio()
    ok("existe exatamente uma task que esvazia /etc/apt/sources.list")

    if len(i_declara) < 3:
        erro(f"esperava 3+ tasks declarando repositório deb822, achei {len(i_declara)}")
        return relatorio()
    ok(f"{len(i_declara)} tasks declaram repositório em deb822")

    # --- CAMADA 2: a unidade. Esvaziar sem declarar deixa o host sem apt.
    if TAG not in tags_de(tasks[i_esvazia[0]]):
        erro(f"a task que esvazia o sources.list NÃO tem a tag `{TAG}`")
    else:
        ok(f"a task que esvazia o sources.list tem a tag `{TAG}`")

    sem_tag = [tasks[i].get("name", f"#{i}") for i in i_declara
               if TAG not in tags_de(tasks[i])]
    if sem_tag:
        erro(f"task que declara repositório SEM a tag `{TAG}` — `--tags {TAG}` "
             f"esvaziaria o sources.list e não declararia nada: {sem_tag}")
    else:
        ok(f"toda task que declara repositório carrega a tag `{TAG}`")

    # --- CAMADA 3: a ordem. Declarar antes de esvaziar perde a declaração.
    if i_esvazia[0] > min(i_declara):
        erro("a task que esvazia o sources.list vem DEPOIS de uma que declara "
             "repositório — a ordem do bloco inverteu")
    else:
        ok("a task que esvazia vem antes de todas as que declaram")

    # --- CAMADA 4: o upgrade fica fora da tag.
    if not i_upgrade:
        erro("nenhuma task com `upgrade: dist` — o playbook parou de atualizar")
    for i in i_upgrade:
        nome = tasks[i].get("name", f"#{i}")
        if TAG in tags_de(tasks[i]):
            erro(f"`upgrade: dist` voltou para a tag `{TAG}` em {nome!r}: "
                 "consertar repositório passaria a atualizar o sistema inteiro")
        else:
            ok(f"`upgrade: dist` está fora da tag `{TAG}` ({nome!r})")

    # --- CAMADA 5: refresh de cache sob a tag, senão a troca não é validada.
    # ⚠️ `name` PRECISA ficar de fora, e o controle negativo é que mostrou por
    # quê: a task que instala `python3-debian` também traz `update_cache: true`
    # e também carrega a tag, então um teste frouxo passava por causa DELA — e
    # ela roda ANTES das declarações, não validando troca nenhuma. Um gate
    # satisfeito pela task errada é verde que não prova nada.
    def so_atualiza_cache(t):
        a = t.get("ansible.builtin.apt") or t.get("apt") or {}
        return (a.get("update_cache") is True
                and "upgrade" not in a
                and "name" not in a)
    i_cache = [i for i, t in enumerate(tasks)
               if so_atualiza_cache(t) and TAG in tags_de(t)]
    if not i_cache:
        erro(f"a tag `{TAG}` não tem uma task dedicada a atualizar o cache do "
             "APT — a troca de repositório não seria validada na mesma execução")
    elif max(i_declara) > max(i_cache):
        erro("o refresh de cache vem ANTES da última declaração de repositório: "
             "valida o estado velho")
    else:
        ok(f"a tag `{TAG}` atualiza o cache do APT depois de trocar tudo")

    return relatorio()


def relatorio() -> int:
    print()
    if falhas:
        print(f"\033[31m  {len(falhas)} FALHA(S) NO GATE DE REPOSITÓRIOS\033[0m\n")
        return 1
    print("\033[32m  TODOS OS GATES DE REPOSITÓRIO PASSARAM\033[0m\n")
    return 0


def erro_fatal(msg) -> int:
    erro(msg)
    return relatorio()


if __name__ == "__main__":
    sys.exit(main())
