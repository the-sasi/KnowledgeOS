# tests/unit

Fast, isolated tests for a single module. No network, no containers, no data
directories. Mirror the `services/` layout here.

| File | Covers |
| --- | --- |
| `test_canonical.py` | Canonical representation: hierarchy, stats, serialization roundtrip |
| `test_html_parser.py` | HTML parser: noise removal, headings, tables, lists, normalization, errors |
| `test_detection.py` | Format detection and the parser registry |
| `test_processing_generic.py` | Architectural guards: generic namespaces, document shapes, no domain vocabulary in parser logic |
