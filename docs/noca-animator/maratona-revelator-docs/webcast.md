# Engenharia Reversa do Webcast do BOCA

Este documento descreve, como especificação observada, o protocolo de exportação consumido por `src/admin/report/webcast.php` e a semântica efetivamente aplicada pelo consumidor real usado no `maratona-animeitor`.

O objetivo é servir como base de implementação de um placar remoto compatível em outra aplicação, como o NOCA.

## Escopo

O documento cobre:

- como `webcast.php` autoriza e gera o ZIP
- quais arquivos entram no ZIP
- formato de cada arquivo
- semântica real usada pelo consumidor
- regras de reconstrução do placar
- dinâmica de freeze e revelação
- implicações técnicas para uma implementação compatível

O documento não tenta descrever a UI do consumidor. O foco é o contrato de dados e a lógica de domínio.

## Fontes validadas

### BOCA

- `/home/dclobato/boca/src/admin/report/webcast.php`
- `/home/dclobato/boca/src/admin/report/header.php`

### Consumidor real

- `/home/dclobato/maratona-animator/server/run_reveleitor.sh`
- `/home/dclobato/maratona-animator/server/service/src/webcast.rs`
- `/home/dclobato/maratona-animator/server/service/src/dataio.rs`
- `/home/dclobato/maratona-animator/server/data/src/lib.rs`
- `/home/dclobato/maratona-animator/server/data/src/revelation.rs`
- `/home/dclobato/maratona-animator/client-v2/src/views/reveleitor.rs`
- `/home/dclobato/maratona-animator/client-v2/Cargo.toml`
- `/home/dclobato/maratona-animator/Makefile`
- `/home/dclobato/noca/README.md`

## Resumo executivo

`src/admin/report/webcast.php` exporta um ZIP binário com um snapshot simplificado do contest. Esse ZIP contém:

- `contest`
- `runs`
- `version`
- `time`
- `icpc`

O consumidor real:

- lê `contest`, `runs` e `time`
- ignora `version` para lógica de domínio
- ignora `icpc` porque ele sai vazio no BOCA atual
- reconstrói o placar a partir de `runs`
- reaplica freeze com base no arquivo `contest`
- mantém uma fila de revelação pós-freeze

Conclusão prática: para compatibilidade, o produtor precisa emitir corretamente `contest`, `runs` e `time`. Os demais arquivos são secundários.

## Visão geral do protocolo

O endpoint `webcast.php` funciona como um exportador autenticado por código. O cliente chama:

```text
/admin/report/webcast.php?webcastcode=<codigo>
```

Quando o código é aceito, a resposta HTTP contém o conteúdo binário de um ZIP.

O ZIP não contém o placar final pronto. Ele contém dados suficientes para um consumidor reconstruir:

- lista de times
- configuração temporal do contest
- histórico de submissões
- relógio atual

## Geração do ZIP no BOCA

O fluxo observado em `webcast.php` é:

1. validar `webcastcode`
2. localizar esse código em `private/webcast.sep`
3. extrair os filtros de site e faixa de usuários associados ao código
4. gerar o diretório `private/webcast.<codigo>`
5. escrever arquivos texto nesse diretório
6. compactar o diretório em `private/webcast.<codigo>.zip`
7. devolver o conteúdo do ZIP no corpo da resposta

Se o `webcastcode` não existir em `webcast.sep`, o script registra a tentativa em `webcast.log` e encerra sem gerar saída útil.

## Controle de acesso e filtragem

O arquivo `private/webcast.sep` é a tabela de autorização e filtragem do exportador.

Cada linha tem este formato lógico:

```text
<webcastcode> <site-ou-faixa> <site-ou-faixa> ...
```

Cada token após o código pode ser:

- `3`
- `3/10/25`

Semântica observada:

- `3`: inclui o site 3
- `3/10/25`: inclui o site 3, com usuários de número entre 10 e 25

O exportador constrói três vetores internos:

- site
- lower user
- upper user

Esse filtro é aplicado:

- na lista de times do arquivo `contest`
- na lista de runs do arquivo `runs`

## Estrutura do ZIP

O ZIP contém:

- `contest`
- `runs`
- `version`
- `time`
- `icpc`

Os arquivos `contest` e `runs` usam o caractere `0x1C` como separador de campos. Em muitos editores esse caractere aparece invisível ou como um símbolo de controle.

Para uma implementação compatível, trate esse separador explicitamente como `FS` e não como tab, pipe ou ponto e vírgula.

## Arquivo `contest`

O arquivo `contest` carrega metadados do contest e a lista de equipes exportadas.

### Estrutura observada

Linha 1:

- nome do contest

Linha 2:

- campo 1
- campo 2
- campo 3
- campo 4

Linha 3:

- número de times
- número de problemas

Próximas `N` linhas:

- `team_login`
- instituição
- nome visível do time

Penúltima linha:

- `1`
- `1`

Última linha:

- `numProblems`
- `Y`

### Exemplo

```text
Maratona BOCA 2026
30030030020
35
team01USPTime Alpha
team02UNICAMPTime Beta
team03UFMGTime Gamma
11
5Y
```

### Semântica no BOCA

O BOCA escreve na segunda linha:

1. `contestduration`
2. `contestlastmileanswer`
3. `contestlastmilescore`
4. `contestpenalty`

Todos os valores, exceto nomes e contagens, são emitidos em minutos.

### Semântica usada pelo consumidor real

O consumidor interpreta a segunda linha como:

1. `maximum_time`
2. `current_time`
3. `score_freeze_time`
4. `penalty`

Na prática, isso gera esta equivalência:

- `maximum_time <- contestduration`
- `current_time <- contestlastmileanswer`
- `score_freeze_time <- contestlastmilescore`
- `penalty <- contestpenalty`

Observação importante:

- o `current_time` que realmente dirige o relógio da aplicação não vem desse campo
- o relógio real vem do arquivo `time`

Portanto, para compatibilidade, o campo 2 de `contest` pode ser tratado como legado pelo consumidor, desde que `time` seja fornecido corretamente.

### Origem dos times

Os times vêm de `usertable`, com os filtros:

- `contestnumber = 1`
- `userenabled = 't'`
- `usertype = 'team'`
- restrição adicional por site/faixa derivada de `webcast.sep`

### Observações de compatibilidade

- a instituição é extraída de `userdesc` por parsing textual, o que é frágil
- o exportador fixa `contest = 1` e `site = 1`
- o arquivo `contest` é cacheado por até uma hora no diretório `webcast.<codigo>`

## Arquivo `runs`

O arquivo `runs` representa o histórico de submissões exportado.

### Estrutura observada

Cada linha contém:

1. `run_id`
2. tempo da submissão em minutos
3. `team_login`
4. problema
5. status simplificado

### Exemplo

```text
10115team01AN
10218team02BY
10322team01AY
10430team03C?
10531team02DX
```

### Status emitidos pelo BOCA

- `Y`: accepted
- `N`: rejeição comum
- `?`: not answered yet
- `X`: resultado especial

O status `X` é emitido para:

- `NO - Compilation error`
- `NO - Contact staff`
- `NO - Name mismatch`

### Filtro aplicado pelo BOCA

O exportador:

- ignora runs com status `deleted`
- reaplica o filtro de times autorizado pelo `webcastcode`
- aceita o parâmetro opcional `runtimege`, em minutos

### Ordenação no consumidor real

O consumidor não usa `run_id` como única fonte de ordenação.

Ele:

1. lê as linhas do arquivo
2. inverte a ordem lida
3. atribui um campo interno `order`
4. ordena as runs por `(time, order)`

Para compatibilidade, a melhor prática no produtor é continuar emitindo as runs em ordem estável de submissão. O consumidor observado já protege contra ambiguidades usando `(time, order)`.

## Arquivo `time`

O arquivo `time` contém um único inteiro, em segundos.

### Semântica no BOCA

O valor gravado é:

- `currenttime`, se `currenttime < freezeTime`
- `freezeTime`, caso contrário

No entanto, o código sobrescreve `freezeTime` com `siteduration`. Na prática:

- durante o contest, `time` tende a refletir o tempo atual
- após o final, `time` tende a refletir a duração total

### Semântica no consumidor real

O consumidor usa `time` como relógio efetivo da aplicação.

O estado de freeze é calculado como:

```text
current_time >= score_freeze_time * 60
```

Logo:

- `time` está em segundos
- `score_freeze_time` está em minutos

Para compatibilidade, preserve exatamente essa convenção de unidades.

## Arquivo `version`

O BOCA grava:

```text
1.0
```

O consumidor real não usa esse arquivo para lógica de placar.

## Arquivo `icpc`

O arquivo é gerado, mas sai vazio no BOCA observado.

Existe código morto que sugere uma exportação alternativa em formato ICPC, mas ele está dentro de `if(false)` e não participa do comportamento real.

Para uma implementação compatível com o consumidor observado, esse arquivo pode continuar vazio.

## Freeze no exportador e no consumidor

### Exportador BOCA

O código do exportador sugere que já existiu intenção de mascarar resultados após freeze, mas essa lógica não está ativa na implementação observada.

Indícios:

- `freezeTime` é inicialmente calculado com base em `sitelastmilescore`
- em seguida é sobrescrito por `siteduration`
- trechos que esconderiam resultados após freeze estão comentados

Resultado prático:

- o arquivo `runs` tende a sair com o resultado conhecido de cada run, inclusive após o freeze

### Consumidor real

O consumidor não confia no exportador para fazer o freeze. Ele reaplica o freeze localmente com base no terceiro campo do arquivo `contest`.

Essa decisão é importante para integração:

- um produtor compatível não precisa mascarar `runs`
- o consumidor compatível deve ser capaz de reconstituir o freeze sozinho

## Semântica real do placar

Esta é a lógica efetivamente aplicada pelo consumidor observado.

### Modelo conceitual

O consumidor mantém:

- um `ContestFile`
- um conjunto de times
- um conjunto de problemas por time
- uma lista de runs
- uma fila de revelação pós-freeze

Para cada problema de cada time, o estado inclui:

- `solved`
- `solved_first`
- `submissions`
- `penalty`
- `time_solved`
- `answers` congeladas ainda não reveladas
- `waits` pendentes

## Reconstrução do placar

### Etapa 1: parse de `contest`

Construir:

- nome do contest
- tempo máximo
- freeze
- penalidade
- número de problemas
- mapa de times

### Etapa 2: parse de `runs`

Transformar cada linha em:

- `id`
- `time`
- `team_login`
- `prob`
- `answer`

### Etapa 3: anotação de first-to-solve

Antes de aplicar as runs, o consumidor percorre as submissões aceitas em ordem de processamento e marca a primeira solução de cada problema com `is_first = true`.

Esse bit entra no estado do problema e pode ser exibido pela UI.

### Etapa 4: aplicação das runs antes do freeze

Para cada run com:

```text
run.time < score_freeze_time
```

o consumidor aplica o resultado diretamente ao placar visível.

### Etapa 5: armazenamento das runs após freeze

Para cada run com:

```text
run.time >= score_freeze_time
```

o consumidor não aplica imediatamente o resultado real ao placar visível. Em vez disso, armazena essa run na estrutura de freeze do problema.

## Semântica de cada resposta

### `Y`

Para um problema ainda não resolvido:

- marca o problema como resolvido
- incrementa `submissions`
- soma `time` à penalidade do problema
- grava `time_solved`
- propaga o marcador `is_first`
- limpa respostas congeladas pendentes desse problema

### `N`

Para um problema ainda não resolvido:

- incrementa `submissions`
- soma `20` de penalidade

### `?`

- registra o `run_id` no conjunto de `waits`

### `X`

- não incrementa `submissions`
- não soma penalidade
- remove um eventual `wait` com o mesmo `run_id`

### Runs posteriores a um aceite

Se o problema já estiver resolvido, runs posteriores são ignoradas para efeito de placar.

## Cálculo do score

Para cada time:

- `solved = quantidade de problemas resolvidos`
- `penalty = soma das penalidades dos problemas resolvidos`
- `max_solution_time = maior tempo de solução entre os problemas resolvidos`

No consumidor observado, a penalidade por erro é efetivamente fixa em `20`, mesmo havendo um campo de penalidade no `contest`.

Portanto, para compatibilidade estrita com o consumidor real:

- `N` soma sempre `20`
- `X` não conta como erro

## Ordenação do ranking

A ordenação observada é:

1. maior número de problemas resolvidos
2. menor penalidade total
3. menor `max_solution_time`
4. `team_login` em ordem lexicográfica

Esse quarto critério é relevante para empates estáveis e deve ser preservado numa reimplementação compatível.

## Revelação pós-freeze

Depois de aplicar todas as runs:

- o placar visível contém os efeitos das runs pré-freeze
- as runs pós-freeze permanecem guardadas por problema

O consumidor então monta uma fila de revelação por time.

### Estrutura observada

A fila é baseada no score atual do time.

Cada iteração:

1. retira um time da fila
2. tenta revelar uma run congelada desse time
3. recalcula o score do time
4. reinsere o time se ainda houver runs congeladas pendentes
5. recalcula o ranking global

### Ordem de revelação dentro do time

Dentro do time:

- os problemas são percorridos em ordem de letra
- o primeiro problema com conteúdo congelado pendente é escolhido

Dentro do problema:

- as respostas congeladas são reveladas em ordem de inserção

### Implicação

O reveleitor não é apenas um replay linear de `runs`. Ele é um motor de revelação orientado pelo ranking corrente.

Isso importa se o objetivo no NOCA for reproduzir não apenas o placar remoto, mas também a experiência de revelação.

## Arquitetura do consumidor real

O consumidor observado é dividido em:

- backend Rust em `server/`
- frontend Rust compilado para WebAssembly em `client-v2/`

### Backend

Responsabilidades:

- ler o ZIP do BOCA
- parsear `contest`, `runs` e `time`
- manter o estado da competição
- expor HTTP e WebSocket

### Frontend WebAssembly

Responsabilidades:

- renderizar animeitor e reveleitor
- consumir streams do backend
- executar lógica de revelação no navegador
- reagir a comandos de teclado

O ponto mais importante é que o frontend importa diretamente o crate de domínio do backend:

```toml
data = { path = "../server/data" }
```

Ele usa diretamente tipos como:

- `ContestFile`
- `RunsFile`
- `ProblemView`
- `TimerData`
- `RevelationDriver`

Isso explica tecnicamente o uso de Rust + WASM:

- uma única implementação das regras de placar
- compartilhada entre servidor e navegador
- sem duplicação da lógica em JavaScript

## Implicações para implementação no NOCA

Se a meta for compatibilidade funcional com o protocolo observado, o mínimo necessário é:

1. gerar um ZIP com `contest`, `runs`, `time`, `version` e `icpc`
2. usar `0x1C` como separador em `contest` e `runs`
3. emitir `time` em segundos
4. emitir tempos de `contest` e `runs` em minutos
5. manter `runs` como histórico de submissões por time/problema
6. preservar os status `Y`, `N`, `?` e `X`
7. preencher o terceiro campo de `contest` com o freeze desejado

Se a meta for compatibilidade com o reveleitor atual do `maratona-animeitor`, então também é preciso aceitar estas convenções do consumidor:

- `X` não conta como erro
- a penalidade efetiva por `N` é `20`
- o ranking usa `max_solution_time` e depois `team_login` como desempate
- o freeze é reaplicado localmente no consumidor

## Recomendações práticas para o NOCA

### Se a prioridade for compatibilidade com o consumidor existente

Implemente o exportador para reproduzir o contrato observado, mesmo que ele não seja ideal.

Em especial:

- mantenha `penalty = 20` no comportamento exportado, se o objetivo for compatibilidade estrita
- trate `X` como não-penalizante
- preserve o terceiro campo de `contest` como freeze, em minutos

### Se a prioridade for limpar o protocolo

Vale separar duas camadas:

- uma camada interna, semanticamente correta para o NOCA
- uma camada de exportação compatível com o webcast legado

Isso evita contaminar o modelo interno do NOCA com decisões históricas do BOCA, como:

- penalidade fixa em `20`
- uso legado do segundo campo de `contest`
- presença de arquivos não utilizados como `icpc`

### Estratégia sugerida

Para reduzir risco:

1. implementar primeiro um exportador compatível com o formato legado
2. validar esse exportador com o `maratona-animeitor`
3. só depois considerar extensões ou um protocolo novo

## Invariantes de compatibilidade

Uma implementação compatível deve preservar estes invariantes:

- `contest` e `runs` usam `0x1C`
- `time` está em segundos
- tempos de run estão em minutos
- freeze está no terceiro campo da segunda linha de `contest`
- times são identificados por `team_login`
- problemas são identificados por letras compatíveis com o consumidor
- `X` não penaliza
- `N` penaliza em `20`
- o consumidor pode recalcular todo o placar apenas a partir de `contest`, `runs` e `time`

## Pontos frágeis observados

- o exportador BOCA fixa `contest = 1` e `site = 1`
- a instituição do time depende de parsing textual de `userdesc`
- o cache de `contest` pode atrasar alterações de metadados por até uma hora
- `version` e `icpc` não carregam valor funcional relevante no cenário observado
- a documentação antiga do `maratona-animeitor/server` menciona um `client` embutido, mas a arquitetura atual usa `client-v2`

## Conclusão

O protocolo de webcast do BOCA, no estado observado, é simples o suficiente para ser reproduzido em outro sistema, mas carrega algumas decisões históricas e algumas inconsistências entre exportador e consumidor.

A compatibilidade real depende menos do PHP em si e mais destes três contratos:

- formato do `contest`
- formato do `runs`
- semântica de score aplicada pelo consumidor

Para o NOCA, a abordagem mais segura é tratar este documento como especificação de interoperabilidade, não como modelo de domínio ideal. O modelo interno do NOCA pode continuar mais limpo, desde que a camada de exportação reproduza corretamente o contrato legado esperado pelo consumidor remoto.
