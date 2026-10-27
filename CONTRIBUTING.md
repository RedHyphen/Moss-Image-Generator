# Contributing to Moss-Image-Generator

Start with [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). It is the canonical
guide for the repository layout, coding conventions, local commands, testing
requirements, and runtime boundaries.

Before opening a change:

1. Keep runtime state under ignored `data/`; never stage databases, uploads,
   generated images, model caches, `.env` files, or credentials.
2. Make the smallest change that fixes the underlying cause and add a
   regression test when behavior changes.
3. Run `make check` after backend or frontend changes. Run the live browser
   acceptance path for any prompt, queue, worker, storage, or UI submission
   change.
4. Record blocked hardware/integration checks honestly in
   `docs/TEST_RESULTS.md`; do not infer GPU inference from unit tests.

Pull requests should explain the symptom, execution path, root cause, changed
boundary, validation performed, and remaining risk.
