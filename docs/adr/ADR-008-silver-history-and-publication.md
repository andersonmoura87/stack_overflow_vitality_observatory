# ADR-008: Histórico Silver de conteúdo e observações de métricas

- **Status:** accepted

## Contexto e alternativas

1. SCD2 de conteúdo e métricas reproduz cada alteração de visualizações como uma versão completa, aumentando volume sem mudança de conteúdo.
2. Histórico de conteúdo separado de observações de métricas mantém evolução e linhagem sem repetir HTML em cada observação.
3. Histórico de conteúdo com somente métricas atuais é menor, mas descarta observações necessárias a análises futuras.

## Decisão

Adotar a alternativa 2. `question_id` é a chave natural, qualificada por `site`. Um catálogo armazena conteúdo por `record_hash`; observações pequenas armazenam métricas, instante de recuperação e linhagem. `questions.jsonl` materializa segmentos consecutivos de conteúdo com validade observacional `[valid_from, valid_to)`, em UTC; isso não representa o instante real de edição na fonte. Cada segmento mostra as métricas e a linhagem da última observação nele. `observations.jsonl` preserva todas as observações aceitas, inclusive origens distintas com conteúdo igual.

O hash inclui schema, site, question_id, title_raw, body_html, tags normalizadas, creation_at, last_edit_at, owner_user_id, content_license, link, closed_at e closed_reason. Não inclui métricas, last_activity_at, is_answered, accepted_answer_id, timestamps de coleta ou linhagem. Score pode ser negativo (votos); contadores view_count/answer_count não podem. Ausência legítima permanece null e não se preenche conteúdo ausente com observações anteriores.

Observações são identificadas pelo SHA-256 de site/run/página/checksum do arquivo Bronze/índice do item. Replay da mesma entrada não cria observação ou versão adicional. Nova origem preserva nova linhagem. Snapshots tardios são inseridos por collected_at e a história é reconstruída; A->B->A produz três segmentos. No mesmo instante, desempate lexical por record_hash e observation_id define uma única representação do conteúdo; todas as observações permanecem disponíveis e divergências recebem warning. Não se inventa ordem cronológica entre empates.

## Persistência e publicação

JSON Lines canônico é adotado: não há engine Parquet nas dependências atuais. Parquet exigiria pyarrow ou outra dependência de maior porte. A evolução futura deve manter o contrato lógico e verificar equivalência de nulos, UTC e listas.

Cada geração completa contém catálogo, observações e perguntas; seu caminho é derivado do hash das saídas. Portas distintas representam leitura Bronze, Silver, quarentena e manifestos. Saídas são publicadas imutavelmente e verificadas antes do manifesto terminal. Um manifesto terminal completed/empty, publicado atomicamente por último, é o commit da geração; não há ponteiro separado que possa divergir. Leitores usam o maior commit_sequence bem-sucedido para o mesmo dataset e confirmam checksums. Manifestações failed não alteram a geração corrente. Um lock local por dataset serializa a leitura/mescla/publicação; não é distribuído. Quarentena e eventos têm paths por transformation_run_id. Falha pode deixar saídas órfãs, nunca visíveis como geração committed sem manifesto válido.

## Limitações e consequências

Reconstrução em memória e reescrita da geração são adequadas apenas ao volume local controlado; não são uma solução de escala para todo o Dump. Manter gerações permite auditoria, mas não há compactação ou limpeza automática. Fim de validade é observacional. Não há garantias de completude de uma coleta Bronze; a transformação processa somente páginas selecionadas. Snapshot Bronze ilegível ou checksum divergente falha a execução; registros inválidos em páginas íntegras vão para quarentena. A saída Silver com registros válidos pode ser completed mesmo com quarentena, com contagens explícitas.

## Parser

Usar `html.parser.HTMLParser` da biblioteca padrão: sem novas dependências, parser tolerante a fragmentos, tratamento de entidades e extração separada de code. Regex não é parser HTML. Texto narrativo exclui code, script e style; código preserva whitespace e quebras. HTML bruto permanece intacto. Não é um sanitizador HTML para renderização web; consumidores não devem renderizar body_html sem controles próprios.
