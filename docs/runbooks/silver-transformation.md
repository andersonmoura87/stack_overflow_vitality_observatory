# Transformação local Bronze → Silver

1. Execute transform-questions com --bronze-path existente, --silver-path e --quarantine-path distintos e sem sobreposição. Use --dry-run para verificar páginas/checksums sem escrita. --run-id aceita UUID Bronze opcional.
2. Execute sem --dry-run. Exit 0 indica completed ou empty; falha operacional retorna 1, argumentos inválidos retornam 2. Itens rejeitados por qualidade podem coexistir com completed: confira quarantined_records e quality_errors.
3. Leia o manifesto em <silver-path>/manifests. Selecione a maior commit_sequence de completed/empty para o dataset, verifique output_checksums e use somente output_paths dessa geração. Nunca trate um diretório generation isolado como publicação.
4. Investigue quarentena por source_path, source_page e source_item_index. quality_events registra todas as violações; quarantined_records registra um resumo por item. Não cole corpos ou segredos nos logs.
5. Corrija a origem sintética/configuração quando aplicável e execute novamente. Não edite páginas Bronze ou gerações existentes. Replay mantém observações e arquivos idênticos, mas produz novo manifesto por tentativa. Regras/schema alterados exigem migração explícita para nova raiz.

## Falhas e recuperação

Falha de leitura/checksum, escrita ou confirmação impede manifesto completed dessa tentativa. A geração anterior permanece selecionável. Um erro ao publicar o manifesto pode impedir até o diagnóstico failed; confira exit code e logs estruturados. Arquivos órfãos não são commits e podem ser inspecionados sem tocar a geração anterior. Após crash, a confirmação de entrega ao chamador pode ser ambígua: inspecione se existe terminal íntegro antes de repetir.

O lock local <silver-path>/.transform.lock usa criação exclusiva. Lock órfão exige confirmar que não existe processo ativo antes de removê-lo manualmente. Não há remoção automática nem lease distribuído. Filesystem precisa permitir hard links; não use filesystem remoto como se oferecesse transação distribuída.

Antes de remover artefatos órfãos, identifique todos os output_paths referenciados por manifestos concluídos. Não existe garbage collector nesta versão. Manifestações de corrupção em estado anterior interrompem processamento; restaure cópia íntegra ou reconstrua em nova raiz a partir da Bronze preservada. A raiz absoluta participa do dataset_id: mover o diretório exige reconstrução/migração deliberada.

Logs JSON contém IDs, contagens, status, duração e tipo de erro, sem payload. O corpo máximo padrão é 500000 caracteres (--max-body-chars). Ausência legítima de corpo é informativa; pontuação negativa é permitida. Leia o [contrato](../data-contracts/silver_questions.md) para distinção entre erro de item e erro da execução.
