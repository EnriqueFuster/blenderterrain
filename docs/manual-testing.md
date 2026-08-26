# Manual test for the current BlenderTerrain build

This build is intended to validate the first complete terrain and PNOA material path. It is
the initial MVP release candidate.

## Install

1. Open Blender 4.5.
2. Go to `Edit > Preferences > Get Extensions`.
3. Open the menu in the upper-right corner and choose `Install from Disk`.
4. Select the BlenderTerrain ZIP supplied with this test.
5. Allow online access in Blender when prompted or under Preferences.
6. Open the BlenderTerrain extension preferences and choose a cache directory with several
   gigabytes free.

The panel is in `3D Viewport > Sidebar (N) > Terrain`.

## Quick test area

Use this small area in Valencia:

```text
West   -0.381
South  39.469
East   -0.379
North  39.471
```

Recommended options:

```text
Elevation product     DTM (MDT02)
Elevation resolution  10 m
Use PNOA Orthophoto   enabled
Imagery GSD            1 m
Vertical Scale         1
Pack PNOA into .blend  disabled for the first run
```

Run the buttons in order:

1. `Validate ROI`
2. `Discover Sources`
3. `Download Data`
4. `Create Terrain`

CNIG distributes elevation by complete source sheet, so a small ROI can still require a
roughly 100 MB TIFF on the first run. Repeating the same area with the same cache should reuse
that TIFF.

## Inspect the result

1. Change to `Material Preview`.
2. Press `Numpad 7` for top view.
3. Press `Home` if the terrain is not framed.
4. Compare a road, field boundary, building group or other asymmetric feature with the same
   PNOA area in QGIS.
5. Orbit close to boundaries between terrain objects and imagery tiles.

Please report:

- whether north, south, east or west appears reversed;
- whether the image is vertically inverted;
- any black, bright or blurred line between images;
- mismatch between recognizable imagery and relief;
- unexpected color or contrast in Material Preview;
- time spent in discovery, download, processing and mesh creation;
- whether object and collection names are understandable;
- any confusing button or message.

## Duplicate and packed-image checks

- Press `Create Terrain` again. Blender should warn before creating another copy.
- Cancel once and confirm once to test both paths.
- With the original terrain present, press `Pack PNOA Images`. The confirmation reports the
  approximate embedded size. Save the `.blend`, temporarily rename the cache directory, reopen
  the file and confirm that the material still displays.

Packing copies the images into the `.blend`; it does not delete the cache. A packed project can
be much larger and slower to save.
