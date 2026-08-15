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
                 "hypervisor1": "192.0.2.5", "hypervisor2": "", "omv": "192.0.2.15",
                 "opnsense": "192.0.2.2", "openwrt": "", "dev": "",
                 "ntp_interno": "192.0.2.1"},
  "nos": {"primario": {"nome": "no-um"}, "secundario": {"nome": "no-dois"}},
  "rede_alvo": {"cidr": 24, "gateway": "198.51.100.1", "manager": "198.51.100.10",
                "worker": "198.51.100.11", "omv": "198.51.100.15", "dev": "198.51.100.20"},
  "opnsense_wan_pci_id": "0000:05:00.0",
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

# 3) tfvars -> as 9 obrigatorias presentes e CIDR nos IPs
tf = bs.render_tfvars(resp, seg, pub)
obrig = ["proxmox_api_token_id","proxmox_api_token_secret","proxmox_node_primary",
         "proxmox_node_secondary","docker_swarm_node01_ip","docker_swarm_node02_ip",
         "swarm_gateway","omv_nas_ip","dev_node_ip"]
faltando = [k for k in obrig if not re.search(rf'^{k}\s*=', tf, re.M)]
assert not faltando, f"tfvars sem: {faltando}"
assert '"198.51.100.10/24"' in tf, "IP do manager sem CIDR"
assert '"198.51.100.1"' in tf, "gateway nao deveria ter CIDR"
print("  OK tfvars: 9 obrigatorias presentes, CIDR so onde deve")

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
else:
    print("  – ansible-inventory ausente, pulado")
