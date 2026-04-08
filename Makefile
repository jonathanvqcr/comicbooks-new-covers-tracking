.PHONY: dev migrate sync-now install export publish

DYLD_LIB = /opt/homebrew/lib

dev:
	DYLD_LIBRARY_PATH=$(DYLD_LIB) backend/.venv/bin/uvicorn backend.main:app --reload --port 8000 &
	cd frontend && npm run dev

backend:
	DYLD_LIBRARY_PATH=$(DYLD_LIB) backend/.venv/bin/uvicorn backend.main:app --reload --port 8000

migrate:
	backend/.venv/bin/alembic upgrade head

sync-now:
	curl -s -X POST http://localhost:8000/api/admin/sync-now | python3 -m json.tool

install:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt
	backend/.venv/bin/playwright install chromium

export:
	backend/.venv/bin/python3 backend/scripts/export_static.py

publish: export
	git add frontend/public/data/
	git diff --staged --quiet || git commit -m "chore: update static data [skip ci]"
	git push
	PATH="$(HOME)/.nvm/versions/node/v22.12.0/bin:$(PATH)" vercel --prod --yes
