import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZipFile

from fontTools import ttLib
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.varLib.instancer import instantiateVariableFont

from source.py.extra_glyphs import EXTRA_GLYPHS_DIR, add_extra_glyphs
from source.py.utils import archive_fonts

# FontTools' table registry exposes dynamic table attributes in these assertions.
TTFont: Any = ttLib.TTFont
ROOT = Path(__file__).resolve().parents[1]
OPENAI = 0xEC81


class ExtraGlyphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TTFont(ROOT / "source/MapleMono[wght]-VF.ttf") as variable:
            font = instantiateVariableFont(variable, {"wght": 400})
            buffer = BytesIO()
            font.save(buffer)
            font.close()
        cls.base_bytes = buffer.getvalue()

    def setUp(self):
        self.font = TTFont(BytesIO(self.base_bytes))
        self.addCleanup(self.font.close)

    def test_bundled_subset_contains_only_openai(self):
        with TTFont(EXTRA_GLYPHS_DIR / "codicons.ttf") as source:
            self.assertEqual(set(source.getBestCmap()), {OPENAI})
            self.assertEqual(len(source.getGlyphOrder()), 2)

    def test_preserves_all_existing_glyphs_metrics_and_layout(self):
        cmap = dict(self.font.getBestCmap())
        order = list(self.font.getGlyphOrder())
        metrics = dict(self.font["hmtx"].metrics)
        outlines = {
            name: self.font["glyf"][name].compile(self.font["glyf"])
            for name in order
        }
        tables = {
            tag: self.font.getTableData(tag)
            for tag in ("GSUB", "GPOS", "GDEF", "name", "OS/2")
            if tag in self.font
        }
        self.assertEqual(add_extra_glyphs(self.font), [OPENAI])
        self.assertEqual(set(self.font.getBestCmap()) - set(cmap), {OPENAI})
        self.assertEqual(self.font.getGlyphOrder()[:-1], order)
        for code, name in cmap.items():
            self.assertEqual(self.font.getBestCmap()[code], name)
        for name in order:
            self.assertEqual(self.font["hmtx"][name], metrics[name])
            self.assertEqual(self.font["glyf"][name].compile(self.font["glyf"]), outlines[name])
        for tag, data in tables.items():
            self.assertEqual(self.font.getTableData(tag), data, tag)
        self.assertNotIn(0x25D0, self.font.getBestCmap())

    def test_roundtrip_updates_all_unicode_maps_and_one_cell_metrics(self):
        add_extra_glyphs(self.font)
        buffer = BytesIO()
        self.font.save(buffer)
        buffer.seek(0)
        with TTFont(buffer) as result:
            name = result.getBestCmap()[OPENAI]
            for table in result["cmap"].tables:
                if table.isUnicode() and table.format in (4, 12):
                    self.assertEqual(table.cmap[OPENAI], name)
            self.assertEqual(result["hmtx"][name], (600, 0))
            glyph = result["glyf"][name]
            self.assertEqual((glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax), (0, 60, 600, 660))
            self.assertEqual(result["maxp"].numGlyphs, len(result.getGlyphOrder()))

    def test_idempotent_and_existing_codepoints_are_never_replaced(self):
        add_extra_glyphs(self.font)
        before = {tag: self.font.getTableData(tag) for tag in ("glyf", "cmap", "hmtx")}
        self.assertEqual(add_extra_glyphs(self.font), [])
        for tag, data in before.items():
            self.assertEqual(self.font.getTableData(tag), data)

        with TTFont(BytesIO(self.base_bytes)) as existing:
            original_count = len(existing.getGlyphOrder())
            for table in existing["cmap"].tables:
                if table.isUnicode():
                    table.cmap[OPENAI] = "A"
            self.assertEqual(add_extra_glyphs(existing), [])
            self.assertEqual(existing.getBestCmap()[OPENAI], "A")
            self.assertEqual(len(existing.getGlyphOrder()), original_count)

    def test_respects_actual_cell_width(self):
        for width in (500, 550, 600):
            with self.subTest(width=width), TTFont(BytesIO(self.base_bytes)) as font:
                font["hmtx"][font.getBestCmap()[0x20]] = (width, 0)
                add_extra_glyphs(font)
                name = font.getBestCmap()[OPENAI]
                self.assertEqual(font["hmtx"][name][0], width)
                glyph = font["glyf"][name]
                self.assertEqual(glyph.xMax - glyph.xMin, width)
                self.assertEqual(glyph.yMax - glyph.yMin, width)

    def test_future_non_bmp_subset_creates_full_unicode_map(self):
        self.font["cmap"].tables = [table for table in self.font["cmap"].tables if table.format != 12]
        original_cmap = dict(self.font.getBestCmap())
        code = 0xF0001
        with TemporaryDirectory() as directory:
            with TTFont(EXTRA_GLYPHS_DIR / "codicons.ttf") as source:
                name = source.getBestCmap()[OPENAI]
                table: Any = CmapSubtable.newSubtable(12)
                table.platformID, table.platEncID, table.language = 3, 10, 0
                table.cmap = {code: name}
                source["cmap"].tables = [table]
                source.save(Path(directory) / "future.ttf")
            self.assertEqual(add_extra_glyphs(self.font, Path(directory)), [code])
        for old_code, name in original_cmap.items():
            self.assertEqual(self.font.getBestCmap()[old_code], name)
        buffer = BytesIO()
        self.font.save(buffer)
        buffer.seek(0)
        with TTFont(buffer) as result:
            self.assertIn(code, result.getBestCmap())
            for table in result["cmap"].tables:
                if table.format == 4:
                    self.assertNotIn(code, table.cmap)

    def test_composite_subset_does_not_copy_unrelated_glyphs(self):
        code = 0xE001
        with TemporaryDirectory() as directory:
            with TTFont(EXTRA_GLYPHS_DIR / "codicons.ttf") as source:
                original = source.getBestCmap()[OPENAI]
                pen = TTGlyphPen(source.getGlyphSet())
                pen.addComponent(original, (1, 0, 0, 1, 0, 0))
                order = list(source.getGlyphOrder())
                source["glyf"]["composite"] = pen.glyph()
                source["hmtx"]["composite"] = source["hmtx"][original]
                source.setGlyphOrder(order + ["composite"])
                for table in source["cmap"].tables:
                    table.cmap = {code: "composite"}
                source.save(Path(directory) / "composite.ttf")
            count = len(self.font.getGlyphOrder())
            self.assertEqual(add_extra_glyphs(self.font, Path(directory)), [code])
        self.assertEqual(len(self.font.getGlyphOrder()), count + 1)
        self.assertFalse(self.font["glyf"][self.font.getBestCmap()[code]].isComposite())

    def test_missing_assets_and_variable_destination_fail_explicitly(self):
        with TemporaryDirectory() as directory, self.assertRaises(FileNotFoundError):
            add_extra_glyphs(self.font, Path(directory))
        with (
            TTFont(ROOT / "source/MapleMono[wght]-VF.ttf") as variable,
            self.assertRaisesRegex(ValueError, "static TrueType"),
        ):
            add_extra_glyphs(variable)

    def test_nf_archives_include_attribution_and_license(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            for variant in ("NF", "NF-CN", "TTF"):
                folder = root / variant
                folder.mkdir()
                self.font.save(folder / "Test.ttf")
                _, name = archive_fonts(str(folder), str(root), "Test", "", str(config))
                with ZipFile(root / f"{name}.zip") as archive:
                    self.assertIn("LICENSE.txt", archive.namelist())
                    for filename in ("NOTICE.txt", "LICENSE-Codicons.txt"):
                        member = f"extra-glyphs/{filename}"
                        if variant == "TTF":
                            self.assertNotIn(member, archive.namelist())
                        else:
                            self.assertEqual(archive.read(member), (EXTRA_GLYPHS_DIR / filename).read_bytes())


if __name__ == "__main__":
    unittest.main()
