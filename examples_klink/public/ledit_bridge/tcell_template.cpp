/* tcell_template.cpp — COPY-AND-ADAPT starting point for T-Cell generator
 * code written back through the klink bridge (`tcell_workflows.py
 * writeback` / `set_tcell_code`).
 *
 * DO NOT hand-write UPI C++ from scratch: this exact pattern is
 * byte-exact-verified against L-Edit (the sample device below passed a
 * 6-point differential grid). Hand-rolled code almost always fails
 * L-Edit's in-place compile — and a compile error pops a MODAL dialog,
 * which pauses the bridge heartbeat until the user closes it.
 *
 * The sample device is a parametric via array (n x n squares of w um at
 * pitch um under a metal cap plate). Adapt the marked EDIT sites:
 *   1. cell/function name          (KLINK_VIA_ARRAY -> yours)
 *   2. parameter reads             (typed getters; see below)
 *   3. layers + GDS numbers        (need_layer stamps them)
 *   4. geometry                    (integer internal units only)
 *
 * Rules the template already obeys — keep them:
 * - NO `#define EXCLUDE_LEDIT_LEGACY_UPI`: the Ex830 layer-parameter
 *   calls used for GDS stamping live in that header section.
 * - Parameters are read with LCell_GetParameterAsDouble/Int/Coord — the
 *   bridge's `read` verb discovers parameters by finding those calls.
 * - All geometry in INTEGER internal units (LFile_MicronsToIntU once per
 *   micron input, then integer math) — this is what makes byte-exact
 *   verification against a Python/KLayout reference possible.
 * - need_layer(): create-if-missing, stamp GDS number ONLY when
 *   unassigned (never overwrites the target design's numbering), default
 *   auto-color for new layers. A layer with GDS -1 exports wrong — this
 *   is tape-out critical.
 * - Guard degenerate parameters and return via LUpi_SetReturnCode(1) —
 *   never generate garbage geometry.
 */
#include <string.h>
#include <ldata.h>

static LLayer need_layer(LFile file, const char* name, short gds, short dt)
{
    LLayer layer = LLayer_Find(file, name);
    if (!layer) {
        LLayer_New(file, (LLayer)0, name);
        layer = LLayer_Find(file, name);
        if (layer) {   /* default style: hashed palette color, solid fill */
            LRenderingAttribute ra;
            if (LLayer_GetRenderingAttribute(layer, raiObject, &ra) == LStatusOK) {
                int nc = LFile_GetColorPaletteNumColors(file);
                if (nc > 1) {
                    unsigned h = 5381;
                    const char* p;
                    for (p = name; *p; ++p) h = h * 33 + (unsigned char)*p;
                    ra.mFillColorIndex = 1 + (h % (unsigned)(nc - 1));
                    memset(ra.mFillPattern, 0xFF, sizeof(LStipple));
                    memset(ra.mOutlinePattern, 0x00, sizeof(LStipple));
                    LLayer_SetRenderingAttribute(layer, raiObject, &ra);
                }
            }
        }
    }
    if (layer) {
        LLayerParamEx830 lp;
        memset(&lp, 0, sizeof(lp));
        if (LLayer_GetParametersEx830(layer, &lp) == LStatusOK
                && lp.GDSNumber < 0) {   /* stamp unassigned only */
            lp.GDSNumber = gds;
            lp.GDSDataType = dt;
            LLayer_SetParametersEx830(layer, &lp);
        }
    }
    return layer;
}

/* EDIT 1: rename to <YourCell>_main */
void KLINK_VIA_ARRAY_main(void)
{
    LCell cellCurrent = (LCell)LMacro_GetNewTCell();

    /* EDIT 2: read YOUR parameters (names must match the writeback
     * --params table; use the typed getter matching each type) */
    double w = LCell_GetParameterAsDouble(cellCurrent, "w");
    double pitch = LCell_GetParameterAsDouble(cellCurrent, "pitch");
    int    n = LCell_GetParameterAsInt(cellCurrent, "n");
    LFile file = LCell_GetFile(cellCurrent);

    /* EDIT 3: YOUR layers + GDS numbers */
    LLayer lyVia = need_layer(file, "VIA", 30, 0);
    LLayer lyMetal = need_layer(file, "METAL1", 20, 0);

    /* microns -> integer internal units ONCE, then integer math only */
    LCoord W = LFile_MicronsToIntU(file, w);
    LCoord P = LFile_MicronsToIntU(file, pitch);
    const LCoord MARGIN = 500;   /* internal units (nm at 0.001 um/unit) */

    if (n < 1 || W <= 0) { LUpi_SetReturnCode(1); return; }

    /* EDIT 4: YOUR geometry */
    int i, j;
    for (i = 0; i < n; i++)
        for (j = 0; j < n; j++)
            LBox_New(cellCurrent, lyVia,
                     i * P, j * P, i * P + W, j * P + W);

    LBox_New(cellCurrent, lyMetal,
             -MARGIN, -MARGIN,
             (n - 1) * P + W + MARGIN, (n - 1) * P + W + MARGIN);
}

extern "C" int UPI_Entry_Point(void)
{
    KLINK_VIA_ARRAY_main();   /* EDIT 1 (same rename) */
    return 1;
}
