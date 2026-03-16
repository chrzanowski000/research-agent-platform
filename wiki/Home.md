# Research Agent Platform — Wiki

Welcome to the engineering wiki for **agents-self-reflect**, a multi-agent AI research platform.

---

## Who This Wiki Is For

- **Engineers** onboarding to the codebase
- **Operators** deploying or maintaining the platform
- **Technical stakeholders** who want to understand what the system does and how it runs

---

## Start Here

New to the project? Read in this order:

1. **[Overview](Overview.md)** — What the platform does and why it exists
2. **[Applications](Applications.md)** — Every service explained (ports, deps, roles)
3. **[Manual: Architecture](Manual-Architecture.md)** — How requests flow through the system
4. **[Demonstration](Demonstration.md)** — How to run a live demo end-to-end

Then pick a deployment guide based on your environment:

- Local Docker → **[Docker Deployment](Manual-Deployment-Docker.md)**
- Local Kubernetes (Docker Desktop) → **[Kubernetes Deployment](Manual-Deployment-Kubernetes.md)**
- Production (EKS) → **[Kubernetes Deployment](Manual-Deployment-Kubernetes.md)** (EKS section)

---

## All Pages

| Page | Description |
|------|-------------|
| [Overview](Overview.md) | Platform purpose, capabilities, and tech stack |
| [Applications](Applications.md) | Per-service documentation |
| [Demonstration](Demonstration.md) | End-to-end demo guide and example prompts |
| [Manual: Architecture](Manual-Architecture.md) | Request flow, agent pipeline, Mermaid diagrams |
| [Manual: Agent Graphs](Manual-Agent-Graphs.md) | LangGraph node-by-node docs for all three agents |
| [Manual: Deployment — Docker](Manual-Deployment-Docker.md) | Docker Compose setup and startup |
| [Manual: Deployment — Kubernetes](Manual-Deployment-Kubernetes.md) | K8s local, Helm, and EKS deployment |
| [Manual: Configuration and Secrets](Manual-Configuration-and-Secrets.md) | All env vars, secret management, 1Password flow |
| [Manual: Operations and Troubleshooting](Manual-Operations-and-Troubleshooting.md) | Day-2 ops, logs, restarts, common failures |

---

## Repository Layout (Quick Reference)

```
agents-self-reflect/
├── services/
│   ├── chat-ui/           # Next.js 15 frontend
│   ├── langgraph-api/     # Python LangGraph agents
│   └── persistence-api/   # FastAPI research history API
├── infrastructure/
│   ├── k8s/               # Kustomize manifests (base, dev, prod)
│   ├── helm/              # Helm chart
│   └── docker/            # Dockerfiles
├── scripts/               # Build, deploy, secret injection
├── tests/                 # Python unit tests
├── docker-compose.yml     # Legacy Docker Compose (see Docker page)
└── .env_tpl               # Environment variable template (1Password refs)
```
