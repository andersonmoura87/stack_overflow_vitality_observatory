# Runbook: objeto Bronze já existe

O adaptador compara tamanho e SHA-256 dos bytes canônicos completos, com parse JSON. Bytes idênticos são replay sem reescrita. Divergência de payload ou metadados gera `ImmutableConflict` e preserva o original. Checksum de payload inválido gera `IntegrityError` antes da publicação.

Não apagar nem sobrescrever para “resolver” conflito. Conferir run_id, janela, metadados e conteúdo. Nova observação tem novo UUID; replay pela porta Python registra a origem e preserva recuperação. Manifestos terminais são imutáveis. Temporários não confirmam sucesso.
