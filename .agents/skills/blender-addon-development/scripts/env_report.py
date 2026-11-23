"""Report Blender runtime facts without changing the scene or filesystem."""

import json
import platform

import bpy


def main():
    """Print a tagged JSON object so Blender log lines remain distinguishable."""
    report = {
        "blender_version": bpy.app.version_string,
        "blender_version_tuple": list(bpy.app.version),
        "python_version": platform.python_version(),
        "background": bool(bpy.app.background),
        "platform": platform.system(),
        "architecture": platform.machine(),
    }
    print("BLENDERTERRAIN_ENV=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
