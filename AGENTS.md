# OpenAkun Development Guide for AI Agents

## Build & Test Commands
- **Run tests**: `just test` or `pytest` (uses Docker containers for Postgres/Redis)
- **Single test**: `pytest tests/test_login.py::test_function_name`
- **Lint**: `mypy openakun/` and `flake8 openakun/` (see .flake8 for ignored rules)
- **CSS build**: `just tailwind --watch` (dev) or `just tailwind` (build)
- **Coverage**: `coverage run -m pytest && coverage report`

## Code Style
- **Python**: 3.12+, async/await patterns, SQLAlchemy 2.0 with async sessions
- **Imports**: `from __future__ import annotations`, group stdlib/3rd-party/local
- **Types**: Use type hints, `from typing import Any` for generic types
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Async**: Use `async def` and `await` for DB operations, prefer async context managers
- **DB Models**: Inherit from `Base` (AsyncAttrs, DeclarativeBase), use `Mapped` type hints
- **Templates**: Jinja2 with macros, HTMX for dynamic content

## Error Handling
- Use try/except with specific exceptions, log errors with traceback
- Sentry integration available for production error tracking
- Database sessions auto-close in teardown handlers