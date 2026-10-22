# Metodologia de pesquisa

A investigação considera como descoberta, aprendizagem, documentação, validação e resolução de problemas no Stack Overflow mudaram após a popularização da IA generativa. Não pressupõe declínio, crescimento ou substituição da plataforma.

## Unidades e fontes

A coleta disponível observa páginas de perguntas por site, tag e janela temporal. Cada snapshot preserva parâmetros, instante de recuperação, identificador da execução e checksum. Coletas por tag são independentes; uma pergunta pode aparecer em várias delas. A normalização futura deverá deduplicar entidades sem perder proveniência ou versões observadas.

O Data Dump é uma fonte independente. A ingestão offline grava o Bronze. `transform-data-dump` grava uma Silver separada, uma observação por snapshot, e não altera a Silver da API. O contrato trata `Posts.xml` como snapshot de um corte, não como log completo. `source_created_at` é a data de criação declarada no post. `dump_cutoff` é o corte, com `dump_cutoff_status` `operator_declared`, `unknown` ou, só no futuro, `verified`. A Fase 5.1 não emite `verified` e não consulta a Stack Exchange para confirmá-lo. `source_file_observed_at` é a inspeção do arquivo. `run_started_at` é o início da tentativa. `published_at` é a publicação do manifesto e não substitui criação, corte, inspeção nem início. `transformation_run_id` identifica a tentativa da Silver do dump e não entra na identidade da geração. Corte `unknown` não ordena estados. Corte `operator_declared` autoriza apenas comparação provisória sinalizada, não métrica causal nem corte verificado. A Fase 5.3.0 desenha essa reconciliação e não a executa. Presença no arquivo mostra o que a publicação incluiu; ausência não prova que o post nunca existiu. Score, corpo e aceite são o estado desse corte. O arquivo não fornece o instante do aceite, as versões intermediárias nem o histórico de exclusões. A Silver da API não é reescrita por esse snapshot. Developer Survey continua sendo fonte futura. A API não garante representação completa do histórico. Respostas de Survey descrevem percepções e comportamento declarado, não comprovam comportamento observado na plataforma. Comparações entre anos exigirão mapeamento versionado das perguntas da Survey.

## Métricas planejadas

Vitalidade será examinada por atividade, resolubilidade, tempo até resposta, sobrevivência das perguntas, distribuição de tópicos e concentração das contribuições. Perguntas sem resposta ao fim da observação exigem tratamento de censura; ausência de resposta não equivale a um tempo observado de resolução.

Comparações devem apresentar composição por tag, períodos de observação, disponibilidade das fontes, incerteza e análises de sensibilidade. Mudanças de políticas, tráfego, mecanismos de busca, ecossistemas tecnológicos e composição das perguntas podem confundir associações temporais. Alegações causais exigem desenho identificador específico.

## Avaliação de conteúdo e NLP

Complexidade técnica se refere ao conteúdo, nunca à maturidade de pessoas. A taxonomia provisória contém Fundamentos, Desenvolvimento aplicado, Produção, Arquitetura e Plataforma e IA. Um classificador futuro deverá registrar nível, probabilidade, evidência e versão. A avaliação exige amostragem estratificada, anotação humana, macro-F1, matriz de confusão e concordância entre avaliadores.

Sinais relacionados à IA devem diferenciar menção incidental, uso efetivo, problema causado por IA e meta-discussão. Métodos de tópicos baseados em tags, léxico e embeddings exigirão avaliação, sementes controladas e proveniência de dados/modelos. Sentimento permanece exploratório: termos técnicos negativos não são medidas diretas de qualidade, satisfação ou dificuldade.

## Limites atuais

A Gold v1 produz indicadores descritivos mensais a partir da Silver de perguntas. A trilha de respostas preserva criação, coleta, score, aceite observado, conteúdo, cobertura e linhagem, mas não calcula tempo até a primeira resposta, tempo até o aceite, sobrevivência, ranking, sentimento ou efeito causal. Comentários, NLP e Gold de respostas não estão implementados. Dados derivados publicados mantêm contratos, versões e linhagem, conforme o [data card](data_card.md).

`creation_at` da resposta é tempo do evento na fonte. `collected_at` é tempo da observação. `last_activity_at` é o timestamp de atividade visto nessa observação e existe só na observação de métricas. `is_accepted` é estado observado, não o instante do aceite. Ausência numa coleta não prova exclusão. A API incremental pode censurar à esquerda. Tempo até a primeira resposta exige cobertura histórica; o Data Dump será necessário para um backfill confiável. Não ter coletado uma resposta não prova que ela não existiu.

## Semântica das observações Silver

Conteúdo e métricas possuem históricos separados. Mudança de visualizações não cria nova versão de conteúdo; séries futuras devem consultar as observações, enquanto questions representa intervalos de conteúdo observado. valid_from usa o instante de coleta e não estima quando a alteração efetivamente ocorreu. Snapshots tardios reconstroem os intervalos. Empates conflitantes têm desempate determinístico e warning, sem alegação de conhecer a ordem real.

Contagens de quarentena, páginas e aceitos fazem parte do manifesto e devem acompanhar qualquer análise futura de cobertura. Missing permanece null; ausência de resposta aceita não implica ausência de resolução. Nenhuma métrica de vitalidade ou inferência causal é produzida pela transformação.

## Indicadores descritivos Gold v1

A Gold lê a geração Silver committed e descreve o estado observado até `as_of`. O mês é o mês UTC de `creation_at`. Métricas vêm da última observação com `collected_at <= as_of`. Tags e fechamento vêm da versão de conteúdo vigente nesse corte, não das métricas posteriores que a materialização do intervalo possa carregar. A pergunta entra uma vez no agregado mensal. Publicar exige o corte explícito; a descoberta do maior `collected_at` fica restrita ao dry-run.

`source_is_answered`, `has_answers` e `has_accepted_answer` permanecem separados. Null de `is_answered` ou `answer_count` não vira false nem zero. `accepted_answer_id` nulo significa que nenhum identificador de resposta aceita foi observado; isso não cria data de resposta. Divergências conhecidas, como `is_answered=false` com `answer_count>0`, são contadas e não corrigidas.

`exposure_seconds_*` mede o tempo entre a criação e o corte. Meses recentes tiveram menos tempo para receber respostas. Uma queda no fim da série não é, por si, mudança de vitalidade nem efeito de uma ferramenta. Conclusão causal exigiria desenho estatístico, controles, cobertura histórica adequada e fontes complementares. O contrato está em [gold_vitality.md](data-contracts/gold_vitality.md).
