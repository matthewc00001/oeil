# Contributing to Oeil

Thank you for your interest in contributing to Oeil!

## Development Setup

```bash
git clone https://github.com/openema/oeil.git
cd oeil/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8090
```

## Project Structure

- `backend/` — FastAPI application
- `backend/services/` — Core business logic (ONVIF, recorder, ANPR, etc.)
- `backend/routers/` — REST API endpoints
- `frontend/` — Single-page web app (vanilla JS)
- `config/` — Default configuration files
- `systemd/` — Service unit files
- `install.sh` — Debian installer

## Pull Request Guidelines

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit with clear messages
4. Open a PR against `main`

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

**Mathieu Cadi — Openema SARL**
