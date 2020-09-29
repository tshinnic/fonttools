""" Simplify TrueType glyphs by merging overlapping contours/components.

Requires https://github.com/fonttools/skia-pathops
"""

import itertools
from typing import Iterable, Optional, Mapping

from fontTools.ttLib import ttFont
from fontTools.ttLib.tables import _g_l_y_f
from fontTools.pens.ttGlyphPen import TTGlyphPen

import pathops


_TTGlyphMapping = Mapping[str, ttFont._TTGlyph]


def skPathFromGlyph(glyphName: str, glyphSet: _TTGlyphMapping) -> pathops.Path:
    path = pathops.Path()
    pathPen = path.getPen(glyphSet=glyphSet)
    glyphSet[glyphName].draw(pathPen)
    return path


def skPathFromGlyphComponent(
    component: _g_l_y_f.GlyphComponent, glyphSet: _TTGlyphMapping
):
    baseGlyphName, transformation = component.getComponentInfo()
    path = skPathFromGlyph(baseGlyphName, glyphSet)
    return path.transform(*transformation)


def componentsOverlap(glyph: _g_l_y_f.Glyph, glyphSet: _TTGlyphMapping) -> bool:
    if not glyph.isComposite():
        raise ValueError("This method only works with TrueType composite glyphs")
    if len(glyph.components) < 2:
        return False  # single component, no overlaps

    component_paths = {}

    def _get_nth_component_path(index: int) -> pathops.Path:
        if index not in component_paths:
            component_paths[index] = skPathFromGlyphComponent(
                glyph.components[index], glyphSet
            )
        return component_paths[index]

    return any(
        pathops.op(
            _get_nth_component_path(i),
            _get_nth_component_path(j),
            pathops.PathOp.INTERSECTION,
            fix_winding=False,
            keep_starting_points=False,
        )
        for i, j in itertools.combinations(range(len(glyph.components)), 2)
    )


def ttfGlyphFromSkPath(path: pathops.Path) -> _g_l_y_f.Glyph:
    # Skia paths have no 'components', no need for glyphSet
    ttPen = TTGlyphPen(glyphSet=None)
    path.draw(ttPen)
    glyph = ttPen.glyph()
    assert not glyph.isComposite()
    # compute glyph.xMin (glyfTable parameter unused for non composites)
    glyph.recalcBounds(glyfTable=None)
    return glyph


def removeOverlaps(
    font: ttFont.TTFont, glyphNames: Optional[Iterable[str]] = None
) -> None:
    """Simplify glyphs in TTFont by merging overlapping contours.

    Overlapping components are first decomposed to simple contours, then merged.

    Currently this only works with TrueType fonts with 'glyf' table.
    Raises NotImplementedError if 'glyf' table is absent.

    Args:
        font: input TTFont object, modified in place.
        glyphNames: optional iterable of glyph names (str) to remove overlaps from.
            By default, all glyphs in the font are processed.
    """
    try:
        glyfTable = font["glyf"]
    except KeyError:
        raise NotImplementedError("removeOverlaps currently only works with TTFs")

    hmtxTable = font["hmtx"]
    # wraps the underlying glyf Glyphs, takes care of interfacing with drawing pens
    glyphSet = font.getGlyphSet()

    if glyphNames is None:
        glyphNames = font.getGlyphOrder()

    # process all simple glyphs first, then composites with increasing component depth,
    # so that we don't unnecessarily decompose components simply because their base
    # glyph has overlaps
    glyphNames = sorted(
        glyphNames,
        key=lambda name: (
            glyfTable[name].getCompositeMaxpValues(glyfTable).maxComponentDepth
            if glyfTable[name].isComposite()
            else 0,
            name,
        ),
    )
    for glyphName in glyphNames:
        glyph = glyfTable[glyphName]
        # decompose composite glyphs only if components overlap each other
        if glyph.isComposite() and not componentsOverlap(glyph, glyphSet):
            continue

        path = skPathFromGlyph(glyphName, glyphSet)

        # remove overlaps
        path2 = pathops.simplify(path, clockwise=path.clockwise)

        # replace TTGlyph if simplified path is different
        if path2 != path:
            glyfTable[glyphName] = glyph = ttfGlyphFromSkPath(path2)
            # also ensure hmtx LSB == glyph.xMin so glyph origin is at x=0
            width, lsb = hmtxTable[glyphName]
            if lsb != glyph.xMin:
                hmtxTable[glyphName] = (width, glyph.xMin)


def main(args=None):
    import sys

    if args is None:
        args = sys.argv[1:]

    if len(args) < 2:
        print(
            f"usage: fonttools ttLib.removeOverlaps INPUT.ttf OUTPUT.ttf [GLYPHS ...]"
        )
        sys.exit(1)

    src = args[0]
    dst = args[1]
    glyphNames = args[2:] or None

    with ttFont.TTFont(src) as f:
        removeOverlaps(f, glyphNames)
        f.save(dst)


if __name__ == "__main__":
    main()
