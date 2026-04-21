# Contributing To HALF

HALF is in early open source (v0.x), and the current maintainer is a single
developer. Keep changes small and discuss larger ideas in an issue first.

## Before You Start

1. Search existing issues and pull requests.
2. For non-trivial changes, open an issue before sending a PR.
3. Read [`docs/prd_final.md`](./docs/prd_final.md) and
   [`docs/tech_spec.md`](./docs/tech_spec.md) before larger refactors.

## Development Setup

See the local development section of [`README.md`](./README.md).

## Running Tests

```bash
cd src/backend && python -m pytest tests/ -v
cd src/frontend && npm test && npm run build
```

A PR that breaks either of these will not be merged.

## Pull Request Checklist

- [ ] Tests pass locally (`pytest` + `npm test` + `npm run build`).
- [ ] New behavior is covered by tests.
- [ ] If you changed environment variables, `src/.env.example` is updated.
- [ ] If you changed API shapes, `docs/tech_spec.md` is updated.
- [ ] Commit messages describe why, not just what.
- [ ] No secrets, private URLs, or personal paths in the diff.

## UI Strings And i18n

The UI is currently mostly in Simplified Chinese. English i18n is welcome, but
prefer a dedicated i18n PR rather than mixing translation work into unrelated
feature changes.

## Code Style

- Backend: follow the style of the existing codebase
- Frontend: follow the style of the existing codebase

## License

By contributing, you agree that your contributions are licensed under the MIT
License.
