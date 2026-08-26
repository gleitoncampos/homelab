#!/usr/bin/env python3
"""collect-omv-fs.py — lê os filesystems de dados do NAS e propõe o fragmento
de inventário que o playbook do OMV consome.

Existe por causa de uma consequência da decisão de RECRIAR a VM do NAS em vez
de importá-la. A recriação preserva os discos de dados — eles são físicos e
entram pelo passthrough da controladora — e destrói o disco de sistema, junto
com o `/etc/openmediavault/config.xml`. Numa OMV recém-instalada os discos
aparecem no `lsblk` com o filesystem íntegro e NENHUM deles montado; a
associação entre UUID e ponto de montagem morava só naquele arquivo.

O playbook precisa então de uma lista de UUIDs para remontar. Detectar todo
ext4 não montado seria cômodo e perigoso: adotaria qualquer disco que alguém
plugue na máquina, inclusive um que estivesse ali só para copiar dados. A lista
é portanto declarada — e este script existe para que ninguém precise digitá-la.

⚠️ ELE NÃO ESCREVE NO INVENTÁRIO, e a recusa é o ponto. A saída vai para
`.local/`, para ser lida e instalada por decisão de quem opera. Produção neste
repositório é fonte de garimpo, nunca autoridade: um coletor que escrevesse
direto no inventário faria a máquina de hoje decidir sozinha qual é o alvo.

⚠️ UUID muda quando o filesystem é recriado. O NÚMERO DE SÉRIE do disco não —
por isso ele acompanha cada entrada como comentário. Reformatou um disco?
Rode de novo e compare: o serial diz qual linha se moveu, e a troca deixa de
ser adivinhação.

Uso:
  ./collect-omv-fs.py                 # host do grupo `omv` no inventário de prod
  ./collect-omv-fs.py -i <hosts.ini>  # outro inventário
  ./collect-omv-fs.py -l <host>       # um host nomeado do inventário
"""
import argparse
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_INV = REPO / "IaC/ansible/inventories/prod/hosts.ini"
OUT_DIR = REPO / ".local/snapshot"

LSBLK = ("lsblk -J -b -o "
         "NAME,SERIAL,MODEL,SIZE,ROTA,FSTYPE,UUID,MOUNTPOINT,PKNAME")


def die(msg: str) -> None:
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(1)


def inventory_target(inv: pathlib.Path, limit: str | None) -> tuple[str, str]:
    """Devolve (nome, endereço) do host do grupo `omv`."""
    try:
        raw = subprocess.run(
            ["ansible-inventory", "-i", str(inv), "--list"],
            capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        die("ansible-inventory não encontrado no PATH")
    except subprocess.CalledProcessError as exc:
        die(f"ansible-inventory falhou: {exc.stderr.strip()}")

    data = json.loads(raw)
    hosts = data.get("omv", {}).get("hosts", [])
    if limit:
        hosts = [h for h in hosts if h == limit]
    if not hosts:
        die(f"nenhum host no grupo `omv` de {inv}"
            + (f" casando com --limit {limit}" if limit else ""))
    if len(hosts) > 1:
        die(f"mais de um host em `omv` ({hosts}); use -l para escolher")

    name = hosts[0]
    meta = data.get("_meta", {}).get("hostvars", {}).get(name, {})
    addr = meta.get("ansible_host", name)
    user = meta.get("ansible_user", "root")
    return name, f"{user}@{addr}"


def gib(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GiB"


def collect(target: str) -> dict:
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, LSBLK],
        capture_output=True, text=True)
    if out.returncode != 0:
        die(f"ssh para {target} falhou: {out.stderr.strip()}")
    return json.loads(out.stdout)


def data_partitions(tree: dict) -> list[dict]:
    """Partições de DADOS: têm filesystem e UUID, e não vivem no disco de
    sistema.

    ⚠️ O recorte é pelo DISCO que hospeda `/` ou `/boot/efi`, e não pela
    partição. Excluir só a partição raiz deixaria passar as irmãs dela no mesmo
    disco — na VM atual, a partição EFI e a de BIOS boot entrariam na lista.
    """
    system_disks = set()
    for disk in tree.get("blockdevices", []):
        for part in [disk] + disk.get("children", []):
            if part.get("mountpoint") in ("/", "/boot/efi"):
                system_disks.add(disk["name"])

    found = []
    for disk in tree.get("blockdevices", []):
        if disk["name"] in system_disks:
            continue
        for part in disk.get("children", []):
            if not part.get("fstype") or not part.get("uuid"):
                continue
            # Mídia removível/óptica não é disco de dados, e o iso9660 do drive
            # de cloud-init aparece aqui se não for barrado.
            if part["fstype"] in ("iso9660", "udf", "squashfs"):
                continue
            found.append({
                "uuid": part["uuid"],
                "fstype": part["fstype"],
                "dev": part["name"],
                "model": (disk.get("model") or "?").strip(),
                "serial": (disk.get("serial") or "?").strip(),
                "size": gib(int(part.get("size") or 0)),
                "rotational": bool(disk.get("rota")),
                "mounted": part.get("mountpoint") or None,
            })
    return sorted(found, key=lambda p: p["dev"])


def render(host: str, parts: list[dict]) -> str:
    lines = [
        "---",
        "# GERADO por prod-snapshot/collect-omv-fs.py — LEIA ANTES DE INSTALAR.",
        f"# Origem: host `{host}` do grupo `omv`.",
        "#",
        "# Filesystems de dados que o playbook do OMV remonta depois de uma",
        "# reinstalação. A lista é declarada, e não detectada em tempo de",
        "# execução, para que um disco plugado por outro motivo não seja",
        "# adotado sozinho.",
        "#",
        "# ⚠️ O UUID muda se o filesystem for recriado; o serial do disco, não.",
        "# Depois de reformatar, rode o coletor de novo e compare pelo serial.",
        "omv_data_filesystems:",
    ]
    for p in parts:
        kind = "HDD" if p["rotational"] else "SSD"
        estado = f"montado em {p['mounted']}" if p["mounted"] else "NÃO montado"
        lines += [
            f"  # {p['dev']} · {p['model']} · série {p['serial']}",
            f"  #   {p['size']} · {kind} · {estado} na coleta",
            f'  - uuid: "{p["uuid"]}"',
            f"    fstype: {p['fstype']}",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--inventory", type=pathlib.Path, default=DEFAULT_INV)
    ap.add_argument("-l", "--limit", default=None)
    args = ap.parse_args()

    if not args.inventory.exists():
        die(f"inventário não encontrado: {args.inventory}")

    host, target = inventory_target(args.inventory, args.limit)
    parts = data_partitions(collect(target))
    if not parts:
        die(f"nenhuma partição de dados encontrada em {host}. "
            "O passthrough da controladora está ativo nesta VM?")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"omv-filesystems-{host}.yml"
    out.write_text(render(host, parts), encoding="utf-8")
    out.chmod(0o600)

    print(f"{len(parts)} filesystem(s) de dados em {host}:")
    for p in parts:
        estado = p["mounted"] or "não montado"
        print(f"  {p['dev']:<6} {p['size']:>10}  {p['model'][:24]:<24} {estado}")
    print()
    print(f"Proposta escrita em: {out.relative_to(REPO)}")
    print()
    print("Leia o arquivo. Se estiver certo, instale na camada local do")
    print("inventário — que é gitignorada e onde o dado real pode viver:")
    print()
    print(f"  cat {out.relative_to(REPO)} \\")
    print("    >> IaC/ansible/inventories/prod/group_vars/omv/99-local.yml")
    print()
    print("⚠️  Confira se o 99-local.yml já tem um `omv_data_filesystems:`.")
    print("    Duas chaves iguais no mesmo arquivo: a segunda vence, em")
    print("    silêncio. Substitua o bloco, não empilhe.")


if __name__ == "__main__":
    main()
