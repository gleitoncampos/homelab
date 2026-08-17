# `expected/*.diff`

`make render-opnsense` concatena estes arquivos em ordem alfabética (glob) e
compara o resultado contra `diff -u --label baseline --label novo` entre
`baseline/opnsense-fase0.xml` e o render atual. Por isso **o nome de cada
arquivo codifica a posição do hunk no XML renderizado, não a ordem em que a
task que o gerou foi executada** — nomear pela task quebra a comparação
assim que uma task posterior mexe em algo mais cedo no arquivo. Terceira vez
que essa confusão custa uma investigação a alguém; da próxima, comece por
aqui.

O cabeçalho `--- baseline` / `+++ novo` só aparece uma vez em todo `diff -u`
— pertence sempre a quem ordena primeiro (hoje `fase-0a.diff`). Uma fase
nova cujo hunk fica ANTES do que hoje é o primeiro arquivo herda esse
cabeçalho e tira as duas linhas do antigo primeiro.

Gere um fragmento novo com `diff -u --label baseline --label novo baseline
novo.xml` — sem os `--label`, o cabeçalho embute caminho de tmpdir e mtime,
e o arquivo nunca mais bate.

`fase-2a.diff` (fase sem efeito líquido no XML) foi removido nesta rodada:
vazio, não contribuía nada à concatenação, e um arquivo de teste vazio numa
pasta de testes lê como um teste que passa.
