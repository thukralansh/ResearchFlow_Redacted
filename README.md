# ResearchFlow (Redacted)

ResearchFlow is an AI-native workspace for structured technical research. This public snapshot preserves the executable architecture: a FastAPI backend, Streamlit client, PostgreSQL/Supabase persistence layer, Alembic migrations, typed domain and application boundaries, and workflow orchestration.

This repository intentionally excludes internal planning, design documents, prompt libraries, examples tied to private work, test material, historical Git metadata, and all local secrets.

## Local setup

Use Python 3.12 and install dependencies with `uv sync`. Copy `.env.example` to `.env` and supply local service configuration. Start the API with `uv run uvicorn researchflow.main:app --app-dir backend --reload`; start the interface with `uv run streamlit run apps/streamlit/app.py`.

The included migrations and Supabase configuration define the local persistence architecture. Environment values are intentionally not included.
