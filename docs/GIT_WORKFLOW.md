# Git delivery workflow

Code delivery repository: https://github.com/AustinYI111/agent_quant_2

Develop MVP modules on `feature/quant-mvp`. Preserve existing acquisition tasks when changing branches. Do not modify remote `main` directly.

For each runnable module, run relevant tests and inspect the changes before committing. Record validation results and material data limitations. Keep downloaded datasets, private holdings and credentials out of commits. Push the development branch when an authorized GitHub connection is available; never report a push as successful without verifying it.

The user's latest workflow message ended after item 3 (tests/checks); no additional unpublished workflow requirements are assumed.
