gold: uv run uvicorn composer_api:gold_app --port 8000
bronze: uv run uvicorn composer_api:bronze_app --port 8003
admin: uv run uvicorn composer_admin:admin_app --port 8001
django: uv run --project apps/dashboard python apps/dashboard/manage.py runserver 8002
frontend: npm --prefix apps/frontend run dev
