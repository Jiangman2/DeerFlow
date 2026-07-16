"""CLI entry point for svg_to_pptx."""

from __future__ import annotations

import sys
import shutil
import argparse
import tempfile
import io
from importlib import import_module
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from .pptx_dimensions import (
    CANVAS_FORMATS,
    get_pixel_dimensions,
    get_project_info,
    get_viewbox_dimensions,
)
from .pptx_discovery import find_svg_files, find_notes_files
from .pptx_builder import create_pptx_with_native_svg
from .pptx_narration import find_narration_files
from .pptx_slide_xml import TRANSITIONS

try:
    from pptx_animations import ANIMATIONS as _ANIMATIONS
except ImportError:
    _ANIMATIONS = {}


def _discover_user_outputs_dir(project_path: Path) -> Path | None:
    """Best-effort discover `/mnt/user-data/outputs` from a project path.

    Expected project path shape in sandbox:
      /mnt/user-data/workspace/<...>/<project_name>
    """
    try:
        resolved = project_path.resolve()
    except Exception:
        resolved = project_path

    # Walk upwards and find the `workspace` anchor directory.
    for node in [resolved, *resolved.parents]:
        if node.name == "workspace":
            return node.parent / "outputs"
    return None


def _sync_export_artifacts_to_outputs(
    *,
    project_path: Path,
    pptx_path: Path,
    preview_pdf_path: Path | None,
    verbose: bool,
) -> tuple[Path | None, Path | None]:
    """Copy exported artifacts to user outputs for direct present/preview."""
    outputs_dir = _discover_user_outputs_dir(project_path)
    if outputs_dir is None:
        if verbose:
            print("  [info] outputs sync skipped (workspace anchor not found)")
        return None, None

    try:
        outputs_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        if verbose:
            print(f"  [warn] outputs sync skipped (cannot create outputs dir): {exc}")
        return None, None

    synced_pptx: Path | None = None
    synced_preview: Path | None = None

    if pptx_path.exists():
        pptx_target = outputs_dir / pptx_path.name
        try:
            if pptx_path.resolve() != pptx_target.resolve():
                shutil.copy2(pptx_path, pptx_target)
            synced_pptx = pptx_target
        except Exception as exc:
            if verbose:
                print(f"  [warn] outputs sync failed for pptx: {exc}")

    if preview_pdf_path is not None and preview_pdf_path.exists():
        preview_target = outputs_dir / preview_pdf_path.name
        try:
            if preview_pdf_path.resolve() != preview_target.resolve():
                shutil.copy2(preview_pdf_path, preview_target)
            synced_preview = preview_target
        except Exception as exc:
            if verbose:
                print(f"  [warn] outputs sync failed for preview pdf: {exc}")

    if verbose and (synced_pptx or synced_preview):
        print(f"  Synced outputs directory: {outputs_dir}")
        if synced_pptx:
            print(f"    - PPTX: {synced_pptx}")
        if synced_preview:
            print(f"    - Preview PDF: {synced_preview}")

    return synced_pptx, synced_preview


def _export_preview_pdf_from_svg(
    svg_files: list[Path],
    output_pdf: Path,
    canvas_format: str | None,
    verbose: bool,
) -> bool:
    """Generate a PDF preview deck from slide SVG files."""
    try:
        canvas_module = import_module("reportlab.pdfgen.canvas")
        utils_module = import_module("reportlab.lib.utils")
        cairosvg_module = import_module("cairosvg")
    except Exception as exc:
        if verbose:
            print(f"  [warn] preview PDF skipped (missing dependency): {exc}")
        return False

    canvas_cls = cast(Any, getattr(canvas_module, "Canvas", None))
    image_reader_cls = cast(Any, getattr(utils_module, "ImageReader", None))
    svg_to_png = cast(Any, getattr(cairosvg_module, "svg2png", None))
    if canvas_cls is None or image_reader_cls is None or svg_to_png is None:
        if verbose:
            print("  [warn] preview PDF skipped (reportlab/cairosvg symbols unavailable)")
        return False

    if not svg_files:
        if verbose:
            print("  [warn] preview PDF skipped (no SVG files)")
        return False

    first_viewbox = get_viewbox_dimensions(svg_files[0])
    if first_viewbox:
        width_px, height_px = first_viewbox
    else:
        width_px, height_px = get_pixel_dimensions(canvas_format or "ppt169")

    points_per_pixel = 72.0 / 96.0
    page_width = width_px * points_per_pixel
    page_height = height_px * points_per_pixel

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf_canvas = canvas_cls(str(output_pdf), pagesize=(page_width, page_height))

    # svglib/reportlab is strict about exact font names and may not fallback
    # like browsers do. Map common CJK font aliases to fonts we install in sandbox.
    font_alias_map = {
        "Microsoft YaHei": "Noto Sans CJK SC",
        "微软雅黑": "Noto Sans CJK SC",
        "PingFang SC": "Noto Sans CJK SC",
        "SimSun": "Noto Serif CJK SC",
        "宋体": "Noto Serif CJK SC",
        "SimHei": "Noto Sans CJK SC",
        "黑体": "Noto Sans CJK SC",
        "KaiTi": "Noto Serif CJK SC",
        "楷体": "Noto Serif CJK SC",
    }

    def _maybe_rewrite_svg_font_aliases(svg_path: Path) -> str | None:
        try:
            raw = svg_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

        rewritten = raw
        for source_name, target_name in font_alias_map.items():
            rewritten = rewritten.replace(source_name, target_name)

        if rewritten == raw:
            return None
        return rewritten

    rendered_pages = 0
    for svg_path in svg_files:
        rewritten_svg = _maybe_rewrite_svg_font_aliases(svg_path)

        # Route B: rasterize each SVG to PNG via CairoSVG, then compose PDF pages.
        page_png: bytes | None = None
        try:
            if rewritten_svg is None:
                page_png = cast(
                    bytes,
                    svg_to_png(
                        url=str(svg_path),
                        output_width=int(width_px),
                        output_height=int(height_px),
                        unsafe=True,
                    ),
                )
            else:
                # Keep original SVG path as base URL so relative assets (../images/...) still resolve.
                page_png = cast(
                    bytes,
                    svg_to_png(
                        bytestring=rewritten_svg.encode("utf-8"),
                        url=str(svg_path),
                        output_width=int(width_px),
                        output_height=int(height_px),
                        unsafe=True,
                    ),
                )
        except Exception as exc:
            if verbose:
                print(f"  [warn] cairosvg render failed for {svg_path.name}: {exc}")
            continue

        try:
            image_reader = image_reader_cls(io.BytesIO(page_png))
            img_width_px = float(getattr(image_reader, "getSize", lambda: (width_px, height_px))()[0] or width_px)
            img_height_px = float(getattr(image_reader, "getSize", lambda: (width_px, height_px))()[1] or height_px)
            draw_width = img_width_px * points_per_pixel
            draw_height = img_height_px * points_per_pixel
            scale = min(page_width / draw_width, page_height / draw_height)
            offset_x = (page_width - draw_width * scale) / 2.0
            offset_y = (page_height - draw_height * scale) / 2.0
            pdf_canvas.drawImage(
                image_reader,
                offset_x,
                offset_y,
                width=draw_width * scale,
                height=draw_height * scale,
                mask="auto",
            )
        except Exception as exc:
            if verbose:
                print(f"  [warn] preview image compose failed for {svg_path.name}: {exc}")
            continue

        pdf_canvas.showPage()
        rendered_pages += 1

    pdf_canvas.save()
    if rendered_pages == 0:
        try:
            output_pdf.unlink(missing_ok=True)
        except Exception:
            pass
        if verbose:
            print("  [warn] preview PDF skipped (no renderable SVG pages)")
        return False

    if verbose:
        print(f"  Preview PDF: {output_pdf}")
    return True


def main() -> None:
    """CLI entry point for the SVG to PPTX conversion tool."""
    transition_choices = (
        ['none'] + (list(TRANSITIONS.keys()) if TRANSITIONS
                    else ['fade', 'push', 'wipe', 'split', 'strips', 'cover', 'random'])
    )

    animation_choices = (
        ['none'] + (list(_ANIMATIONS.keys()) if _ANIMATIONS
                    else ['fade', 'fly', 'zoom', 'appear'])
        + ['mixed', 'random']
    )

    parser = argparse.ArgumentParser(
        description='PPT Master - SVG to PPTX Tool (Office Compatibility Mode)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'''
Examples:
    %(prog)s examples/ppt169_demo -s final    # Default: main pptx -> exports/, SVG snapshot + svg_output -> backup/<ts>/
    %(prog)s examples/ppt169_demo --only native   # Only native shapes version
    %(prog)s examples/ppt169_demo --only legacy   # Only SVG image version
    %(prog)s examples/ppt169_demo -o out.pptx     # Explicit path (SVG ref -> out_svg.pptx)

    # Disable transition / change transition effect
    %(prog)s examples/ppt169_demo -t none
    %(prog)s examples/ppt169_demo -t push --transition-duration 1.0

SVG source directory (-s):
    output   - svg_output (original version)
    final    - svg_final (post-processed, recommended)
    <any>    - Specify a subdirectory name directly

Transition effects (-t/--transition):
    {', '.join(transition_choices)}

Per-element entrance animation (-a/--animation, native shapes mode):
    {', '.join(animation_choices)}
    Notes: applied to top-level <g id="..."> SVG groups in z-order. Default is
           "mixed" (auto-vary effects per group). Start mode set by
           --animation-trigger, matching PowerPoint's Start dropdown:
             on-click              one presenter click per group
             with-previous         all groups start together on slide entry
             after-previous (default)  cascade on slide entry;
                                       gap = --animation-stagger seconds
           mixed uses a curated visible-effect sequence across the deck; random samples
           from the same visible-effect pool. Use "-a none" to disable.

Compatibility mode (enabled by default):
    - Automatically generates PNG fallback images, SVG embedded as extension
    - Compatible with all Office versions (including Office LTSC 2021)
    - Newer Office still displays SVG (editable), older versions display PNG
    - Requires svglib: pip install svglib reportlab
    - Use --no-compat to disable (only Office 2019+ supported)

Speaker notes (enabled by default):
    - Automatically reads Markdown notes files from the notes/ directory
    - Supports two naming conventions:
      1. Match by filename (recommended): 01_cover.md corresponds to 01_cover.svg
      2. Match by index: slide01.md corresponds to the 1st SVG (backward compatible)
    - Use --no-notes to disable

Recorded narration:
    %(prog)s examples/ppt169_demo -s final --recorded-narration audio
    - Keeps speaker notes when enabled
    - Embeds per-slide audio matched by SVG filename / slide number
    - Sets slide auto-advance from audio duration so video export can use
      "recorded timings and narrations"
''',
    )

    parser.add_argument('project_path', type=str, help='Project directory path')
    parser.add_argument('-o', '--output', type=str, default=None, help='Output file path')
    parser.add_argument('-s', '--source', type=str, default='output',
                        help='SVG source: output/final or any subdirectory name (recommended: final)')
    parser.add_argument('-f', '--format', type=str,
                        choices=list(CANVAS_FORMATS.keys()), default=None,
                        help='Specify canvas format')
    parser.add_argument('-q', '--quiet', action='store_true', help='Quiet mode')

    parser.add_argument('--no-compat', action='store_true',
                        help='Disable Office compatibility mode (pure SVG only, requires Office 2019+)')

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--only', type=str, choices=['native', 'legacy'], default=None,
                            help='Only generate one version: native (editable shapes) or legacy (SVG image)')
    mode_group.add_argument('--native', action='store_true', default=False,
                            help='(Deprecated, now default) Convert SVG to native DrawingML shapes')

    parser.add_argument('-t', '--transition', type=str, choices=transition_choices, default='fade',
                        help='Page transition effect (default: fade, use "none" to disable)')
    parser.add_argument('--transition-duration', type=float, default=0.4,
                        help='Transition duration in seconds (default: 0.4)')
    parser.add_argument('--auto-advance', type=float, default=None,
                        help='Auto-advance interval in seconds (default: manual advance)')

    parser.add_argument('-a', '--animation', type=str, choices=animation_choices,
                        default='mixed',
                        help='Per-element entrance animation (native shapes mode '
                             'only). Pick a single effect, "mixed" (auto-vary per '
                             'element, default), "random", or "none" to disable.')
    parser.add_argument('--animation-duration', type=float, default=0.4,
                        help='Per-element entrance duration in seconds (default: 0.4)')
    parser.add_argument('--animation-trigger', type=str,
                        choices=['on-click', 'with-previous', 'after-previous'],
                        default='after-previous',
                        help='Per-element Start mode (matches PowerPoint Start dropdown): '
                             '"on-click" (one click per element), '
                             '"with-previous" (all start together on slide entry), '
                             '"after-previous" (default, cascade after the previous element).')
    parser.add_argument('--animation-stagger', type=float, default=0.5,
                        help='Delay between elements in --animation-trigger=after-previous '
                             '(seconds, default 0.5). Ignored in other modes.')

    parser.add_argument('--no-notes', action='store_true',
                        help='Disable speaker notes embedding (enabled by default)')
    parser.add_argument('--narration-audio-dir', type=str, default=None,
                        help='Embed per-slide narration audio from this directory')
    parser.add_argument('--use-narration-timings', action='store_true',
                        help='Set slide auto-advance timings from narration audio durations')
    parser.add_argument('--recorded-narration', type=str, default=None,
                        help='Shortcut: embed narration audio and use its durations as recorded timings')
    parser.add_argument('--narration-padding', type=float, default=0.5,
                        help='Seconds to add after each narration before auto-advance (default: 0.5)')

    args = parser.parse_args()

    project_path = Path(args.project_path)
    if not project_path.exists():
        print(f"Error: Path does not exist: {project_path}")
        sys.exit(1)

    try:
        project_info = get_project_info(str(project_path))
        project_name = project_info.get('name', project_path.name)
        detected_format = project_info.get('format')
    except Exception:
        project_name = project_path.name
        detected_format = None

    canvas_format = args.format
    if canvas_format is None and detected_format and detected_format != 'unknown':
        canvas_format = detected_format

    svg_files, source_dir_name = find_svg_files(project_path, args.source)

    if not svg_files:
        print("Error: No SVG files found")
        sys.exit(1)

    # Determine which versions to generate
    only_mode = args.only
    gen_native = only_mode in (None, 'native')
    gen_legacy = only_mode in (None, 'legacy')

    # --native flag (deprecated) maps to --only native
    if args.native and only_mode is None:
        gen_legacy = False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_dir: Path | None = None
    if args.output:
        output_base = Path(args.output)
        native_path = output_base
        stem = output_base.stem
        legacy_path = output_base.parent / f"{stem}_svg{output_base.suffix}"
    else:
        exports_dir = project_path / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        native_path = exports_dir / f"{project_name}_{timestamp}.pptx"

        backup_dir = project_path / "backup" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        legacy_path = backup_dir / f"{project_name}_svg.pptx"

    native_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)

    verbose = not args.quiet

    enable_notes = not args.no_notes
    notes: dict[str, str] = {}
    if enable_notes:
        notes = find_notes_files(project_path, svg_files)

    narration_audio: dict[str, Path] = {}
    narration_audio_dir_arg = args.recorded_narration or args.narration_audio_dir
    use_narration_timings = args.use_narration_timings or bool(args.recorded_narration)
    if narration_audio_dir_arg:
        narration_audio_dir = Path(narration_audio_dir_arg)
        if not narration_audio_dir.is_absolute():
            narration_audio_dir = project_path / narration_audio_dir
        narration_audio = find_narration_files(narration_audio_dir, svg_files)
        if verbose:
            print(f"  Narration audio directory: {narration_audio_dir}")
            print(f"  Narration audio matched: {len(narration_audio)}/{len(svg_files)} slide(s)")

    transition = args.transition if args.transition != 'none' else None
    animation = args.animation if args.animation != 'none' else None

    success = True

    # --- Native shapes version (primary) ---
    if gen_native:
        if verbose:
            print("PPT Master - SVG to PPTX Tool")
            print("=" * 50)
            print(f"  Project path: {project_path}")
            print(f"  SVG directory: {source_dir_name}")
            print(f"  Output file: {native_path}")
            print()

        ok = create_pptx_with_native_svg(
            svg_files=svg_files,
            output_path=native_path,
            canvas_format=canvas_format,
            verbose=verbose,
            transition=transition,
            transition_duration=args.transition_duration,
            auto_advance=args.auto_advance,
            use_compat_mode=not args.no_compat,
            notes=notes,
            enable_notes=enable_notes,
            use_native_shapes=True,
            animation=animation,
            animation_duration=args.animation_duration,
            animation_stagger=args.animation_stagger,
            animation_trigger=args.animation_trigger,
            narration_audio=narration_audio,
            use_narration_timings=use_narration_timings,
            narration_padding=args.narration_padding,
        )
        native_preview_path = native_path.with_suffix(".preview.pdf")
        if ok:
            _export_preview_pdf_from_svg(
                svg_files=svg_files,
                output_pdf=native_preview_path,
                canvas_format=canvas_format,
                verbose=verbose,
            )
            _sync_export_artifacts_to_outputs(
                project_path=project_path,
                pptx_path=native_path,
                preview_pdf_path=native_preview_path,
                verbose=verbose,
            )
        success = success and ok

    # --- SVG image reference version ---
    if gen_legacy:
        if verbose:
            if gen_native:
                print()
                print("-" * 50)
            print("PPT Master - SVG to PPTX Tool (SVG Reference)")
            print("=" * 50)
            print(f"  Project path: {project_path}")
            print(f"  SVG directory: {source_dir_name}")
            print(f"  Output file: {legacy_path}")
            print()

        ok = create_pptx_with_native_svg(
            svg_files=svg_files,
            output_path=legacy_path,
            canvas_format=canvas_format,
            verbose=verbose,
            transition=transition,
            transition_duration=args.transition_duration,
            auto_advance=args.auto_advance,
            use_compat_mode=not args.no_compat,
            notes=notes,
            enable_notes=enable_notes,
            use_native_shapes=False,
            animation=animation,
            animation_duration=args.animation_duration,
            animation_stagger=args.animation_stagger,
            animation_trigger=args.animation_trigger,
            narration_audio=narration_audio,
            use_narration_timings=use_narration_timings,
            narration_padding=args.narration_padding,
        )
        if ok:
            _export_preview_pdf_from_svg(
                svg_files=svg_files,
                output_pdf=legacy_path.with_suffix(".preview.pdf"),
                canvas_format=canvas_format,
                verbose=verbose,
            )
        success = success and ok

        if ok and backup_dir is not None:
            svg_output_src = project_path / "svg_output"
            if svg_output_src.is_dir():
                svg_output_dst = backup_dir / "svg_output"
                try:
                    shutil.copytree(svg_output_src, svg_output_dst)
                    if verbose:
                        print(f"  svg_output backup: {svg_output_dst}")
                except Exception as exc:
                    if verbose:
                        print(f"  [warn] svg_output backup skipped: {exc}")
            elif verbose:
                print(f"  [info] svg_output/ not found, backup skipped")

    sys.exit(0 if success else 1)
