# Política de segurança

Este repositório descreve a infraestrutura de um **homelab pessoal**. Não é um
produto, não tem usuários além do dono e não expõe serviço a terceiros. Isso
muda o que faz sentido reportar — e o que não faz.

## Como reportar

Use o **Private Vulnerability Reporting** do GitHub: aba *Security* →
*Report a vulnerability*. O relato fica privado entre você e o mantenedor até
haver correção.

**Não abra issue pública** para falha de segurança. Issue é indexada por
buscador em minutos, e o objetivo é corrigir antes de divulgar.

Resposta em até 7 dias. Sendo um projeto pessoal, não há SLA além disso.

## O que vale a pena reportar

- **Segredo vazado em arquivo versionado ou no histórico** — token, chave,
  senha, certificado. É o modo de falha que este repositório já teve, e o
  motivo de existir o gate em [`.githooks/`](./.githooks/).
- **Dado pessoal** que tenha escapado: e-mail, endereço, identificador.
- **Configuração insegura por padrão** que outra pessoa copiaria daqui e
  levaria para a infraestrutura dela — porta administrativa publicada,
  autenticação desligada, TLS ausente, permissão excessiva num volume.
- **Automação destrutiva** — um playbook que apaga dado sem confirmação, ou uma
  task que sobrescreve estado sem checar.

## O que **não** é vulnerabilidade aqui

Para poupar seu tempo e o meu:

- **IP privado, VLAN e faixa de rede em exemplo.** Os exemplos usam as faixas
  de documentação da RFC 5737 (`192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`). Onde aparece endereço de topologia, é intencional e está
  isento no gate — ver [`.githooks/secret-patterns.txt`](./.githooks/secret-patterns.txt).
- **Versão de imagem desatualizada.** O [Renovate](./.renovaterc.json) abre PR
  de atualização; um CVE numa imagem pinada é backlog, não incidente.
- **Serviço sem autenticação numa stack do catálogo que nenhum playbook
  implanta.** Definição não implantada não é vulnerabilidade — a maior parte
  das stacks em `docker/_catalog/` ainda precisa de ajuste antes de subir. Mas
  a stack cujo `stack_compose_path` já aparece num playbook do Ansible conta
  como implantada, esteja ela em `docker/` ou em `docker/_catalog/`: falha
  nela é reportável como em qualquer outra.
- **Ausência de hardening que o próprio README já lista como pendente.** O
  backlog de hardening é mantido fora do versionamento, justamente por
  enumerar o que ainda não está protegido; item conhecido não vira advisory.
- **Resultado de scanner sem impacto demonstrado.** Relato gerado por
  ferramenta, sem caminho de exploração descrito, será fechado.

## Escopo

Só este repositório. A infraestrutura em si não é alvo autorizado: **não faça
varredura, teste de intrusão ou qualquer acesso a hosts, domínios ou serviços
mencionados aqui.** Não há autorização, e boa parte do que aparece em exemplo
nem corresponde à realidade.
