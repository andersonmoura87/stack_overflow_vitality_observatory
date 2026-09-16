# ADR-004: Local-first e alvo AWS

- **Status:** accepted

## Contexto
A Fase 0 precisa ser reproduzível, barata e testável sem credenciais.

## Decisão
Núcleo sem AWS, com interfaces injetáveis e stores locais em memória. AWS futura poderá usar S3, DynamoDB, Lambda/EventBridge e CloudWatch, mediante orçamento, IAM mínimo e Terraform validado.

## Alternativas
Construir primeiro na AWS; rejeitada por custo e acoplamento precoce. Kubernetes/Kafka/Airflow/Glue/EMR; adiados.

## Consequências
Adaptadores serão adicionados sem alterar domínio; há trabalho futuro de operação e infraestrutura.

## Riscos
Paridade local/AWS e concorrência precisam de testes específicos depois.
