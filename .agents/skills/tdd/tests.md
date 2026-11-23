# Tests worth maintaining

Adapted from Matt Pocock's behavior-focused testing guidance for BlenderTerrain.

| Behavior | Useful evidence | Avoid |
| --- | --- | --- |
| ROI/tile selection | Tiny fixed grid with hand-checked selected tile IDs | Computing expected IDs with the same selection algorithm |
| CRS or extent conversion | Independently validated points and tolerances | An unverified round trip as the only evidence |
| NoData and raster alignment | Small fixture with explicit valid cells and expected layout | Repeating the implementation inside the assertion |
| Provider parsing | Representative recorded response and expected parsed contract | A mock that already returns the final answer |
| Add-on lifecycle | Registration, unregistration and repeated activation inside Blender | A fake bpy module as the only integration check |

Use names and interfaces already present in the repository. These examples do
not prescribe new functions, modules or dependencies.

Test meaningful outcomes rather than incidental call counts or private methods.
Treat changes to correct expected values as a contract decision, not a way to
turn a failing test green. Tests also have a maintenance cost; keep them focused.
