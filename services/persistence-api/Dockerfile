FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install base agent dependencies (needed for shared imports)
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install API-specific dependencies
COPY research_persistence_api/requirements.txt ./research_persistence_api/requirements.txt
RUN pip install --no-cache-dir -r research_persistence_api/requirements.txt

COPY config.py ./config.py
COPY agents/ ./agents/
COPY research_persistence_api/ ./research_persistence_api/

EXPOSE 8001
CMD ["uvicorn", "research_persistence_api.main:app", "--host", "0.0.0.0", "--port", "8001"]
