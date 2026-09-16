# Arquitetura implementada

CLI com configuração YAML -> cliente HTTP substituível -> páginas Bronze imutáveis -> prepared -> CAS do watermark -> manifesto terminal. Apenas esse fluxo local existe. API/Dump/Survey -> Silver -> Gold -> dashboard/NLP é arquitetura futura.

`RunContext` determina um único diretório por contexto de execução, baseado no início UTC da janela efetiva. Backoff válido é consumido pelo coletor; retries HTTP respeitam backoff mandatório sem reduzi-lo. Bronze usa hard link exclusivo e validação integral dos bytes. Watermark inclui fonte/site/endpoint/tag e usa compare-and-set protegido por lock local. Backfill preserva o watermark.

A janela entre CAS e publicação terminal não é atômica. Prepared permite investigar interrupções, sem promessa de recuperação automática. Veja ADR-007, contratos v2 e runbooks. O módulo Terraform declara apenas a versão mínima da ferramenta e não provisiona recursos.
