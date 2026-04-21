# HALF - Human-AI Loop Framework

A task management platform for orchestrating multi-agent collaboration. Designed for teams that use multiple AI coding agents (Claude Code, Codex, etc.) via subscriptions and need to coordinate task planning, dispatch, and tracking across agents through Git-based workflows.

## Features

- **Project Management** - Create projects, set goals, assign participating agents
- **Plan Generation** - Generate structured work plans via agent-assisted planning with DAG visualization
- **Task Dispatch** - One-click prompt copying for manual agent dispatch
- **Status Tracking** - Git polling-based task status detection with timeout and error handling
- **Agent Overview** - Monitor agent availability, subscription expiry, and reset schedules
- **Execution Summary** - Review task outcomes, manual interventions, and output files

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 + FastAPI + SQLite |
| Frontend | React 18 + TypeScript + Vite + React Flow |
| Deployment | Docker Compose |

## Quick Start

HALF requires a strong admin password and secret key before it will start.

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Generate a strong `HALF_SECRET_KEY` and set a strong `HALF_ADMIN_PASSWORD`:

   ```bash
   echo "HALF_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" >> .env
   echo "HALF_ADMIN_PASSWORD=<your-strong-password>" >> .env
   ```

   The admin password must be at least 8 characters and contain uppercase,
   lowercase, and digits. Never use `admin`, `example-insecure-password-placeholder`, `password`, etc.

3. Start the stack:

   ```bash
   docker compose up -d
   ```

4. Open `http://localhost:3000` and log in with the username `admin` and the
   password you set in step 2.

### Environment Variables

See [`.env.example`](./.env.example) for the full list. The required ones are
`HALF_ADMIN_PASSWORD` and `HALF_SECRET_KEY`; the stack will refuse to start
without them.

### Git Access From The Container

Out of the box, the backend container cannot reach private git repositories,
because HALF does not mount host SSH keys by default. If you need private
repo access, copy [`docker-compose.override.yml.example`](./docker-compose.override.yml.example)
to `docker-compose.override.yml` and mount **a dedicated deploy key** rather
than your entire `~/.ssh` directory.

## License

MIT License. See [LICENSE](./LICENSE) for details.
