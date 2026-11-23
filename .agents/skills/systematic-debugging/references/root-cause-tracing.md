# Trace the cause, not only the crash

Adapted from Superpowers' root-cause-tracing and defense-in-depth guidance.

1. Locate the first invalid input or state in the failing path.
2. Inspect its caller and the assumptions that caller made.
3. Follow the value upstream until its source and expected contract are clear.
4. Correct the source or the violated boundary; do not add identical guards to
   every caller just to make the symptom disappear.
5. Keep validation at real input boundaries where it protects the contract.

In BlenderTerrain, an apparent mesh or UV problem can originate in a provider
extent, coordinate transformation, raster row order, or a scene origin offset.
Test these hypotheses against known inputs. A visually plausible offset is
not evidence of a correct coordinate transformation.

Retain a minimal failing fixture or reproduction so the correction can be checked.
