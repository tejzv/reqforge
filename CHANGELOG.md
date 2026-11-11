# Changelog

## v0.1.0 — Initial Public Release

- **HAR & cURL Ingestion**: Full HAR 1.2 support (BOM, HTTP/2 pseudo-headers, base64 responses, cookies, form/JSON/multipart) and Bash (`$'...'` ANSI-C), Windows CMD (`^"`), PowerShell, and multi-command cURL parsing.
- **Static & Tracker Filtering**: Drops static assets, telemetry beacons, preflight `OPTIONS`, HTML navigations, browser bloat headers, and tracking cookies while flagging and parameterizing auth headers/cookies/query params.
- **Live Delta-Debugging Prober**: Binary `ddmin` minimization over headers, cookies, and optional query parameters with soft-200/HTML redirect detection and `httpx` vs `curl_cffi` TLS auto-selection.
- **Endpoint & Schema Inference**: Clusters dynamic routes, splits GraphQL and JSON-RPC operations, and merges multi-sample JSON shapes into Pydantic v2 models and TypeScript interfaces.
- **Multi-Target Codegen & Web Studio**: Generates pooled async Python (`httpx` / `curl_cffi`), typed TypeScript, and ESM JavaScript `fetch` SDKs with smoke tests, plus `reqforge ui` local studio and `reqforge export-web` standalone Pyodide browser bundle.
