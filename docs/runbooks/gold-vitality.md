# Gold descritiva local

1. Confirme uma geração Silver committed em `--silver-path`. O comando usa o maior `commit_sequence` `completed` ou `empty` e recusa manifesto ausente.
2. Escolha `--gold-path` sem sobreposição com a Silver. `--dry-run` calcula o corte e as contagens e não cria arquivos.
3. `--as-of` é obrigatório para publicar e deve ser UTC terminado em `Z`. Sem ele, a execução real falha antes de escrever. `--dry-run` sem `--as-of` imprime o maior `collected_at` com `as_of_source=derived_max_collected_at` e não cria arquivos. `--dry-run` com `--as-of` usa o valor informado. Timestamp inválido ou sem timezone falha antes da escrita.
4. Sem `--dry-run`, a publicação fica em `<gold-path>/vitality/generation=<id>/` e o manifesto em `<gold-path>/manifests/<id>.json`. Exit 0 imprime o caminho. Exit 1 é falha de leitura, overlap ou confirmação. Exit 2 é argumento inválido.
5. Leia o manifesto impresso. Confira `output_checksums`, as contagens Silver e `questions_without_observation`. Não escolha um diretório `generation` isolado.

Repetir o mesmo corte na mesma raiz reencontra os mesmos bytes. Outro `--as-of` cria outra geração e preserva a anterior. Lock órfão em `<gold-path>/.gold.lock` só deve ser removido depois de confirmar que não há escritor ativo.

Os três sinais de resposta não se substituem. `exposure_seconds_*` acompanha a leitura do mês recente. O manifesto declara `causal_claims=unsupported`: a série não estima efeito de ferramenta, tempo até resposta ou sobrevivência. O contrato está em [gold_vitality.md](../data-contracts/gold_vitality.md).
