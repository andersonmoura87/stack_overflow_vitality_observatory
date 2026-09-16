# Metodologia de pesquisa

A investigação considera como descoberta, aprendizagem, documentação, validação e resolução de problemas no Stack Overflow mudaram após a popularização da IA generativa. Não pressupõe declínio, crescimento ou substituição da plataforma.

## Unidades e fontes

A coleta disponível observa páginas de perguntas por site, tag e janela temporal. Cada snapshot preserva parâmetros, instante de recuperação, identificador da execução e checksum. Coletas por tag são independentes; uma pergunta pode aparecer em várias delas. A normalização futura deverá deduplicar entidades sem perder proveniência ou versões observadas.

Data Dump e Developer Survey são fontes futuras independentes. A API não garante representação completa do histórico. Respostas de Survey descrevem percepções e comportamento declarado, não comprovam comportamento observado na plataforma. Comparações entre anos exigirão mapeamento versionado das perguntas da Survey.

## Métricas planejadas

Vitalidade será examinada por atividade, resolubilidade, tempo até resposta, sobrevivência das perguntas, distribuição de tópicos e concentração das contribuições. Perguntas sem resposta ao fim da observação exigem tratamento de censura; ausência de resposta não equivale a um tempo observado de resolução.

Comparações devem apresentar composição por tag, períodos de observação, disponibilidade das fontes, incerteza e análises de sensibilidade. Mudanças de políticas, tráfego, mecanismos de busca, ecossistemas tecnológicos e composição das perguntas podem confundir associações temporais. Alegações causais exigem desenho identificador específico.

## Avaliação de conteúdo e NLP

Complexidade técnica se refere ao conteúdo, nunca à maturidade de pessoas. A taxonomia provisória contém Fundamentos, Desenvolvimento aplicado, Produção, Arquitetura e Plataforma e IA. Um classificador futuro deverá registrar nível, probabilidade, evidência e versão. A avaliação exige amostragem estratificada, anotação humana, macro-F1, matriz de confusão e concordância entre avaliadores.

Sinais relacionados à IA devem diferenciar menção incidental, uso efetivo, problema causado por IA e meta-discussão. Métodos de tópicos baseados em tags, léxico e embeddings exigirão avaliação, sementes controladas e proveniência de dados/modelos. Sentimento permanece exploratório: termos técnicos negativos não são medidas diretas de qualidade, satisfação ou dificuldade.

## Limites atuais

Essas métricas e modelos ainda não estão implementados. O produto disponível fornece ingestão e proveniência Bronze para análises futuras. Dados derivados publicados deverão manter contratos, versões, evidências rastreáveis e revisão de licença e privacidade, conforme o [data card](data_card.md).
