# Catálogo — stacks descritas, mas não implantadas

Nada aqui está no ar. São definições prontas para uso futuro, mantidas fora de
`docker/` para que aquele diretório descreva **só o que roda de verdade**.

Um levantamento da produção mostrou 4 stacks em execução contra 25 diretórios no
repositório. Misturar as duas coisas fazia o repositório parecer um retrato da
infraestrutura quando era, em boa parte, uma lista de desejos — e foi o que levou
os playbooks a apontarem para stacks inexistentes sem que ninguém percebesse.

## Promover uma stack para produção

1. Confira a stack contra o [`CLAUDE.md`](../../CLAUDE.md): labels do Traefik em
   `deploy.labels`, volumes sob `/srv/containers/`, tag de imagem fixa,
   `deploy.resources.limits` e `deploy.placement.constraints`.
2. `git mv docker/_catalog/<servico> docker/<servico>`
3. Ajuste o `stack_compose_path` no playbook que a implanta — o caminho é
   relativo ao repositório, porque o Portainer clona do Git.
4. Acrescente as variáveis de ambiente que o compose exige ao `stack_env` do
   playbook, e as chaves de vault correspondentes ao
   [`vault.yml.example`](../../ansible/inventories/vault.yml.example).

## Cinco não seguem o padrão

`jdownloader`, `kasm`, `nextcloud` e `zoneminder` têm o `compose.yml` na raiz do
diretório; `obsidian` usa `docker-compose.yml`. Todas trazem `version:`,
`container_name`, volumes relativos ou `privileged` — incompatíveis com Swarm ou
proibidos pelo `CLAUDE.md`. Precisam ser portadas antes de qualquer promoção,
não só movidas.
