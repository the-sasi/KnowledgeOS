# tests

Tests exist from the start so every module is testable as it is written.

| Folder         | Scope                                                          |
| -------------- | -------------------------------------------------------------- |
| `unit/`        | Single module in isolation, no I/O, fast                        |
| `integration/` | Modules working together, real containers, real files           |
| `evaluation/`  | Quality of retrieval and agent output, driven by datasets       |

`unit/` and `integration/` answer *"is it correct?"*.
`evaluation/` answers *"is it good?"* — expect scores and thresholds, not
pass/fail assertions.

Test runner not chosen yet; mirror the `services/` layout inside each folder.
