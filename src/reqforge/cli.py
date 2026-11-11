from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from reqforge.codegen import format_terminal_report, result_to_json
from reqforge.errors import ReqForgeError
from reqforge.filter import FilterOptions
from reqforge.pipeline import ForgeOptions, run_pipeline, write_generated_files
from reqforge.probe import ProbeConfig


def _read_input(source: str | None) -> tuple[str, str]:
    if source is None or source == "-":
        if sys.stdin.isatty():
            raise ReqForgeError("Provide an input file path or pipe HAR/cURL via stdin")
        return sys.stdin.read(), "stdin"
    p = Path(source)
    if not p.exists():
        raise ReqForgeError(f"File not found: {p}")
    return p.read_text(encoding="utf-8", errors="replace"), p.name


def _add_common_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--keep-static", action="store_true")
    parser.add_argument("--keep-trackers", action="store_true")
    parser.add_argument("--keep-options", action="store_true")
    parser.add_argument("--keep-html", action="store_true")
    parser.add_argument("--only-json", action="store_true")
    parser.add_argument("--include-host", action="append", default=[])
    parser.add_argument("--exclude-host", action="append", default=[])
    parser.add_argument("--keep-user-agent", action="store_true")
    parser.add_argument("--keep-origin-referer", action="store_true")


def _add_probe_args(parser: argparse.ArgumentParser, *, probe_flag: bool = True) -> None:
    if probe_flag:
        parser.add_argument("--probe", action="store_true")
    parser.add_argument("--probe-transport", choices=["auto", "httpx", "curl_cffi"], default="auto")
    parser.add_argument("--probe-timeout", type=float, default=10.0)
    parser.add_argument("--probe-delay", type=float, default=0.0)
    parser.add_argument("--max-probes", type=int, default=80)
    parser.add_argument("--no-probe-cookies", action="store_true")
    parser.add_argument("--impersonate", default="chrome")
    parser.add_argument("--insecure", action="store_true")


def _build_options_from_args(args: argparse.Namespace, *, force_probe: bool = False) -> ForgeOptions:
    filter_opts = FilterOptions(
        drop_static=not getattr(args, "keep_static", False),
        drop_trackers=not getattr(args, "keep_trackers", False),
        drop_options=not getattr(args, "keep_options", False),
        drop_html_documents=not getattr(args, "keep_html", False),
        only_json=getattr(args, "only_json", False),
        include_hosts=list(getattr(args, "include_host", [])),
        exclude_hosts=list(getattr(args, "exclude_host", [])),
    )
    probe_cfg = ProbeConfig(
        transport=getattr(args, "probe_transport", "auto"),
        timeout_seconds=getattr(args, "probe_timeout", 10.0),
        delay_seconds=getattr(args, "probe_delay", 0.0),
        max_probes=getattr(args, "max_probes", 80),
        minimize_cookies=not getattr(args, "no_probe_cookies", False),
        impersonate=getattr(args, "impersonate", "chrome"),
        verify_ssl=not getattr(args, "insecure", False),
    )
    return ForgeOptions(
        client_name=getattr(args, "name", "") or "",
        base_url=getattr(args, "base_url", "") or "",
        target=getattr(args, "target", "python"),
        transport=getattr(args, "transport", "auto"),
        single_file=getattr(args, "single_file", False),
        probe=force_probe or getattr(args, "probe", False),
        probe_config=probe_cfg,
        filter_options=filter_opts,
        keep_user_agent=getattr(args, "keep_user_agent", False),
        keep_origin_referer=getattr(args, "keep_origin_referer", False),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reqforge",
        description="Convert HAR captures and cURL commands into clean typed Python, TypeScript, and JavaScript SDKs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate SDK files from a HAR file or cURL command(s)")
    gen.add_argument("input", nargs="?", default="-", help="Path to .har/.txt file or '-' for stdin")
    gen.add_argument("--target", choices=["python", "typescript", "javascript", "all"], default="python")
    gen.add_argument("--transport", choices=["auto", "httpx", "curl_cffi"], default="auto")
    gen.add_argument("--out", "-o", default="reqforge_out", help="Output directory")
    gen.add_argument("--stdout", action="store_true", help="Print generated files to stdout instead of writing disk")
    gen.add_argument("--single-file", action="store_true", help="Emit a single self-contained Python client module")
    gen.add_argument("--name", default="", help="Client class/package name")
    gen.add_argument("--base-url", default="", help="Override base URL")
    _add_common_filter_args(gen)
    _add_probe_args(gen, probe_flag=True)

    analyze = sub.add_parser("analyze", help="Inspect endpoints, stripped headers, auth findings, and inferred schemas")
    analyze.add_argument("input", nargs="?", default="-")
    analyze.add_argument("--json", action="store_true")
    analyze.add_argument("--name", default="")
    analyze.add_argument("--base-url", default="")
    _add_common_filter_args(analyze)

    probe = sub.add_parser("probe", help="Run live header/cookie minimization probing against target endpoints")
    probe.add_argument("input", nargs="?", default="-")
    probe.add_argument("--json", action="store_true")
    probe.add_argument("--name", default="")
    probe.add_argument("--base-url", default="")
    _add_common_filter_args(probe)
    _add_probe_args(probe, probe_flag=False)

    ui = sub.add_parser("ui", aliases=["serve"], help="Start local ReqForge interactive web workbench")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-open", action="store_true")

    exp = sub.add_parser("export-web", help="Export static browser-ready web app bundle (for tejv.in or static hosting)")
    exp.add_argument("--out", "-o", default="reqforge_web")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "generate":
            raw, src = _read_input(args.input)
            opts = _build_options_from_args(args)
            result, generated = run_pipeline(raw, source_name=src, options=opts)
            if args.stdout:
                for target_name, files in generated.items():
                    for gf in files:
                        print(f"=== [{target_name}] {gf.path} ===")
                        print(gf.content)
            else:
                written = write_generated_files(args.out, generated)
                print(format_terminal_report(result))
                print(f"\nGenerated {len(written)} file(s) in {Path(args.out).resolve()}")
            return 0

        if args.command == "analyze":
            raw, src = _read_input(args.input)
            opts = _build_options_from_args(args)
            result, _ = run_pipeline(raw, source_name=src, options=opts)
            print(result_to_json(result) if args.json else format_terminal_report(result))
            return 0

        if args.command == "probe":
            raw, src = _read_input(args.input)
            opts = _build_options_from_args(args, force_probe=True)
            result, _ = run_pipeline(raw, source_name=src, options=opts)
            print(result_to_json(result) if args.json else format_terminal_report(result))
            return 0

        if args.command in {"ui", "serve"}:
            from reqforge.server import run_server

            url = f"http://{args.host}:{args.port}"
            print(f"ReqForge Studio running at {url}")
            if not args.no_open:
                webbrowser.open(url)
            run_server(host=args.host, port=args.port)
            return 0

        if args.command == "export-web":
            from reqforge.web_export import export_static_web_bundle

            written = export_static_web_bundle(args.out)
            print(f"Exported {len(written)} static web bundle file(s) to {Path(args.out).resolve()}")
            return 0

    except ReqForgeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
