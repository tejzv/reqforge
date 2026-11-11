<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0d1117,50:161b22,100:1f6feb&height=190&section=header&text=ReqForge&fontSize=64&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=HAR%20%E2%80%A2%20cURL%20%E2%80%A2%20Zero-LLM%20Typed%20SDK%20Generator&descAlignY=62&descSize=17" width="100%" />

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=500&size=18&duration=2800&pause=1000&color=58A6FF&center=true&vCenter=true&width=720&lines=HAR+%2B+cURL+%E2%86%92+Async+Python+(httpx+%2F+curl_cffi)+%2B+TS+%2B+JS;Live+Delta-Debugging+Header+%26+Cookie+Minimization+Probe;Zero+LLMs+%E2%80%A2+100%25+Local+%E2%80%A2+Pydantic+v2+%26+TypeScript+Models" alt="ReqForge Highlights" />

<p>
  <a href="https://github.com/tejzv/reqforge/releases/latest"><img src="https://img.shields.io/github/v/release/tejzv/reqforge?style=for-the-badge&color=1f6feb&label=Release" /></a>
  <a href="https://github.com/tejzv/reqforge/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/tejzv/reqforge/ci.yml?style=for-the-badge&label=CI" /></a>
  <a href="https://github.com/tejzv/reqforge/stargazers"><img src="https://img.shields.io/github/stars/tejzv/reqforge?style=for-the-badge&color=d29922" /></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/TypeScript_%26_JS-Fetch_SDK-3178C6?style=for-the-badge&logo=typescript&logoColor=white" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-238636?style=for-the-badge" /></a>
</p>

<p>
  <a href="https://github.com/tejzv/reqforge/releases/latest"><b>Latest Release</b></a> &nbsp;&bull;&nbsp;
  <a href="#installation"><b>Installation</b></a> &nbsp;&bull;&nbsp;
  <a href="#quick-start"><b>Quick Start</b></a> &nbsp;&bull;&nbsp;
  <a href="#how-reqforge-works"><b>Architecture</b></a> &nbsp;&bull;&nbsp;
  <a href="https://discord.gg/PYfeSaDGPJ"><b>Discord</b></a>
</p>

<br />

<img src="assets/showcase.gif" alt="ReqForge Live Showcase" width="880" />

</div>

---

## Why ReqForge?

Inspecting an app in Chrome DevTools (**Copy as cURL** or **Save all as HAR**) to build a fast request-based tool leaves you with 25 noisy browser headers, tracking cookies (`_ga`, `_fbp`), hardcoded auth tokens, and tedious boilerplate.

**ReqForge** filters the noise, optionally replays and probes live endpoints with `httpx` or `curl_cffi` to isolate the **exact minimal headers, cookies, and query params** required for a matching response, infers parameterized routes and schemas, and generates clean async SDKs + smoke tests — **100% locally with zero LLMs.**

<table>
  <tr>
    <td width="50%">
      <h3>🧹 Noise &amp; Tracker Filtering</h3>
      <ul>
        <li>Drops static assets (<code>.css</code>, <code>.js</code>, fonts, images, media), preflight <code>OPTIONS</code>, HTML navigations, and telemetry beacons</li>
        <li>Strips browser bloat (<code>sec-ch-ua*</code>, <code>sec-fetch-*</code>, <code>priority</code>, <code>dnt</code>) &amp; tracking cookies (<code>_ga</code>, <code>_fbp</code>, <code>_hj*</code>)</li>
      </ul>
    </td>
    <td width="50%">
      <h3>🔬 Live Delta-Debugging Probe</h3>
      <ul>
        <li>Binary-splits headers, cookies, and query params against live targets in logarithmic HTTP probes</li>
        <li>Catches soft-200 error payloads &amp; HTML redirects, and auto-detects when <code>curl_cffi</code> TLS impersonation is required</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>🧬 Parameter &amp; Schema Inference</h3>
      <ul>
        <li>Clusters paths into parameterized routes (<code>/users/{user_id}/orders</code>), splits GraphQL by <code>operationName</code> &amp; JSON-RPC by <code>method</code></li>
        <li>Merges multi-sample JSON payloads into Pydantic v2 models and TypeScript interfaces with nullable &amp; optional tracking</li>
      </ul>
    </td>
    <td width="50%">
      <h3>⚡ Multi-Target SDK + Web Studio</h3>
      <ul>
        <li>Generates pooled async Python (<code>httpx</code> / <code>curl_cffi</code>), typed TypeScript, and ESM JavaScript <code>fetch</code> clients with retries &amp; <code>from_env()</code></li>
        <li>Ships <code>reqforge ui</code> local studio + <code>reqforge export-web</code> standalone Pyodide browser bundle</li>
      </ul>
    </td>
  </tr>
</table>

---

## Before vs. After

<table>
  <tr>
    <th>Chrome DevTools Copy as cURL (Before)</th>
    <th>Generated Async Python Client (After)</th>
  </tr>
  <tr>
    <td width="50%">

```bash
curl 'https://api.shop.example/v2/users/42/orders?limit=20&utm_source=devtools' \
  -H 'Authorization: Bearer eyJhbGci...' \
  -H 'Accept-Language: en-US,en;q=0.9' \
  -H 'sec-ch-ua: "Chromium";v="124"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-origin' \
  -H 'User-Agent: Mozilla/5.0 ...' \
  -H 'Priority: u=1, i' \
  -b '_ga=GA1.2.9; _fbp=fb.1; sid=sess_99'
```

</td>
    <td width="50%">

```python
async with OrdersClient.from_env() as client:
    orders = await client.list_user_orders(
        user_id=42,
        limit=20,
    )
    print(orders.next_cursor, len(orders.orders))
```

</td>
  </tr>
</table>

---

## ReqForge vs. Existing Tools

| Capability | Raw DevTools / Scripts | `curlconverter` | `har-to-openapi` | **ReqForge** |
| :--- | :---: | :---: | :---: | :---: |
| Multi-cURL (Bash, CMD, PowerShell) + HAR 1.2 | ❌ | ⚠️ 1-to-1 | ❌ HAR only | ✅ |
| Static asset & tracker request filtering | ❌ | ❌ | ⚠️ Partial | ✅ |
| Live `ddmin` header, cookie & query probing | ❌ | ❌ | ❌ | ✅ |
| Browser TLS detection (`httpx` vs `curl_cffi`) | ❌ | ❌ | ❌ | ✅ |
| Auth detection + secret extraction to env vars | ❌ | ❌ | ❌ | ✅ |
| Multi-request route clustering & schema merging | ❌ | ❌ | ⚠️ OpenAPI only | ✅ |
| Async Python (`httpx`/`curl_cffi`) + TS + JS SDKs + Smoke Test | ❌ | ❌ Snippets only | ❌ Needs 2nd tool | ✅ |
| Local Web Studio + Zero-Backend Browser Mode | ❌ | ⚠️ Web snippets | ❌ | ✅ |

---

## Installation

### Option 1 — Install From Source (Recommended)

```bash
git clone https://github.com/tejzv/reqforge.git
cd reqforge
pip install -e ".[all]"
```

Core parsing, filtering, schema inference, SDK generation, and the stdlib Web Studio run with **zero required dependencies** (`pip install -e .`). Adding `.[all]` enables live probing with `httpx`, TLS browser impersonation via `curl_cffi`, and Pydantic model validation.

### Option 2 — Prebuilt Release Artifacts ([Latest Release](https://github.com/tejzv/reqforge/releases/latest))

Download directly from **[Releases → Latest](https://github.com/tejzv/reqforge/releases/latest)**:

| Artifact | Usage |
| :--- | :--- |
| `reqforge-0.1.0-py3-none-any.whl` | `pip install reqforge-0.1.0-py3-none-any.whl` |
| `reqforge-v0.1.0.pyz` | Zero-install portable executable: `python reqforge-v0.1.0.pyz ui` |
| `reqforge-web-v0.1.0.zip` | Ready-to-host static browser studio (Pyodide powered, works on GitHub Pages / `tejv.in`) |

---

## Quick Start

### Analyze a HAR Capture or cURL Dump

```bash
reqforge analyze capture.har
reqforge analyze capture.har --json
```

### Live Header, Cookie & Query Minimization Probe

```bash
reqforge probe request.curl
reqforge probe request.curl --probe-query --probe-transport auto
reqforge probe request.curl --out cleaned_report.json
```

### Generate Clean Typed SDKs + Smoke Tests

```bash
reqforge generate request.curl --target python --probe --out ./sdk
reqforge generate request.curl --target python --transport curl_cffi --single-file --out ./sdk
reqforge generate capture.har --target typescript --out ./ts_sdk
reqforge generate capture.har --target javascript --out ./js_sdk
reqforge generate capture.har --target all --out ./generated_sdks
```

Pipe directly from clipboard or stdin:

```bash
cat devtools.curl | reqforge generate - --stdout --single-file --target python
```

### Launch ReqForge Studio (Local Web UI)

```bash
reqforge ui
```

Opens the interactive browser workbench with CORS-free live probing, visual header/cookie diff chips, schema field previews, and 1-click **Download ZIP** export.

### Export Static Browser Bundle (`tejv.in` / Static Hosting)

```bash
reqforge export-web --out ./dist_web
```

---

## How ReqForge Works

| Stage | Module | What Happens |
| :--- | :--- | :--- |
| **1. Ingest** | `reqforge.ingest` | Parses HAR 1.2 captures and Bash/CMD/PowerShell cURL batches into normalized `CapturedRequest` models |
| **2. Filter & Classify** | `reqforge.filter` | Drops static/tracker/preflight noise, strips browser-bloat headers & tracking cookies, and flags auth tokens |
| **3. Live Probe** | `reqforge.probe` | Runs `ddmin` delta debugging over headers, cookies, and query params with `httpx` / `curl_cffi` TLS auto-selection |
| **4. Infer** | `reqforge.infer` | Parameterizes path segments, separates GraphQL & JSON-RPC calls, and merges JSON samples into typed AST schemas |
| **5. Export** | `reqforge.codegen` | Emits pooled async Python, typed TypeScript, ESM JavaScript SDKs, runnable smoke tests, and studio bundles |

---

<details>
<summary><b>🛠️ CLI Reference &amp; Studio Previews</b></summary>

<br />

| Command | Purpose | Key Flags |
| :--- | :--- | :--- |
| `reqforge analyze <input>` | Inspect endpoints, auth findings, stripped noise & inferred schemas | `--json`, `--include-host`, `--exclude-host`, `--only-json` |
| `reqforge probe <input>` | Replay requests via `ddmin` to isolate minimum required headers & cookies | `--probe-transport`, `--probe-query`, `--no-probe-cookies`, `--max-probes`, `--out` |
| `reqforge generate <input>` | Emit Python (`httpx`/`curl_cffi`), TypeScript, or JavaScript SDK + smoke test | `--target`, `--transport`, `--probe`, `--single-file`, `--name`, `--base-url`, `--out`, `--stdout` |
| `reqforge ui` (`serve`) | Launch local interactive Web Studio server | `--host`, `--port`, `--no-open` |
| `reqforge export-web` | Build static Pyodide-powered web bundle | `--out` |

<p align="center">
  <img src="assets/studio-preview.png" alt="ReqForge Studio Code View" width="840" />
  <br /><br />
  <img src="assets/studio-audit.png" alt="ReqForge Studio Headers & Auth Audit" width="840" />
</p>

</details>

---

## Running Tests & Contributing

```bash
python -m unittest discover -s tests -v
```

Contributions, bug reports, and feature ideas are welcome — see [CHANGELOG.md](CHANGELOG.md) and open an issue or pull request.

---

## Community & Support

- **Discord Server**: https://discord.gg/PYfeSaDGPJ
- **Contact**: [@sqnq](https://discord.com/users/987979957907116052)
- **Website**: [tejv.in](https://tejv.in)

### Buy Me A Coffee ☕

**Litecoin (LTC)** — click copy on the right:

```text
LbDG5BE3foVsfK6JRsSc7UtBVX4DqYYKXN
```

<div align="center">

**Made With ♥️ By [Tej](https://github.com/tejzv)**

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0d1117,50:161b22,100:1f6feb&height=100&section=footer" width="100%" />

</div>
