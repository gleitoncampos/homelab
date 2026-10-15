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
    faltando = [k for k, *_ in bs.GENERATED if k not in d] + [k for k in bs.OPNSENSE_SEGREDOS if k not in d]
    assert not faltando, f"vault {env} sem: {faltando}"
    assert d["homelab_domain"] == "homelab.example"
    print(f"  OK vault/{env}: YAML valido, {len(d)} chaves")

# 1b) todo segredo que algum playbook exige no gate do vault (`required_secrets`)
#     tem de sair do bootstrap. `portainer_agent_secret` não saía e o
#     02-bootstrap-cluster abortava no gate (13/10/2026).
#     Sem pendência: pve_exporter_* saiu do bootstrap em 13/10/2026 (usuário
#     prometheus@pve só leitura, senha gerada, igual nos dois hosts).
PENDENTE = set()
NAO_VAULT = {"system_user"}  # vem do 99-local.yml, testado em 2b
def _exigidos(no, acc):
    if isinstance(no, dict):
        if isinstance(no.get("required_secrets"), list): acc.update(no["required_secrets"])
        for x in no.values(): _exigidos(x, acc)
    elif isinstance(no, list):
        for x in no: _exigidos(x, acc)
_raiz_pb = pathlib.Path(__file__).resolve().parents[1] / "ansible" / "playbooks"
for env in ("prod", "dev"):
    d = yaml.safe_load(bs.render_vault(env, resp, seg, pub))
    for pb in sorted(_raiz_pb.rglob("*.yml")):
        acc = set(); _exigidos(yaml.safe_load(pb.read_text(encoding="utf-8")), acc)
        sem = sorted(acc - set(d) - NAO_VAULT - PENDENTE)
        assert not sem, f"vault {env} não gera {sem}, exigido por {pb.relative_to(_raiz_pb)}"
print("  OK vault: gera todo segredo que os playbooks exigem")

# Exporter do Proxmox: usuário DEDICADO só leitura, nunca root@pam; senha (e não
# token) porque os dois hipervisores não são cluster — token tem valor diferente por
# host, e o exporter usa UMA credencial para os dois (13/10/2026).
d = yaml.safe_load(bs.render_vault("prod", resp, seg, pub))
assert d.get("pve_exporter_user") == "prometheus@pve", f"pve_exporter_user: {d.get('pve_exporter_user')!r}"
assert len(d.get("pve_exporter_password") or "") >= 16, "pve_exporter_password não gerada"
assert not any(k.startswith("pve_exporter_token") for k, *_ in bs.EXTERNAL), "bootstrap ainda pergunta token do exporter"
print("  OK exporter do Proxmox: prometheus@pve com senha gerada, sem token")

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
assert cp["all:vars"]["ntp_interno"] == "true"
assert "\n# node2 ansible_host" in ini, "worker vazio deveria sair comentado"
print(f"  OK hosts.ini: INI valido, secoes={len(cp.sections())}, worker ausente comentado")

# 2b) 99-local.yml de group_vars/all -> system_user e o MESMO ssh_user do
#     hosts.ini: um cria a conta nos nos, o outro entra por ela.
loc = yaml.safe_load(bs.render_local_all(resp))
# mgmt_allowed_cidrs: quem pode chegar às portas administrativas do Swarm
# (9000 do Portainer, 1514 do syslog). Uma faixa só não servia: o syslog vem
# do firewall e do NAS (VLAN 20), do Proxmox (LAN), e o operador da LAN
# (13/10/2026). LAN = gateway atual /24; homelab = gateway-alvo /cidr;
# gerência = <prefixo>.10.0/24, pela convenção <prefixo>.<vlan>.x.
assert loc == {"system_user": resp["ssh_user"],
               "mgmt_allowed_cidrs": ["192.0.2.0/24", "198.51.10.0/24", "198.51.100.0/24"]}, \
    f"99-local inesperado: {loc}"
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

# 5a) o que o render do OPNsense consome e SO o vault fornece tem de sair do
#     bootstrap: chave da fixture, menos o que os group_vars versionados
#     definem, menos o que o template cobre com `| default`. A
#     opnsense_tailscale_preauth_key faltava: o apply abortava em "is undefined"
#     escondido atras de no_log, e o `make render-opnsense` passava porque a
#     fixture a define.
_ans = pathlib.Path(__file__).parents[1] / "ansible"
_fix = set(yaml.safe_load((_ans / "tests/fixtures/opnsense.yml").read_text()))
_vers = set()
for _f in list((_ans / "inventories/_common").glob("*.yml")) + [
        _f for _f in (_ans / "inventories/prod/group_vars").rglob("*.yml")
        if _f.name not in ("vault.yml",) and not _f.name.startswith("99-")]:
    _vers |= set(yaml.safe_load(_f.read_text()) or {})
_comdefault = set(re.findall(r"(\w+)\s*\|\s*default\(",
                  "".join(t.read_text() for t in (_ans / "templates/network/opnsense").glob("*.j2"))))
_vk = set(yaml.safe_load(bs.render_vault("prod", resp, seg, pub)))
_falta = _fix - _vers - _comdefault - _vk
assert not _falta, f"o render do OPNsense consome e o vault do bootstrap nao gera: {sorted(_falta)}"
print(f"  OK vault do bootstrap cobre o que so ele fornece ao render do OPNsense ({len(_fix - _vers - _comdefault)} chaves)")

# 5a) senha de PESSOA: recusa igual ao usuario e senha obvia, exige o minimo
#     (12 nas de administrador). Proposta de 12/10/2026, aceita em 14/10: a
#     senha do admin do OPNsense tinha saido igual ao usuario.
import builtins as _bi
_digitadas = iter(["operador-casa", "Operador-Casa", "curta12", "SenhaBoa-com-12", "SenhaBoa-com-12"])
_ask_real = bs.ask_secret
bs.ask_secret = lambda label, obrigatorio=True: next(_digitadas)
_print_real, _ditos = _bi.print, []
_bi.print = lambda *a, **k: _ditos.append(" ".join(map(str, a)))
try:
    _v = bs.perguntar_senha("teste", "sugestao", minimo=12, usuarios=("operador-casa",))
finally:
    _bi.print = _print_real
assert _v == "SenhaBoa-com-12", _v
assert sum("nome de usu" in d for d in _ditos) == 2, _ditos   # usuario, mesma em outra caixa
assert bs.senha_recusada("Admin") and bs.senha_recusada("OPNsense") and not bs.senha_recusada("Admin-x")
assert sum("no mínimo 12" in d for d in _ditos) == 1, _ditos   # curta12
# o admin do OPNsense e perguntado com minimo 12 e com o nome do usuario
_pedidos = []
bs.perguntar_senha = lambda d, sug, **k: (_pedidos.append(k), sug)[1]
bs.segredos_opnsense(None, pub, "homelab.example",
                     {"root_hash": "$2b$x", "pppoe": ""}, admin_user="netadmin")
assert _pedidos and _pedidos[0].get("minimo") == bs.MINIMO_ADMIN == 12, _pedidos
assert "netadmin" in _pedidos[0].get("usuarios", ()), _pedidos
# root do OPNsense: digitada, passa pela mesma regra
_digitadas = iter(["netadmin", "curta", "RaizBoa-com-12c", "RaizBoa-com-12c"])
bs.ask_secret = lambda label, obrigatorio=True: next(_digitadas)
_bi.print = lambda *a, **k: None
try:
    _o, _ = bs.segredos_opnsense(None, pub, "homelab.example", {"pppoe": ""}, admin_user="netadmin")
finally:
    _bi.print = _print_real
assert _o["opnsense_root_password_hash"].startswith(("$2b$", "$2y$")), "root nao aceitou senha valida"
assert next(_digitadas, "fim") == "fim", "root aceitou senha curta ou igual ao usuario"
for _k in ("omv_admin_password", "proxmox_backup_share_password"):
    assert bs.LIMITES_SENHA[_k][0] >= 12, f"{_k} sem minimo de administrador"
bs.ask_secret = _ask_real
print("  OK senha de pessoa: recusa usuario/obvia, minimo 12 nas de administrador")

# 5b) segredos do OPNsense: os seis saem preenchidos, sem pergunta quando o
#     cache ja tem root/PPPoE, e valor existente nunca e trocado.
import base64 as _b64
# Perguntas viram stubs: "Enter" = aceita a sugestão, ou mantém a atual.
_enter = lambda d, sug, **k: None if k.get("atual") else sug
bs.perguntar_senha = _enter
_ssid_real = bs.perguntar_ssid
bs.perguntar_ssid = lambda d, padrao: padrao or "rede-teste"
cache = {"root_hash": bs.bcrypt_hash("raiz-teste"), "pppoe": _b64.b64encode(b"ppp").decode()}
opn, opn_h = bs.segredos_opnsense(None, pub, "homelab.example", cache)
assert all(opn[k] for k in bs.OPNSENSE_SEGREDOS), [k for k in bs.OPNSENSE_SEGREDOS if not opn[k]]
assert opn["opnsense_admin_password_hash"].startswith(("$2b$", "$2y$"))
try:
    import bcrypt as _bc
    assert _bc.checkpw(opn["opnsense_admin_password"].encode(), opn["opnsense_admin_password_hash"].encode()), \
        "hash do admin nao confere com a senha entregue nas credenciais"
except ImportError:
    pass  # sem o modulo, o hash veio do mkpasswd; o prefixo acima ja foi conferido
assert _b64.b64decode(opn["opnsense_admin_authorizedkeys_b64"]).decode().strip() == pub
assert _b64.b64decode(opn["opnsense_gui_cert_crt_b64"]).startswith(b"-----BEGIN CERTIFICATE-----")
assert b"PRIVATE KEY" in _b64.b64decode(opn["opnsense_gui_cert_key_b64"])
assert [c for _d, c, _v in opn_h] == ["opnsense_admin_password"], opn_h
# prod e dev no mesmo cache: mesmo valor, sem regerar
opn2, _ = bs.segredos_opnsense(None, pub, "homelab.example", cache)
assert opn2 == opn, "dev saiu com segredos do OPNsense diferentes de prod"
# hash existente sem senha guardada: NAO inventa senha nova que nao abre nada
ex = {k: "existente" for k in bs.OPNSENSE_SEGREDOS}
opn3, h3 = bs.segredos_opnsense(ex, pub, "homelab.example", {})
assert all(opn3[k] == "existente" for k in bs.OPNSENSE_SEGREDOS), "valor existente foi trocado"
assert not h3 and not opn3["opnsense_admin_password"], "senha entregue sem o hash correspondente"
print("  OK segredos do OPNsense: 6 preenchidos, hash confere, existente preservado")

# 5d) senhas de PESSOA e nomes de Wi-Fi: perguntados (Enter aceita a sugestão),
#     sugestão digitável em teclado ABNT, e --senhas troca só o que mudou.
_resp_w = {**resp, "wifi": {"main": "casa", "iot": "homelab-iot", "media": "tv", "guest": "visita"}}
bs.perguntar_ssid = _ssid_real
bs.ask = lambda label, default="", obrigatorio=True: default or "casa-iot"  # Enter; padrao vazio -> digita
bs.ask_secret = lambda label, obrigatorio=True: ""             # externos (Cloudflare...): Enter
bs.step = lambda *_a: None
seg2, hum2, troc2 = bs.montar_segredos(None, pub, "homelab.example", {**cache}, _resp_w)
_humanas = {c for c, t, h, d in bs.GENERATED if h}
assert _humanas | {"opnsense_admin_password"} <= {c for _d, c, _v in hum2}, hum2
for _d, c, v in hum2:
    assert re.fullmatch(r"[A-Za-z0-9]+", v), f"sugestao de {c} tem simbolo (nao passa no teclado ABNT da console)"
assert seg2["wifi_iot_ssid"] == "casa-iot" and seg2["wifi_main_ssid"] == "casa", seg2["wifi_iot_ssid"]
assert {"wifi_main_ssid", "wifi_main_password"} <= troc2
_v2 = yaml.safe_load(bs.render_vault("prod", _resp_w, seg2, pub))
assert _v2["wifi_iot_ssid"] == "casa-iot", "render_vault nao usou o SSID escolhido"
# existente + --senhas + Enter em tudo: nada muda
_ex = {**seg2}
_, _, troc3 = bs.montar_segredos(_ex, pub, "homelab.example", {**cache}, _resp_w, trocar=True)
assert not troc3, f"Enter deveria manter tudo, mudou: {troc3}"
# existente + --senhas + senha nova digitada: so ela muda
bs.perguntar_senha = lambda d, sug, **k: "NovaSenhaDoWifi2026" if "principal" in d else _enter(d, sug, **k)
seg4, _, troc4 = bs.montar_segredos(_ex, pub, "homelab.example", {**cache}, _resp_w, trocar=True)
assert troc4 == {"wifi_main_password"}, troc4
bs.perguntar_senha = _enter
print(f"  OK senhas de pessoa perguntadas ({len(hum2)}), sugestao digitavel, --senhas troca so o que mudou")

# 5c) vault existente e COMPLETADO: chave vazia/ausente entra, preenchida fica.
import subprocess as _sp, tempfile as _tf, shutil as _sh
if _sh.which("ansible-vault"):
    with _tf.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        senha = d / "pass"; senha.write_text("teste\n")
        alvo = d / "vault.yml"
        _sp.run(["ansible-vault", "encrypt", "--vault-password-file", str(senha),
                 "--encrypt-vault-id", "default", "--output", str(alvo)],
                input="a: 'mantido'\nb: ''  # PENDENTE\n", text=True, check=True, cwd=d)
        bs.VAULT_PASS, bs.ANSIBLE, bs.REPO = senha, d, d
        bs.completar_vault(alvo, {"a": "mantido", "b": ""},
                           "a: 'NOVO'\nb: '$2b$x/y'\nc: 'Q+/='\nd: ''\n", False)
        out = _sp.run(["ansible-vault", "view", "--vault-password-file", str(senha), str(alvo)],
                      capture_output=True, text=True, check=True, cwd=d).stdout
        v = yaml.safe_load(out)
        # d: ausente no vault, entra mesmo vazia (opcional precisa existir)
        assert v == {"a": "mantido", "b": "$2b$x/y", "c": "Q+/=", "d": ""}, v
        assert "..." not in out, "marcador de fim de documento no meio do vault"
        assert oct(alvo.stat().st_mode)[-3:] == "600"
        # --senhas: chave PREENCHIDA so muda se estiver em `sobrescrever`
        bs.completar_vault(alvo, v, "a: 'TROCADA'\nb: 'outra'\n", False, {"a"})
        v2 = yaml.safe_load(_sp.run(["ansible-vault", "view", "--vault-password-file", str(senha), str(alvo)],
                                    capture_output=True, text=True, check=True, cwd=d).stdout)
        assert v2["a"] == "TROCADA" and v2["b"] == "$2b$x/y", v2
    print("  OK vault existente: vazio completado, preenchido preservado, 0600")
else:
    print("  – ansible-vault ausente, completar_vault pulado")

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
        assert gv.get("ntp_interno") == "true", f"ntp nao chegou ao host: {gv}"
        print(f"  OK ansible-inventory: aceito, grupos={sorted(k for k in inv if k!='_meta')}")
        print(f"     ntp_interno resolve em node1 = {gv['ntp_interno']}")

# 7) sem NTP interno, a linha sai COMENTADA -> so as publicas valem
        resp_sem = {**resp, "rede_atual": {**resp["rede_atual"], "ntp_interno": ""}}
        ini2 = bs.render_hosts("prod", resp_sem)
        assert "\n# ntp_interno=" in ini2, "sem NTP interno deveria sair comentado"
        assert "\nntp_interno=" not in ini2, "nao deveria definir a variavel"
        p2 = os.path.join(d, "h2.ini"); open(p2, "w").write(ini2)
        r2 = subprocess.run(["ansible-inventory", "-i", p2, "--list"], capture_output=True, text=True)
        assert r2.returncode == 0, f"inventario sem NTP rejeitado: {r2.stderr[:300]}"
        assert "ntp_interno" not in json.loads(r2.stdout)["_meta"]["hostvars"]["node1"]
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
        # Endereco PCI e PER-HOST, no no que tem o dispositivo: o
        # 00-proxmox-api-token.yml cria o mapping onde a variavel existe. Num
        # no errado, o mapping apontaria para o dispositivo de outro host.
        # O firewall e acessado pelo admin nomeado: o 1o apply desliga o login
        # de root, e um inventario com root nao entraria na 2a execucao.
        assert hv["opnsense"].get("ansible_user") == resp["dominio_admin_user"], hv["opnsense"]
        # NAS: imagem cloud do Debian, chave no usuario `debian`, root recusado
        assert hv["omv"].get("ansible_user") == "debian", hv["omv"]
        assert hv["no-um"].get("opnsense_wan_pci_id") == "0000:05:00.0", hv["no-um"]
        assert hv["no-um"].get("opnsense_wan2_pci_id") == "0000:04:00.0", hv["no-um"]
        assert "omv_hba_pci_id" not in hv["no-um"], "HBA do NAS vazou para o primario"
        assert hv["no-dois"].get("omv_hba_pci_id") == "0000:05:00.0", hv["no-dois"]
        assert "opnsense_wan_pci_id" not in hv["no-dois"], "WAN vazou para o secundario"
        assert "pci_id" not in tf, "endereco PCI voltou ao tfvars: o hostpci e por mapping"
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
