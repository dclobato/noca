# Backlog Técnico: Webcast Compatível no NOCA

Este documento converte o checklist de implementação do webcast em um backlog técnico orientado à execução no NOCA.

Documentos de referência:

- [webcast.md](/home/dclobato/boca/doc/webcast.md)
- [noca-webcast-implementation-checklist.md](/home/dclobato/boca/doc/noca-webcast-implementation-checklist.md)

## Objetivo

Adicionar ao NOCA uma funcionalidade de exportação de placar remoto compatível com o protocolo legado de webcast consumido pelo `maratona-animeitor`.

Resultado esperado:

- endpoint HTTP no NOCA que devolve um ZIP compatível
- compatibilidade funcional com o consumidor legado
- validação ponta a ponta com o reveleitor/animeitor

## Premissas

- o modelo interno do NOCA não deve ser distorcido para acomodar decisões históricas do BOCA
- a compatibilidade deve ser implementada em uma camada de adaptação/exportação
- a validação final deve usar o consumidor real, não apenas parse local

## Épico 1: Definição do Contrato de Compatibilidade

### Objetivo

Fechar as decisões de interoperabilidade antes da implementação.

### Tarefas

1. Definir o escopo funcional da exportação.
   Critério de saída:
   escopo documentado entre:
   - placar remoto apenas
   - placar remoto + freeze
   - placar remoto + freeze + revelação compatível

2. Definir o mecanismo de autenticação do endpoint.
   Critério de saída:
   escolha documentada entre:
   - token simples
   - segredo por contest
   - segredo por integração remota

3. Definir a granularidade da exportação.
   Critério de saída:
   documentar se a exportação será:
   - por contest inteiro
   - por sede/site
   - por subconjunto explícito de times

4. Definir o mapeamento de veredictos do NOCA para `Y`, `N`, `?`, `X`.
   Critério de saída:
   tabela de mapeamento aprovada.

5. Definir a política de compatibilidade estrita versus compatibilidade adaptada.
   Critério de saída:
   documentar explicitamente:
   - se `X` será não-penalizante na camada exportada
   - se a penalidade exportada será fixada em `20`

## Épico 2: Modelo de Exportação Webcast

### Objetivo

Criar o modelo interno de exportação sem contaminar o domínio principal do NOCA.

### Tarefas

1. Criar uma camada dedicada de exportação webcast.
   Critério de saída:
   módulo ou pacote separado do domínio principal.

2. Definir DTOs ou estruturas equivalentes para o protocolo legado.
   Critério de saída:
   existência de estruturas equivalentes a:
   - `WebcastContestExport`
   - `WebcastRunExport`
   - `WebcastZipBundle`

3. Definir a estratégia de mapeamento entre o domínio do NOCA e o modelo legado.
   Critério de saída:
   funções explícitas de transformação.

4. Definir a estratégia de serialização do separador `0x1C`.
   Critério de saída:
   helper único e reutilizável para serialização dos arquivos `contest` e `runs`.

## Épico 3: Implementação do Arquivo `contest`

### Objetivo

Serializar o arquivo `contest` exatamente no formato esperado pelo consumidor.

### Tarefas

1. Implementar serialização da primeira linha com o nome do contest.
   Critério de saída:
   linha 1 emitida corretamente.

2. Implementar serialização da segunda linha com quatro campos.
   Critério de saída:
   emissão, em minutos, de:
   - duração máxima
   - campo legado compatível
   - freeze
   - penalidade

3. Implementar serialização da terceira linha com:
   - número de times
   - número de problemas

4. Implementar serialização da lista de times.
   Critério de saída:
   cada time exportado com:
   - `team_login`
   - instituição
   - nome visível

5. Implementar as duas linhas finais legadas.
   Critério de saída:
   emissão de:
   - `1 0x1C 1`
   - `numProblems 0x1C Y`

6. Definir fallback para ausência de instituição.
   Critério de saída:
   regra estável documentada e testada.

## Épico 4: Implementação do Arquivo `runs`

### Objetivo

Serializar o histórico de submissões em formato compatível.

### Tarefas

1. Implementar extração de submissões elegíveis para exportação.
   Critério de saída:
   subconjunto de runs definido por contest e filtro de exportação.

2. Implementar cálculo do tempo da submissão em minutos.
   Critério de saída:
   função estável de conversão de timestamp interno para minutos de contest.

3. Implementar serialização de cada run com os cinco campos esperados.
   Critério de saída:
   `run_id`, `time`, `team_login`, `problem`, `status`.

4. Implementar o mapeamento de veredictos do NOCA para `Y`, `N`, `?`, `X`.
   Critério de saída:
   serialização consistente com a tabela definida no Épico 1.

5. Definir e implementar ordenação estável de runs.
   Critério de saída:
   ordenação documentada e previsível, recomendada por:
   - tempo
   - id estável

6. Definir regra para submissões removidas, canceladas ou inválidas.
   Critério de saída:
   regra documentada e coberta por teste.

## Épico 5: Implementação dos Arquivos `time`, `version` e `icpc`

### Objetivo

Completar o conjunto mínimo de arquivos exigidos pelo consumidor.

### Tarefas

1. Implementar geração de `time`.
   Critério de saída:
   valor emitido em segundos.

2. Definir a política de valor do relógio exportado.
   Critério de saída:
   regra documentada para:
   - contest em andamento
   - contest congelado
   - contest encerrado

3. Implementar geração de `version`.
   Critério de saída:
   conteúdo `1.0`.

4. Implementar geração de `icpc`.
   Critério de saída:
   arquivo presente, mesmo que vazio.

## Épico 6: Empacotamento ZIP

### Objetivo

Gerar um ZIP compatível com o layout esperado.

### Tarefas

1. Implementar builder do ZIP.
   Critério de saída:
   ZIP válido contendo:
   - `contest`
   - `runs`
   - `version`
   - `time`
   - `icpc`

2. Garantir que os arquivos sejam gravados na raiz do ZIP.
   Critério de saída:
   consumo validado pelo loader observado.

3. Garantir estabilidade do ZIP entre chamadas equivalentes.
   Critério de saída:
   serialização determinística para mesmo estado de entrada.

## Épico 7: Endpoint HTTP de Exportação

### Objetivo

Expor a exportação via endpoint do NOCA.

### Tarefas

1. Implementar a rota HTTP de exportação.
   Critério de saída:
   endpoint funcional retornando o ZIP.

2. Implementar autenticação/autorização do endpoint.
   Critério de saída:
   acesso protegido por token ou segredo.

3. Implementar logging de acessos válidos e inválidos.
   Critério de saída:
   eventos auditáveis.

4. Definir e implementar estratégia de erro.
   Critério de saída:
   respostas previsíveis para:
   - token inválido
   - contest inexistente
   - falha de serialização
   - falha de geração do ZIP

## Épico 8: Filtros de Exportação

### Objetivo

Permitir exportação controlada por contest, sede ou subconjunto de times.

### Tarefas

1. Definir o modelo de filtro de exportação no NOCA.
   Critério de saída:
   estrutura persistida ou parametrizada.

2. Implementar filtro por contest.
   Critério de saída:
   exportação limitada ao contest solicitado.

3. Implementar filtro por subconjunto de times, se necessário.
   Critério de saída:
   `contest` e `runs` exportam apenas os times autorizados.

4. Implementar filtro por sede/site, se necessário.
   Critério de saída:
   exportação consistente com a segmentação desejada.

## Épico 9: Cache e Desempenho

### Objetivo

Tornar a exportação segura para polling frequente.

### Tarefas

1. Definir política de cache.
   Critério de saída:
   decisão documentada entre:
   - sem cache
   - cache curto
   - cache com invalidação explícita

2. Implementar invalidação em eventos relevantes.
   Critério de saída:
   invalidação em:
   - nova submissão
   - alteração de verdict
   - alteração de freeze
   - alteração de times/problemas

3. Implementar métricas de geração.
   Critério de saída:
   observabilidade mínima de:
   - tempo de geração
   - número de runs
   - número de times
   - tamanho do ZIP

## Épico 10: Testes Unitários de Serialização

### Objetivo

Garantir que os arquivos produzidos tenham formato correto.

### Tarefas

1. Criar testes unitários para o arquivo `contest`.
   Critério de saída:
   cobertura das linhas obrigatórias e do separador `0x1C`.

2. Criar testes unitários para o arquivo `runs`.
   Critério de saída:
   cobertura de:
   - `Y`
   - `N`
   - `?`
   - `X`

3. Criar testes unitários para `time`, `version` e `icpc`.
   Critério de saída:
   valores corretos para casos mínimos.

4. Criar testes unitários para o ZIP completo.
   Critério de saída:
   presença e leitura correta de todos os arquivos.

## Épico 11: Testes de Compatibilidade com Fixtures

### Objetivo

Verificar a compatibilidade do exportador do NOCA com o consumidor legado.

### Tarefas

1. Criar fixtures mínimos de contest para webcast.
   Critério de saída:
   cenários pequenos, determinísticos e fáceis de inspecionar.

2. Criar fixtures com freeze e pós-freeze.
   Critério de saída:
   cenários com runs antes e depois do freeze.

3. Validar o parse das fixtures do NOCA com o consumidor legado.
   Critério de saída:
   loader do consumidor aceitando os ZIPs sem erro.

4. Validar ranking calculado pelo consumidor.
   Critério de saída:
   ranking observado igual ao esperado.

5. Validar comportamento de `X`.
   Critério de saída:
   confirmação de que `X` não penaliza no fluxo completo.

## Épico 12: Teste Ponta a Ponta com o `maratona-animeitor`

### Objetivo

Provar interoperabilidade real com o consumidor existente.

### Tarefas

1. Subir o exportador do NOCA localmente.
   Critério de saída:
   endpoint acessível com token ou segredo válido.

2. Configurar o `maratona-animeitor` para apontar para o ZIP do NOCA.
   Critério de saída:
   integração local funcional.

3. Validar carregamento do contest.
   Critério de saída:
   times e problemas aparecem corretamente.

4. Validar timer e freeze.
   Critério de saída:
   comportamento visível consistente com o esperado.

5. Validar runs panel e placar.
   Critério de saída:
   submissões e ranking coerentes.

6. Validar reveleitor pós-freeze.
   Critério de saída:
   revelação sem erro e coerente com o score.

## Épico 13: Documentação Operacional

### Objetivo

Registrar como a funcionalidade deve ser mantida e usada.

### Tarefas

1. Documentar o endpoint do webcast no NOCA.
   Critério de saída:
   rota, autenticação e parâmetros documentados.

2. Documentar o mapeamento de veredictos.
   Critério de saída:
   tabela pública para manutenção futura.

3. Documentar limitações de compatibilidade.
   Critério de saída:
   diferenças entre domínio interno do NOCA e protocolo legado registradas.

4. Documentar procedimento de validação com o `maratona-animeitor`.
   Critério de saída:
   receita reprodutível de teste ponta a ponta.

## Sequência sugerida de execução

### Fase 1: Decisão e modelagem

- Épico 1
- Épico 2

### Fase 2: Serialização básica

- Épico 3
- Épico 4
- Épico 5
- Épico 6

### Fase 3: Exposição e operação

- Épico 7
- Épico 8
- Épico 9

### Fase 4: Validação

- Épico 10
- Épico 11
- Épico 12

### Fase 5: Fechamento

- Épico 13

## Itens críticos

Os itens abaixo têm maior risco de quebrar compatibilidade:

1. mapeamento de veredictos para `Y`, `N`, `?`, `X`
2. serialização exata de `contest`
3. unidade de tempo de `runs` e `time`
4. terceiro campo da segunda linha de `contest` como freeze
5. estabilidade de `team_login`
6. estabilidade da ordem das runs
7. tratamento de `X` como não-penalizante
8. desempate do ranking no consumidor legado

## Critério de conclusão do projeto

O trabalho pode ser considerado concluído quando:

1. o NOCA gera um ZIP compatível
2. o `maratona-animeitor` consome esse ZIP sem ajustes locais
3. o placar calculado bate com o esperado em fixtures controladas
4. o freeze e a revelação se comportam como esperado
5. a implementação está documentada e testada
