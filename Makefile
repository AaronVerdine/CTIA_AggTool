PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
LAMBDA_PY := 3.13
LAMBDA_PLATFORM := manylinux2014_aarch64
BUILD := build

.PHONY: install lint test check serve docker bundle tf-validate clean

install: ## Create a virtualenv with dev dependencies
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -U pip
	$(BIN)/pip install -e ".[dev]"

lint:
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests
	$(BIN)/mypy

test:
	$(BIN)/pytest

check: lint test

serve: ## Run the API and web UI locally on http://localhost:8000
	$(BIN)/aggtool serve

docker: ## Build and run the local container on http://localhost:8000
	docker compose up --build

bundle: ## Build the arm64 Lambda bundle at build/lambda.zip
	rm -rf $(BUILD)/lambda $(BUILD)/dist $(BUILD)/lambda.zip
	$(BIN)/pip wheel --no-deps -w $(BUILD)/dist .
	$(BIN)/pip install --quiet --target $(BUILD)/lambda \
		--platform $(LAMBDA_PLATFORM) --implementation cp --python-version $(LAMBDA_PY) \
		--only-binary=:all: $(BUILD)/dist/*.whl
	# boto3/botocore ship with the Lambda runtime.
	rm -rf $(BUILD)/lambda/boto3* $(BUILD)/lambda/botocore* $(BUILD)/lambda/s3transfer*
	find $(BUILD)/lambda -type d \( -name __pycache__ -o -name tests \) -prune -exec rm -rf {} +
	cd $(BUILD)/lambda && zip -q -r -X ../lambda.zip .
	@du -sh $(BUILD)/lambda $(BUILD)/lambda.zip

tf-validate:
	terraform -chdir=infra/terraform fmt -check -recursive
	terraform -chdir=infra/terraform init -backend=false -input=false
	terraform -chdir=infra/terraform validate

clean:
	rm -rf $(BUILD) .pytest_cache .mypy_cache .ruff_cache .coverage .aggtool-data
