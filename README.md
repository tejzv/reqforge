# ReqForge

HAR and cURL to clean, minimal, typed Python (`httpx` / `curl_cffi`), TypeScript, and JavaScript SDKs — 100% local with zero LLMs.

## Features

- **HAR & cURL Ingest**: Parses Chrome DevTools `.har` exports (1.2, HTTP/2 pseudo-headers, base64 responses, cookies, form/JSON payloads) and Bash/POSIX (`$'...'`), Windows CMD (`^`), PowerShell, and multi-command cURL dumps.
- **Noise & Tracker Filtering**: Drops static assets (`.css`, `.js`, fonts, images, media), preflight `OPTIONS`, HTML navigations, and common analytics/telemetry beacons, plus tracking cookies (`_ga`, `_fbp`, `_hj*`, etc.) and browser-bloat headers (`sec-ch-ua*`, `sec-fetch-*`, `priority`, `dnt`, etc.).
- **Auth Detection & Secret Parameterization**: Flags `Authorization` (Bearer/JWT, Basic, HMAC), `X-*-Key`, CSRF tokens, `cf_clearance`, and session cookies, extracting them into constructor arguments and `from_env()` helpers with masked audit reports.
- **Live Header & Cookie Minimization Probe**: Replays captured requests against target endpoints using delta debugging (`ddmin`) to isolate the exact minimal headers and cookies required for an equivalent response, with `httpx` vs `curl_cffi` browser TLS auto-detection.
- **Dynamic Parameter & Schema Inference**: Clusters multi-request paths into parameterized routes (`/users/{user_id}/orders`), detects UUID/numeric/prefixed/opaque path IDs, splits GraphQL operations by `operationName`, and merges JSON samples into Pydantic v2 models and TypeScript interfaces.
- **SDK & Smoke Test Export**: Outputs connection-pooled async Python clients (`httpx` or `curl_cffi` with retry/backoff and `Retry-After`) or zero-dependency typed TypeScript / ESM JavaScript `fetch` clients, plus runnable smoke tests.
- **Local Web Studio & Static Bundle Export**: Launch `reqforge ui` for an interactive local workbench (with CORS-free live probing), or `reqforge export-web` to emit a standalone static browser bundle powered by Pyodide.

## Quick Start

```bash
pip install -e ".[all]"
```

Analyze a HAR capture or cURL dump:

```bash
reqforge analyze capture.har
```

Generate a Python `httpx` / `curl_cffi` SDK with live header probing:

```bash
reqforge generate request.curl --target python --probe --out ./sdk
```

Generate all targets (Python, TypeScript, JavaScript):

```bash
reqforge generate capture.har --target all --out ./generated_sdks
```

Launch local interactive Web Studio:

```bash
reqforge ui
```

Export static browser bundle:

```bash
reqforge export-web --out ./dist_web
```

