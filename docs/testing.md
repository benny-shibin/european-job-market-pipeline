# End-to-end testing

## Run locally

From the repository root:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
# Show one scenario with its pipeline output:
python -m pytest tests/test_e2e.py::test_pipeline_and_dashboard -v -s
```

`pytest.ini` sets the test directory and root import path. No API credentials, network requests, or pre-existing datasets are needed.

## Coverage

The primary scenario mocks only the HTTP boundary and the ingestion pagination delay. It writes real Bronze JSON into a pytest temporary directory, copies the skill configuration and dashboard there, and invokes transformations as subprocesses with the current Python interpreter. Assertions cover pagination, newer-observation deduplication, Silver validation, skill counts, nullable dimension joins, execution of analytics SQL, and stable fact contents after rebuilding.

Streamlit AppTest reads the resulting temporary Gold database and exercises title search, skill filtering, remote filtering, empty results, reset, and listing row counts. Additional scenarios cover zero detected skills, invalid Silver data, unexpected API schemas, and HTTP failures. HTTP failures must not produce a page file. The optional incremental collector is also exercised across new, unchanged, and updated observations; the test checks SQLite version state and rebuilds Silver/Gold from its archived pages.

## Boundaries

These are offline pipeline and application integration tests. AppTest does not launch a browser or verify CSS contrast, animation, layout, keyboard behavior, download contents, or external posting links. It also does not validate availability or the changing schema of the live API. Manually review the dashboard at desktop and narrow viewport widths after visual changes, including open dropdown menus, selected filters, empty results, and reduced-motion mode.

Tests use pytest-managed temporary directories and never rebuild the repository's `data/` directory. CI runs the same command on Ubuntu with Python 3.12. CI failures include subprocess stdout/stderr in pytest's failure report; reproduce the failing test locally before changing expectations.
