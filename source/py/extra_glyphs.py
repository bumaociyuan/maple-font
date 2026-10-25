from pathlib import Path
from typing import Any

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

EXTRA_GLYPHS_DIR = Path(__file__).resolve().parents[1] / "extra-glyphs"


def add_extra_glyphs(font: TTFont, glyph_dir: Path = EXTRA_GLYPHS_DIR) -> list[int]:
    """Fill missing codepoints from bundled TTF subsets, using one terminal cell."""
    if "glyf" not in font or "fvar" in font:
        raise ValueError("Extra glyphs require a static TrueType destination")

    sources = sorted(glyph_dir.glob("*.ttf"))
    if not sources:
        raise FileNotFoundError(f"No extra glyph subsets in {glyph_dir}")

    cmap: Any = font["cmap"]
    glyf: Any = font["glyf"]
    hmtx: Any = font["hmtx"]
    hhea: Any = font["hhea"]
    maxp: Any = font["maxp"]
    unicode_tables = [table for table in cmap.tables if table.isUnicode()]
    existing = {code for table in unicode_tables for code in table.cmap}
    target_cmap = font.getBestCmap()
    if not target_cmap or 0x20 not in target_cmap:
        raise ValueError("Destination has no Unicode space for measuring cell width")
    width = hmtx[target_cmap[0x20]][0]
    ascent, descent = hhea.ascent, hhea.descent
    if width <= 0 or ascent <= descent:
        raise ValueError("Invalid destination cell metrics")

    added = []
    for source_path in sources:
        with TTFont(source_path) as source:
            source_cmap = source.getBestCmap()
            if "glyf" not in source or not source_cmap:
                raise ValueError(f"Not a Unicode TrueType subset: {source_path}")
            glyph_set = source.getGlyphSet()
            source_hhea: Any = source["hhea"]
            source_hmtx: Any = source["hmtx"]
            source_ascent = source_hhea.ascent
            source_descent = source_hhea.descent
            for code, source_name in sorted(source_cmap.items()):
                if code in existing:
                    continue
                advance = source_hmtx[source_name][0]
                if advance <= 0 or source_ascent <= source_descent:
                    raise ValueError(f"Invalid source cell metrics for U+{code:04X}")
                glyph_name = f"extra{code:06X}"
                if glyph_name in font.getGlyphOrder():
                    raise ValueError(f"Glyph name already exists: {glyph_name}")

                scale = min(
                    width / advance,
                    (ascent - descent) / (source_ascent - source_descent),
                )
                dx = (width - advance * scale) / 2
                dy = (ascent + descent - (source_ascent + source_descent) * scale) / 2
                # Decompose references so no unrelated source glyphs or hints are copied.
                outline = DecomposingRecordingPen(glyph_set)
                glyph_set[source_name].draw(outline)
                pen = TTGlyphPen(None)
                outline.replay(TransformPen(pen, (scale, 0, 0, scale, dx, dy)))
                glyph = pen.glyph()
                glyph.recalcBounds(glyf)

                if code > 0xFFFF and not any(
                    table.format == 12 for table in unicode_tables
                ):
                    full_cmap: Any = CmapSubtable.newSubtable(12)
                    full_cmap.platformID, full_cmap.platEncID, full_cmap.language = 3, 10, 0
                    full_cmap.cmap = dict(target_cmap)
                    cmap.tables.append(full_cmap)
                    unicode_tables.append(full_cmap)
                writable_tables = [
                    table
                    for table in unicode_tables
                    if table.format == 12 or (table.format == 4 and code <= 0xFFFF)
                ]
                if not writable_tables:
                    raise ValueError(f"No supported Unicode cmap for U+{code:04X}")

                order = list(font.getGlyphOrder())
                glyf[glyph_name] = glyph
                hmtx[glyph_name] = (width, getattr(glyph, "xMin", 0))
                font.setGlyphOrder(order + [glyph_name])
                maxp.numGlyphs = len(order) + 1
                # Update both BMP and full-Unicode maps, not only getBestCmap().
                for table in writable_tables:
                    table.cmap[code] = glyph_name
                existing.add(code)
                added.append(code)
    return added
