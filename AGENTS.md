# Agent notes

Read [DEVELOPER.md](DEVELOPER.md) for the layout and commands, and
[docs/usage.md](docs/usage.md) for what the tool does.

- Run `make lint test` before you finish. Both must pass.
- Code lives in `src/tkn_innerloop/`. Modules import names from their siblings
  (`from .repo import git`), so a test that replaces a function must patch it
  in every module that imported it. Use `patch_names` from `tests/helpers.py`.
- Behaviour described in `docs/usage.md` and the skill in
  `skills/tekton-innerloop/SKILL.md` must match the code. Update them together.
- Plans and caches live under `${XDG_CACHE_HOME:-~/.cache}/tkn-innerloop/`.
  Never write them into the repository being checked.
- Public text (docs, site, commit messages) uses plain language, no em dashes.
