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

## A ordem NÃO sai do glob

`make render-opnsense` ordena os fragmentos com `ls | LC_ALL=C sort`, e não com
a expansão do `*.diff`. A collation de locale ignora pontuação: sob
`en_US.UTF-8` o shell devolve

```
fase-1a2.diff
fase-1a.diff
fase-1b.diff
```

— `1a2` **antes** de `1a` — enquanto a ordem byte a byte devolve os três na
ordem esperada. O sintoma é a comparação falhar sem que nenhum fragmento esteja
errado, e foi assim que a tentativa de dar arquivo próprio ao hunk do
`gw_switch_default` falhou.

⚠️ Prefixar `LC_ALL=C` no `cat` **não** resolve: quem expande o glob é o shell,
antes de o `cat` existir.

Consequência ao criar fragmento novo: entre `fase-1a` e `fase-1b` não existe
letra, e um sufixo numérico ordena de forma dependente de locale em qualquer
`cat` escrito à mão. Hunk que caia nessa faixa **entra no arquivo anterior** —
foi o que se fez com o `gw_switch_default`, cujo hunk (`-330`) vive em
`fase-1a.diff` junto dos de `-22` e `-138`. A ordem por posição continua válida:
os três precedem o `-368` que abre o `fase-1b`.
