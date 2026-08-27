.PHONY: check lint type deps test

check: lint type deps test
	@echo "✅ All checks passed"

lint:
	ruff check src/

type:
	mypy src/

deps:
	deptry src/

test:
	pytest tests/ -q
