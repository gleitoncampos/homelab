# Como contribuir

Contribuição é bem-vinda: correção de bug, stack nova, ajuste de playbook,
documentação. Este arquivo cobre o que é específico de quem vem de fora.

**As regras técnicas não estão aqui.** Elas vivem no
[`CLAUDE.md`](./CLAUDE.md) — padrões de Docker Swarm, labels do Traefik,
caminhos de volume, gestão de segredo, layout de `IaC/`, e o
[contrato de contribuição](./CLAUDE.md#-contrato-de-contribuição-obrigatório).
Este repositório já teve seis cópias divergentes das mesmas regras; a fonte é
uma só, e `AGENTS.md` é symlink para ela.

## Antes do primeiro commit

```bash
git clone <seu-fork>
cd homelab
git config core.hooksPath .githooks        # obrigatório: o git não versiona .git/hooks/
cp .githooks/secret-patterns.local.txt.example \
   .githooks/secret-patterns.local.txt      # opcional, veja abaixo
make hooks                                  # faz os dois passos acima
```

Sem `core.hooksPath`, os hooks existem no repositório e simplesmente não rodam.
É o modo de falha mais provável — a CI pega depois, mas você descobre tarde.

> `make bootstrap` é para quem vai **operar** um homelab a partir deste
> repositório: gera vault, inventário e `tfvars`. Para contribuir com código não
> é necessário — `make check` roda sem nada disso.

O `secret-patterns.local.txt` guarda hostnames e domínios da **sua** topologia.
Se você não hospeda nada disto, não precisa dele; o hook avisa em amarelo e
segue com os padrões genéricos.

## A regra que não tem exceção

⚠️ **Nenhum dado real entra em arquivo versionado.** Nem em exemplo, nem em
comentário, nem em mensagem de commit — que é varrida pelo mesmo conjunto de
padrões, porque mensagem não tem conserto sem reescrever o histórico.

Use sempre:

| em vez de | use |
|---|---|
| IP da sua rede | RFC 5737: `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` |
| seu domínio | `exemplo.com`, `homelab.example` |
| hostname do seu servidor | o papel: `manager-01`, `nas`, `firewall` |
| endereço MAC | faixa de documentação `00:00:5e:00:53:xx` |
| senha, token, chave | `${VARIAVEL}`, resolvida pelo Portainer ou pelo vault |

## O ciclo

1. **Abra uma issue antes**, para mudança grande ou stack nova. Evita você
   escrever algo que não entra por um motivo que já está decidido.
2. **Branch a partir de `development`.** ⚠️ `main` não é branch de integração:
   é o que o Portainer de produção clona, e merge nela publica.
3. **Commits no padrão [Conventional Commits](https://www.conventionalcommits.org/pt-br/).**
   O hook `commit-msg` valida. Assunto até 72 caracteres; o corpo explica *por
   que*, já que o diff diz *o que*.
4. **`make check` antes de abrir o PR** — roda o mesmo conjunto que a CI.
5. **Abra o PR contra `development`**, preenchendo o template.

## Stack nova

Vai em `docker/_catalog/<servico>/swarm/compose.yml`, não em `docker/`.
`docker/` guarda o que sobe pelo formulário de Stacks do Portainer; o catálogo
guarda a definição — parte dela já implantada direto de lá por um playbook do
Ansible, parte ainda sem playbook e sem deploy algum. Promover uma stack é uma
decisão de operação, não de contribuição — ver
[`docker/_catalog/README.md`](./docker/_catalog/README.md).

O checklist do compose (rede `traefik_public`, labels em `deploy.labels`, volume
sob `/srv/containers/`, tag fixada, `deploy.resources.limits`) está no
[`CLAUDE.md`](./CLAUDE.md#️-workflow-de-implementação-de-novas-stacks).

## Quando o gate reclama

`git commit --no-verify` funciona e às vezes é o certo. Se usar, **escreva o
motivo no corpo da mensagem** — o gate existe para tornar a exceção visível, não
impossível. Num PR, um `--no-verify` sem justificativa volta para revisão.

Se o gate acusou falso positivo, diga no PR: o padrão está errado e a correção é
no padrão, não no seu commit.

## O que não entra

- Configuração com dado real, mesmo que seja o seu.
- Serviço que exige porta publicada no host quando o Traefik resolveria.
- `:latest` em imagem, ou tag sem versão.
- Regra de estilo duplicada num arquivo novo. Se falta regra, ela vai no
  `CLAUDE.md`.
