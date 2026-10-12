#!/usr/bin/env python3
"""
test_render.py — exercita os renderizadores contra os CONSUMIDORES REAIS.

Não contra aproximações: `ansible-inventory` para o hosts.ini (que já rejeitou
um arquivo aparentemente válido nesta base), yaml.safe_load para o vault, e o
próprio motor de regex do gate para os padrões.

Sem framework: são asserts, rodam com `python3 IaC/bootstrap/test_render.py` e
não acrescentam dependência a um repositório que não tem suíte de testes.

    python3 IaC/bootstrap/test_render.py
"""
import importlib.util, sys, yaml, configparser, io, re, pathlib
spec = importlib.util.spec_from_file_location("bs", pathlib.Path(__file__).parent / "bootstrap.py")
bs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bs)

resp = {
  "dominio": "homelab.example", "admin_user": "netadmin",
  "dominio_admin_user": "netadmin", "alerta_email": "ops@homelab.example",
  "ssh_user": "nuvidio", "outros_hostnames": ["nas-um", "fw-um"],
  "wifi": {"main": "lab", "iot": "lab-iot", "media": "lab-tv", "guest": "lab-vis"},
  "rede_atual": {"gateway": "192.0.2.1", "manager": "192.0.2.10", "worker": "",
                 "hypervisor1": "192.0.2.5", "hypervisor2": "192.0.2.6", "omv": "192.0.2.15",
                 "opnsense": "192.0.2.2", "openwrt": "", "dev": "",
                 "ntp_interno": "192.0.2.1"},
  "nos": {"primario": {"nome": "no-um"}, "secundario": {"nome": "no-dois"}},
  "rede_alvo": {"cidr": 24, "gateway": "198.51.100.1", "manager": "198.51.100.10",
                "worker": "198.51.100.11", "omv": "198.51.100.15", "dev": "198.51.100.20"},
  "opnsense_wan_pci_id": "0000:05:00.0",
  "opnsense_wan2_pci_id": "0000:04:00.0",
  "omv_hba_pci_id": "0000:05:00.0",
  "proxmox": {"backup_storage_primario": "nas-bkp-1",
              "backup_storage_secundario": "nas-bkp-2",
              "backup_share": "backup",
              "backup_vmids": [100, 200], "wan_interface": "enp5s0"},
}
seg, humanas = {}, []
for k, t, h, d in bs.GENERATED:
    seg[k] = bs.gen(t)
    if h: humanas.append((d, k, seg[k]))
for k, *_ in bs.EXTERNAL: seg[k] = "valor-de-teste"
pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTE homelab"

# 1) vault  -> precisa ser YAML válido e conter as chaves esperadas
for env in ("prod", "dev"):
    txt = bs.render_vault(env, resp, seg, pub)
    d = yaml.safe_load(txt)
    faltando = [k for k, *_ in bs.GENERATED if k not in d] + [k for k in bs.PENDING_HASHES if k not in d]
    assert not faltando, f"vault {env} sem: {faltando}"
    assert d["homelab_domain"] == "homelab.example"
    print(f"  OK vault/{env}: YAML valido, {len(d)} chaves")

# prod e dev precisam ter senha de banco DIFERENTE (isolamento)
p = yaml.safe_load(bs.render_vault("prod", resp, seg, pub))
v = yaml.safe_load(bs.render_vault("dev", resp, seg, pub))
assert p["postgres_paperless_password"] != v["postgres_paperless_password"], "prod e dev com a MESMA senha de banco"
print("  OK isolamento prod/dev: senhas de banco distintas")

# 2) hosts.ini -> precisa parsear como INI e ter a var do gateway
ini = bs.render_hosts("prod", resp)
cp = configparser.ConfigParser(allow_no_value=True, delimiters=("=",))
cp.read_string(ini)
assert "all:vars" in cp, "sem [all:vars]"
assert cp["all:vars"]["ntp_server_internal"] == "192.0.2.1"
assert "\n# node2 ansible_host" in ini, "worker vazio deveria sair comentado"
print(f"  OK hosts.ini: INI valido, secoes={len(cp.sections())}, worker ausente comentado")

# 2b) 99-local.yml de group_vars/all -> system_user e o MESMO ssh_user do
#     hosts.ini: um cria a conta nos nos, o outro entra por ela.
loc = yaml.safe_load(bs.render_local_all(resp))
assert loc == {"system_user": resp["ssh_user"]}, f"99-local inesperado: {loc}"
assert f"ansible_user={resp['ssh_user']}" in ini, "hosts.ini sem o ssh_user respondido"
print("  OK 99-local: system_user igual ao ansible_user dos nos")

# 3) tfvars -> TODA variavel sem default do variables.tf, e CIDR nos IPs.
#    A lista nao e escrita a mao: ela e LIDA do proprio variables.tf. Uma lista
#    fixa aqui envelhece em silencio — foi o que aconteceu quando o token unico
#    do Proxmox virou um par por no e o teste continuou aprovando o render
#    antigo, que ja nao emitia as chaves novas.
tf = bs.render_tfvars(resp, seg, pub)
_vars_tf = (pathlib.Path(__file__).parent.parent / "terraform/proxmox/variables.tf").read_text()
# `"default" not in bloco` era a versao ingenua e reproduzia o defeito que a
# lista fixa tinha: casava a palavra na DESCRICAO. Duas obrigatorias diziam
# "Sem default:" e "Nao tem default:" no proprio texto, e sumiam da lista sem
# que nada acusasse. Casa a ATRIBUICAO, ancorada em inicio de linha.
obrig = [m.group(1) for m in re.finditer(r'variable\s+"([^"]+)"\s*\{(.*?)\n\}', _vars_tf, re.S)
         if not re.search(r'^\s*default\s*=', m.group(2), re.M)]
assert len(obrig) >= 9, f"parser do variables.tf achou so {len(obrig)} obrigatorias"
# Os quatro tokens sao escritos por
# IaC/ansible/playbooks/00-proxmox-api-token.yml em tokens.auto.tfvars, que o
# Terraform carrega junto pelo sufixo .auto.tfvars. Nao estao no render do
# bootstrap de proposito — um valor, uma fonte. O teste segue cobrindo TODAS as
# obrigatorias: as que nao saem daqui tem que sair do template de la, senao o
# plan trava pedindo variavel.
_tpl_tokens = (pathlib.Path(__file__).parent.parent
               / "ansible/templates/terraform/tokens.auto.tfvars.j2").read_text()
faltando = [k for k in obrig
            if not re.search(rf'^{k}\s*=', tf, re.M)
            and not re.search(rf'^{k}\s*=', _tpl_tokens, re.M)]
assert not faltando, f"nenhum dos dois arquivos de tfvars define: {faltando}"
_no_playbook = [k for k in obrig if re.search(rf'^{k}\s*=', _tpl_tokens, re.M)]
assert len(_no_playbook) == 4, f"esperava 4 tokens vindos do playbook, veio {_no_playbook}"
for k in _no_playbook:
    assert not re.search(rf'^{k}\s*=', tf, re.M), (
        f"{k} sai dos DOIS arquivos — duas fontes para o mesmo segredo")
assert '"198.51.100.10/24"' in tf, "IP do manager sem CIDR"
assert '"198.51.100.1"' in tf, "gateway nao deveria ter CIDR"
# Sem IP do segundo hipervisor, a URL sai COMENTADA — nunca como "https://:8006/",
# que e string nao-vazia e passaria por qualquer checagem de "preenchido".
resp_sem_hv2 = {**resp, "rede_atual": {**resp["rede_atual"], "hypervisor2": ""}}
tf_sem = bs.render_tfvars(resp_sem_hv2, seg, pub)
assert '"https://:8006/"' not in tf_sem, "URL do no secundario sem host"
assert "# proxmox_api_url_secondary" in tf_sem, "deveria sair comentada quando nao ha no 2"
print(f"  OK tfvars: {len(obrig)} obrigatorias cobertas ({len(_no_playbook)} vem do playbook de token), sem sobreposicao")

# 4) secret-patterns -> formato de 3 campos e casa com os nomes
pat = bs.render_patterns(resp)
linhas = [l for l in pat.splitlines() if l and not l.startswith("#")]
assert len(linhas) == 2, f"esperava 2 padroes, veio {len(linhas)}"
for l in linhas:
    assert l.count("%%") == 2, f"formato errado: {l}"
rx = linhas[0].split("%%")[1]
assert re.search(rx, "servidor no-um responde"), "regex nao casa com o hostname"
assert re.search(linhas[1].split("%%")[1], "app.homelab.example"), "regex nao casa com o dominio"
print("  OK secret-patterns: 2 padroes, formato %%, regex casam")

# 5) credenciais -> so as humanas, e nenhuma senha de banco
cred = bs.render_credenciais(humanas)
assert seg["postgres_paperless_password"] not in cred, "VAZOU senha de banco no arquivo de credenciais"
assert seg["portainer_admin_password"] in cred, "senha do Portainer deveria estar"
print(f"  OK credenciais: {len(humanas)} humanas, nenhum segredo de maquina vazado")
print("\n  TODOS OS RENDERS PASSARAM")

# 6) o validador REAL do hosts.ini: ansible-inventory, nao configparser
import subprocess, tempfile, os, json, shutil
if shutil.which("ansible-inventory"):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "hosts.ini")
        open(p, "w").write(ini)
        r = subprocess.run(["ansible-inventory", "-i", p, "--list"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"ansible-inventory REJEITOU:\n{r.stderr[:400]}"
        inv = json.loads(r.stdout)
        assert "homelab" in inv, "grupo homelab ausente"
        gv = inv["_meta"]["hostvars"]["node1"]
        assert gv.get("ntp_server_internal") == "192.0.2.1", f"ntp nao chegou ao host: {gv}"
        print(f"  OK ansible-inventory: aceito, grupos={sorted(k for k in inv if k!='_meta')}")
        print(f"     ntp_server_internal resolve em node1 = {gv['ntp_server_internal']}")

# 7) sem NTP interno, a linha sai COMENTADA -> so as publicas valem
        resp_sem = {**resp, "rede_atual": {**resp["rede_atual"], "ntp_interno": ""}}
        ini2 = bs.render_hosts("prod", resp_sem)
        assert "\n# ntp_server_internal=" in ini2, "sem NTP interno deveria sair comentado"
        assert "\nntp_server_internal=" not in ini2, "nao deveria definir a variavel"
        p2 = os.path.join(d, "h2.ini"); open(p2, "w").write(ini2)
        r2 = subprocess.run(["ansible-inventory", "-i", p2, "--list"], capture_output=True, text=True)
        assert r2.returncode == 0, f"inventario sem NTP rejeitado: {r2.stderr[:300]}"
        assert "ntp_server_internal" not in json.loads(r2.stdout)["_meta"]["hostvars"]["node1"]
        print("  OK sem NTP interno: variavel ausente, inventario valido")

# 8) as variaveis que 00-proxmox-setup.yml exige tem que CHEGAR ao host, nao
#    so aparecer no texto do arquivo. O playbook aborta sem as duas primeiras,
#    e um gateway ausente sai como string vazia no /etc/network/interfaces.
        for chave, esperado in (("proxmox_gateway_ip", "192.0.2.1"),
                                ("proxmox_backup_share", "backup")):
            assert gv.get(chave) == esperado, f"{chave} nao chegou ao host: {gv.get(chave)!r}"

        # A NIC de WAN e PER-HOST: so o no primario faz passthrough. Em
        # [all:vars] ela excluiria da bridge uma NIC homonima e EM USO no
        # secundario, sem erro nenhum — por isso o teste cobre os dois lados.
        hv = inv["_meta"]["hostvars"]
        assert hv["no-um"].get("proxmox_wan_interface") == "enp5s0", (
            f"WAN nao chegou ao no primario: {hv['no-um'].get('proxmox_wan_interface')!r}")
        assert "proxmox_wan_interface" not in gv, "WAN vazou para [all:vars]"

        # O id do storage de backup e PER-HOST: os dois nos registram o mesmo
        # share do NAS com nomes diferentes. Em [all:vars], o storage.cfg do
        # secundario sairia com o id do primario e o vzdump apontaria para um
        # storage inexistente ali.
        assert hv["no-um"].get("backup_storage") == "nas-bkp-1", hv["no-um"].get("backup_storage")
        assert hv["no-dois"].get("backup_storage") == "nas-bkp-2", hv["no-dois"].get("backup_storage")
        assert "backup_storage" not in gv, "id do storage vazou para [all:vars]"
        # Uma ocorrencia so, e na linha do primario. Duas significaria que ela
        # tambem foi parar no secundario, que nao faz passthrough.
        assert ini.count("proxmox_wan_interface=") == 1, "WAN deveria estar em UM host so"
        assert gv.get("backup_vmids") == [100, 200], (
            f"backup_vmids precisa chegar como LISTA, veio {gv.get('backup_vmids')!r}")
        print("  OK vars do Proxmox: gateway/storage/share globais, WAN e storage per-host, vmids como lista")

# 9) sem VMIDs declarados a linha sai COMENTADA — 06-backup.yml aborta, que e
#    melhor do que agendar vzdump contra o convidado errado.
        resp_sv = {**resp, "proxmox": {**resp["proxmox"], "backup_vmids": []}}
        ini3 = bs.render_hosts("prod", resp_sv)
        assert "\n# backup_vmids=" in ini3, "sem VMIDs deveria sair comentado"
        assert "\nbackup_vmids=" not in ini3, "nao deveria definir a variavel"
        print("  OK sem backup_vmids: linha comentada, nada definido")
else:
    print("  – ansible-inventory ausente, pulado")
