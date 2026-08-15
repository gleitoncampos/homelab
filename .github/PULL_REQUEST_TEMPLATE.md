## O que muda e por quê

<!-- O diff já diz o que mudou. Escreva o motivo: que problema isto resolve,
     ou o que estava errado antes. -->

## Impacto

- [ ] Afeta stack **em execução** (`docker/`, fora de `_catalog/`)
- [ ] Afeta playbook que roda contra host real (`IaC/ansible/playbooks/`)
- [ ] Só catálogo, documentação ou ferramenta — sem efeito no que está no ar

> ⚠️ Marcando o primeiro item: diga qual serviço e o que acontece durante o
> redeploy. Mudança em `compose.yml` só chega ao ambiente depois do push para a
> branch que aquele Portainer acompanha — ver a seção *Assimetria push vs.
> Ansible* no [README](../README.md).

## Verificação

- [ ] `make check` passou localmente
- [ ] Hooks ativos (`git config core.hooksPath .githooks`)
- [ ] **Nenhum dado real** em arquivo, comentário ou mensagem de commit —
      exemplos usam RFC 5737 e `exemplo.com`
- [ ] Imagem com tag fixada, sem `:latest`
- [ ] Documentação atualizada no mesmo PR, se mudou estrutura ou catálogo

<!-- Se algum item ficou desmarcado, explique aqui em vez de apagar a linha.
     Um `--no-verify` sem motivo escrito volta para revisão. -->

## Como testar

<!-- Comando, ou o passo a passo de quem for revisar. "Não testado" é uma
     resposta aceitável desde que esteja escrita. -->
