# Any cloud that runs a container runs this: AWS ECS/Fargate/App Runner,
# GCP Cloud Run, Azure Container Apps, any Kubernetes, a bare VM. Nothing in
# agent_foundry/ imports a cloud-specific SDK — this container is the entire
# portability boundary.
#
# Build:  docker build -t agent-foundry .
# Run:    docker run -p 8080:8080 -e ANTHROPIC_API_KEY=... agent-foundry
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt fastapi "uvicorn[standard]"

COPY agent_foundry/ agent_foundry/
COPY prompts/ prompts/
COPY examples/ examples/

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen(f'http://localhost:{__import__(\"os\").environ[\"PORT\"]}/health')" || exit 1

# Swap this for your own agent's serve script (built with agent_foundry.serve) —
# this default containerizes the support-agent example from examples/serve_http.py.
CMD ["python", "examples/serve_http.py"]
