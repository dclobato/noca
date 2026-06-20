# Checklist de Implementação do Webcast no NOCA

Este documento organiza, em formato de checklist, os passos necessários para implementar no NOCA uma exportação compatível com o protocolo de webcast observado no BOCA e consumido pelo `maratona-animeitor`.

Documento de referência:

- [webcast.md](/home/dclobato/boca/doc/webcast.md)

## Objetivo

Implementar no NOCA uma funcionalidade de placar remoto compatível com o consumidor legado, produzindo um ZIP com:

- `contest`
- `runs`
- `version`
- `time`
- `icpc`

e semântica compatível com o reveleitor/animeitor observado.

## 1. Decisão de escopo

- [ ] Confirmar se a meta é compatibilidade estrita com o consumidor atual do `maratona-animeitor`
- [ ] Confirmar se a meta inclui apenas o placar remoto ou também a experiência completa de revelação pós-freeze
- [ ] Decidir se a compatibilidade será implementada:
  - [ ] diretamente no modelo de domínio do NOCA
  - [ ] em uma camada de adaptação/exportação separada

Recomendação:

- [ ] Implementar em camada separada de exportação

## 2. Ponto de entrada HTTP

- [ ] Definir a rota HTTP de exportação do webcast no NOCA
- [ ] Definir se a rota será síncrona ou usará geração/cache assíncrona
- [ ] Garantir resposta binária com o corpo do ZIP
- [ ] Definir cabeçalhos HTTP apropriados para download binário

Decisões recomendadas:

- [ ] Expor uma rota dedicada, separada do placar HTML/API normal
- [ ] Tornar o endpoint idempotente e seguro para polling frequente

## 3. Controle de acesso

- [ ] Definir o mecanismo equivalente ao `webcastcode`
- [ ] Definir a persistência desse código no NOCA
- [ ] Definir como o código se relaciona a:
  - [ ] contest
  - [ ] site/sede
  - [ ] subconjunto de times
- [ ] Registrar tentativas válidas e inválidas de acesso

Se houver necessidade de compatibilidade funcional com o modelo do BOCA:

- [ ] Suportar filtro por grupos de times
- [ ] Suportar filtro por ranges numéricos ou outra abstração equivalente

## 4. Modelo de exportação

- [ ] Criar um modelo interno para o ZIP de webcast
- [ ] Separar claramente:
  - [ ] modelo interno do NOCA
  - [ ] modelo legado exportado
- [ ] Definir uma camada de serialização específica para o protocolo legado

Estruturas mínimas sugeridas:

- [ ] `WebcastContestExport`
- [ ] `WebcastRunExport`
- [ ] `WebcastZipBundle`

## 5. Arquivo `contest`

- [ ] Implementar geração do arquivo `contest`
- [ ] Garantir o formato de linhas na ordem esperada
- [ ] Garantir uso do separador `0x1C`

Campos obrigatórios:

- [ ] linha 1: nome do contest
- [ ] linha 2, campo 1: duração máxima em minutos
- [ ] linha 2, campo 2: valor legado compatível para `current_time`
- [ ] linha 2, campo 3: freeze do placar em minutos
- [ ] linha 2, campo 4: penalidade exportada
- [ ] linha 3: número de times e número de problemas
- [ ] linhas de times: `team_login`, instituição, nome
- [ ] penúltima linha: `1` + `1`
- [ ] última linha: `numProblems` + `Y`

Pontos de atenção:

- [ ] Garantir que `team_login` seja estável e único
- [ ] Garantir letras de problema compatíveis com o consumidor
- [ ] Decidir qual campo interno do NOCA será mapeado para instituição
- [ ] Definir um fallback seguro se a instituição estiver ausente

## 6. Arquivo `runs`

- [ ] Implementar geração do arquivo `runs`
- [ ] Garantir uso do separador `0x1C`
- [ ] Garantir uma linha por submissão exportada

Campos obrigatórios por linha:

- [ ] `run_id`
- [ ] tempo da submissão em minutos
- [ ] `team_login`
- [ ] letra do problema
- [ ] status exportado

Pontos de atenção:

- [ ] Definir um `run_id` estável e monotônico
- [ ] Garantir que o tempo seja expresso em minutos
- [ ] Garantir que o histórico preserve ordem estável de submissão
- [ ] Excluir runs removidas/canceladas, se isso existir no NOCA

## 7. Mapeamento de status

- [ ] Definir o mapeamento dos veredictos internos do NOCA para o conjunto legado:
  - [ ] `Y`
  - [ ] `N`
  - [ ] `?`
  - [ ] `X`

Mapeamento mínimo a decidir:

- [ ] Accepted -> `Y`
- [ ] Waiting / queued / judging -> `?`
- [ ] Wrong answer comum -> `N`
- [ ] Compilation-like / special administrative verdicts -> `X` ou `N`, conforme estratégia

Ponto crítico:

- [ ] Decidir explicitamente quais veredictos internos do NOCA devem virar `X`

Lembrar:

- [ ] No consumidor observado, `X` não penaliza
- [ ] No consumidor observado, `N` penaliza em `20`

## 8. Freeze e relógio

- [ ] Definir a fonte de verdade do freeze no NOCA
- [ ] Exportar o freeze no terceiro campo da segunda linha de `contest`, em minutos
- [ ] Gerar o arquivo `time` em segundos

Decisões necessárias:

- [ ] O `time` refletirá o tempo corrente do contest?
- [ ] O `time` será limitado ao fim do contest?
- [ ] O `time` será limitado ao freeze ou ao fim, para manter compatibilidade?

Para compatibilidade com o BOCA observado:

- [ ] Exportar `time` em segundos
- [ ] Manter o freeze como valor separado em `contest`
- [ ] Permitir que o consumidor reaplique o freeze localmente

## 9. Arquivos `version` e `icpc`

- [ ] Gerar `version` com `1.0`
- [ ] Gerar `icpc`, mesmo que vazio

Recomendação:

- [ ] Manter ambos por compatibilidade de layout do ZIP

## 10. Geração do ZIP

- [ ] Implementar a criação do ZIP com os cinco arquivos
- [ ] Garantir nomes exatos dos arquivos:
  - [ ] `contest`
  - [ ] `runs`
  - [ ] `version`
  - [ ] `time`
  - [ ] `icpc`
- [ ] Garantir que o consumidor consiga abrir o ZIP sem depender do caminho interno

Recomendação:

- [ ] Gravar os arquivos na raiz do ZIP

## 11. Ordenação e estabilidade

- [ ] Garantir estabilidade de exportação entre chamadas consecutivas
- [ ] Garantir que os times saiam em ordem previsível
- [ ] Garantir que as runs saiam em ordem previsível

Recomendação:

- [ ] Ordenar runs por `(submission_time, submission_id)`
- [ ] Ordenar times por uma ordem estável de cadastro ou login

## 12. Compatibilidade de score

Se a meta for compatibilidade estrita com o consumidor atual:

- [ ] Tratar `N` como tentativa penalizante
- [ ] Assumir penalidade efetiva de `20` por `N`
- [ ] Tratar `X` como não-penalizante
- [ ] Garantir que o consumidor consiga recalcular o placar apenas com `contest`, `runs` e `time`

Se a meta for compatibilidade funcional, mas não estrita:

- [ ] Documentar qualquer desvio do modelo legado
- [ ] Validar se o consumidor existente tolera esse desvio

## 13. Modelagem interna no NOCA

- [ ] Mapear conceitos do NOCA para o modelo legado

Checklist sugerido:

- [ ] contest do NOCA -> `contest`
- [ ] team identity do NOCA -> `team_login`
- [ ] scoreboard freeze do NOCA -> terceiro campo de `contest`
- [ ] submission timestamp do NOCA -> minutos em `runs`
- [ ] verdict pipeline do NOCA -> status legado

Pontos a decidir:

- [ ] como representar times sem instituição
- [ ] como exportar problemas se o NOCA usar identificadores diferentes de letras
- [ ] como exportar múltiplos contests concorrentes

## 14. Cache e desempenho

- [ ] Definir se o ZIP será regenerado a cada request
- [ ] Definir se haverá cache por contest/código
- [ ] Definir invalidação do cache em:
  - [ ] nova submissão
  - [ ] mudança de verdict
  - [ ] mudança de freeze
  - [ ] mudança de times/problemas

Recomendação:

- [ ] Usar cache curto e invalidação explícita
- [ ] Evitar cache longo para `contest` se metadados puderem mudar durante operação

## 15. Observabilidade

- [ ] Registrar acessos válidos ao endpoint
- [ ] Registrar acessos inválidos ao endpoint
- [ ] Registrar falhas de geração do ZIP
- [ ] Registrar inconsistências de serialização
- [ ] Expor métricas de:
  - [ ] tempo de geração
  - [ ] tamanho do ZIP
  - [ ] número de runs exportadas
  - [ ] número de times exportados

## 16. Testes de serialização

- [ ] Criar testes unitários para `contest`
- [ ] Criar testes unitários para `runs`
- [ ] Criar testes unitários para `time`
- [ ] Criar testes unitários para o ZIP completo

Casos mínimos:

- [ ] contest vazio não permitido
- [ ] um único time, um único problema
- [ ] múltiplos times e múltiplos problemas
- [ ] runs com `Y`, `N`, `?`, `X`
- [ ] freeze antes e depois do tempo corrente

## 17. Testes de compatibilidade

- [ ] Gerar ZIPs de fixture no NOCA
- [ ] Validar esses ZIPs com o consumidor do `maratona-animeitor`
- [ ] Comparar ranking calculado pelo consumidor com ranking esperado
- [ ] Comparar comportamento de freeze
- [ ] Comparar comportamento de revelação

Estratégia sugerida:

- [ ] começar com fixtures pequenas e determinísticas
- [ ] evoluir para fixtures próximas de contest real

## 18. Testes de interoperabilidade ponta a ponta

- [ ] Subir o consumidor real contra um ZIP exportado pelo NOCA
- [ ] Validar:
  - [ ] leitura do ZIP
  - [ ] parse de `contest`
  - [ ] parse de `runs`
  - [ ] timer
  - [ ] freeze
  - [ ] ranking
  - [ ] reveleitor

## 19. Decisões de produto a fechar antes da implementação

- [ ] O NOCA vai exportar todos os contests ou apenas contests explicitamente habilitados?
- [ ] O acesso será por token simples, segredo compartilhado ou assinatura temporária?
- [ ] O NOCA vai exportar placar por sede/site, por subconjunto de times ou apenas global?
- [ ] O NOCA vai aderir ao comportamento legado de `X` não penalizante?
- [ ] O NOCA vai aderir à penalidade fixa de `20` na camada exportada?

## 20. Critérios de aceite

- [ ] O endpoint devolve um ZIP válido
- [ ] O ZIP contém os cinco arquivos esperados
- [ ] `contest` e `runs` usam `0x1C`
- [ ] `time` sai em segundos
- [ ] o consumidor real do `maratona-animeitor` consegue processar o ZIP
- [ ] o ranking calculado bate com o esperado
- [ ] o freeze calculado bate com o esperado
- [ ] a revelação pós-freeze funciona sem divergência observável

## Ordem sugerida de implementação

- [ ] 1. Implementar o modelo de exportação
- [ ] 2. Implementar serialização de `contest`
- [ ] 3. Implementar serialização de `runs`
- [ ] 4. Implementar `time`, `version` e `icpc`
- [ ] 5. Implementar empacotamento ZIP
- [ ] 6. Expor endpoint HTTP
- [ ] 7. Adicionar autenticação por código/token
- [ ] 8. Criar fixtures e testes unitários
- [ ] 9. Validar com o consumidor real
- [ ] 10. Ajustar incompatibilidades sem contaminar o modelo interno do NOCA

## Recomendação final

Para reduzir risco de integração:

- [ ] tratar o webcast legado como protocolo de interoperabilidade
- [ ] manter a lógica interna do NOCA separada da serialização legada
- [ ] validar cedo com o consumidor real, não apenas com testes locais de parse
