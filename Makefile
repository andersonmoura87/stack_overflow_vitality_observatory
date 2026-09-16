check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run pytest

terraform-check:
	terraform fmt -check -recursive infra/terraform
	terraform -chdir=infra/terraform validate
