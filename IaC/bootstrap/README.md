# `IaC/bootstrap/` — pré-requisitos do Ansible e do Terraform

Gera as **entradas** que as duas ferramentas consomem: vault cifrado,
inventário, `terraform.tfvars`, chave SSH e os padrões do gate. Não provisiona
nada, não instala pacote, não configura host.

```bash
make bootstrap                 # primeira vez: pergunta e gera
make bootstrap                 # depois: regenera, sem reperguntar
make bootstrap ARGS=--dry-run  # mostra o que faria
```

## Por que existe

O mesmo valor era pedido em vários arquivos. O gateway vivia em **três**
(`swarm_gateway` no Terraform, `mgmt_gateway_ip` no inventário, e o plano de
VLANs); a chave SSH pública em dois; hostname e domínio, além disso, alimentam
`secret-patterns.local.txt`. Cada duplicata é uma chance de divergência
silenciosa — e divergência entre inventário e tfvars não dá erro, dá
comportamento errado.

Aqui as respostas ficam em **um** arquivo e se espalham.

```
IaC/homelab.local.yml  ──┬──▶  IaC/ansible/inventories/{prod,dev}/hosts.ini
   (fonte única,         ├──▶  IaC/ansible/inventories/*/group_vars/all/vault.yml (cifrado)
    gitignorada)         ├──▶  IaC/terraform/proxmox/terraform.tfvars
                         └──▶  .githooks/secret-patterns.local.txt
```

Daí sai a propriedade que mais importa: **rodar de novo não repergunta nada**.
Regenerar a partir da mesma fonte é idempotente por construção. Para corrigir um
IP, edite uma linha e rode de novo — não repita 100 perguntas.

## As duas redes

⚠️ O bootstrap pergunta **dois** conjuntos de endereços, e não é redundância:

| onde | quais IPs | por quê |
|---|---|---|
| `hosts.ini` | os de **hoje** | é por eles que o Ansible alcança os hosts |
| `terraform.tfvars`, plano de VLANs | os **alvo** | o Terraform *cria* as VMs, já no lugar certo |

Enquanto a migração de rede não acontece, é esperado que sejam diferentes.
Confundir os dois produz timeout de SSH, cujo sintoma não diz nada sobre a causa.

## Como os segredos são tratados

As 23 chaves de segredo tratadas pelo `bootstrap.py` não são todas do mesmo
tipo, e é isso que decide o comportamento. Também não são todas subconjunto do
vault: das 23, 21 vão para `group_vars/all/vault.yml` — subconjunto das **41**
chaves de topo do `vault.yml.example` (o exemplo inclui ainda SSID, nome de
usuário e outras chaves que o bootstrap não trata); as **2** restantes, os
tokens do Terraform, vão para o `terraform.tfvars` e não estão listadas no
`vault.yml.example`:

- **12 geráveis** — senhas de banco, chaves de assinatura, JWT, senhas de
  Wi-Fi. Nenhuma pergunta: 32 bytes aleatórios são melhores que qualquer senha
  escolhida à mão, e quem as lê é o Ansible.
- **9 externas** — Cloudflare, tokens do Proxmox (destino: `terraform.tfvars`),
  GeoIP, ETPro. Impossíveis de gerar. O script imprime a URL do console e o
  comando que as produz, e aceita vazio: o `validate_vault.yml` cobra depois.
- **2 pendentes** — os hashes bcrypt do OPNsense. bcrypt não sai de `openssl`
  nem da stdlib do Python; gerar um hash de algoritmo errado produziria firewall
  sem acesso à GUI depois do apply, então o script deixa marcador e instrução.

**Segredo já existente é preservado.** O script lê o vault atual e reaproveita
o que estiver lá. Rotação é decisão, não efeito colateral de rodar um comando.

### O arquivo de credenciais

Nem todo segredo é só de máquina: você precisa saber a senha do Portainer, do
Grafana e do Wi-Fi para conseguir logar. Imprimir no terminal é ruim — vai para
o scrollback, para o log do tmux, para o buffer do emulador. Não te dar é pior.

Por isso as **6 senhas de uso humano** vão para `CREDENCIAIS.local.txt`, modo
`0600` e gitignorado. Salve no gerenciador de senhas e apague:

```bash
shred -u IaC/bootstrap/CREDENCIAIS.local.txt
```

As demais nunca são exibidas. Para consultar uma:
`ansible-vault view IaC/ansible/inventories/prod/group_vars/all/vault.yml`.

## Garantias de segurança

- **Nenhuma saída é escrita sem `git check-ignore` passar.** O repositório já
  foi contaminado por arquivo gerado em caminho errado; a checagem é
  incondicional e aborta antes de escrever.
- **Modo restrito desde a criação** (`os.open` com `0600`), não `chmod` depois —
  criar `0644` e ajustar deixa uma janela em que o segredo é legível por
  qualquer usuário da máquina.
- **Nada é lido por argumento de comando.** Argumento aparece em `ps` para
  qualquer usuário; segredo entra por `getpass`, sem eco.
- **O vault nunca passa por arquivo em texto puro no disco.** Falha no meio da
  escrita remove o arquivo parcial em vez de deixar um vault pela metade.
- **Vault existente nunca é sobrescrito.** Para acrescentar chave, use
  `ansible-vault edit`.
- **Chave SSH pergunta sobre passphrase**, com o padrão sendo *com*. Chave sem
  passphrase dá acesso root à frota inteira num arquivo.

## Ordem em relação às outras ferramentas

1. **`make bootstrap`** ← aqui
2. `IaC/terraform/proxmox/` — cria VMs e CTs (`terraform.tfvars` já preenchido)
3. `IaC/ansible/playbooks/00-*` a `05-*` — sistema, cluster e stacks

O passo 3 depende do inventário e do vault que este script gera. O passo 2
depende do `terraform.tfvars`. Rodar o Ansible antes do bootstrap falha com
variável indefinida, que é o desfecho desejado — ver a regra de não usar
`default()` em variável de segredo no [`CLAUDE.md`](../../CLAUDE.md).

## Testes

```bash
python3 IaC/bootstrap/test_render.py
```

Exercita os renderizadores contra os **consumidores reais**, não contra
aproximações: `ansible-inventory` para o `hosts.ini`, `yaml.safe_load` para o
vault, `terraform fmt` para o tfvars, e o próprio motor de regex do gate para os
padrões. Também verifica que prod e dev recebem senhas de banco distintas, e que
nenhum segredo de máquina vaza para o arquivo de credenciais.
