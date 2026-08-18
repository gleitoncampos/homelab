# Portainer Rescue

Corrige o `portainer.db` (BoltDB) depois de restaurar o backup de um convidado Swarm (ver [`docs/runbooks/restauracao.md`](../../../docs/runbooks/restauracao.md)) num Swarm que foi reiniciado: o cluster passa a ter um `SwarmId` diferente do que está gravado no banco, e o Portainer deixa de reconhecer o ambiente e as stacks já cadastradas. `portainer_rescue.sh` builda uma imagem Docker que compila `patch_swarmid.go`, que usa a biblioteca `bbolt` para trocar o `SwarmId` antigo pelo do Swarm atual dentro dos buckets `stacks` e `endpoints`, e em seguida compacta o arquivo.

⚠️ **É uma operação rara e destrutiva.** Um patch malformado, ou uma compactação interrompida no meio, corrompe o `portainer.db` de forma irrecuperável — perdendo todas as stacks, ambientes, usuários e permissões cadastrados no Portainer, e exigindo reconfigurar tudo do zero pela interface.

⚠️ **Faça uma cópia real do arquivo antes de rodar, por fora da ferramenta.** O `patch_swarmid.go` cria um `.bak` sozinho, mas, quando origem e destino estão no mesmo sistema de arquivos, esse `.bak` é um **hardlink** — o mesmo inode do original, não uma cópia independente — e não protege contra a escrita que o próprio patch faz logo depois. Copie (`cp`) o `portainer.db` para fora deste diretório, em outro disco, antes do passo 1 abaixo.

## Como usar

1. Pare o Portainer (`docker service scale portainer=0`, ou remova a stack) antes de tocar no arquivo: o BoltDB só admite um processo escrevendo por vez, e editar o `.db` com o Portainer ainda rodando arrisca corromper os dois.
2. Copie o `portainer.db` para fora deste diretório (o backup real, ver aviso acima).
3. Rode `./portainer_rescue.sh` sem argumento, se o `portainer.db` estiver na mesma pasta dos scripts, ou `./portainer_rescue.sh /caminho/para/portainer.db` apontando o arquivo.
4. Suba o Portainer de novo e confira que os ambientes e as stacks aparecem íntegros.
