FROM langchain/langgraph-api:latest
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY config.py .
COPY langgraph.json .

EXPOSE 2024
# CMD inherited from base image — reads langgraph.json automatically
