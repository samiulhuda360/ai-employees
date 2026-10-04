"""Hermes HQ deck assets, built procedurally in Blender (run headless):

    blender -b --factory-startup --python build_deck.py -- <out_dir>

Writes two glTF binaries the web deck loads with three.js:
  deck.glb      the bridge: floor with seams and light rings, Claudia's dais, curved back
                wall with light strips, status-board frame, pillars, rear truss, relay base
  pedestal.glb  one agent station: hex plinth with a glow ring and an angled console with
                a screen. The web app clones it per agent and recolours two materials:
                StationGlow (plinth ring) and StationScreen (console screen) by live state.

Units are metres and match the scene: deck radius ~13, stations within ~9 of the centre,
status board centred at (0, 3.3, -10.5), relay at (10.5, 0, -8.5). Blender is Z-up; the
glTF exporter converts to Y-up, so "y" in the web scene is Blender's z.
"""

import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

OUT = Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else Path.cwd()
OUT.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ helpers

def reset():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for block in (bpy.data.meshes, bpy.data.materials):
        for b in list(block):
            block.remove(b)


def mat(name, color, metallic=0.0, roughness=0.5, emit=None, strength=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = roughness
    if emit:
        p.inputs["Emission Color"].default_value = (*emit, 1)
        p.inputs["Emission Strength"].default_value = strength
    return m


def hexc(h):
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)  # to linear


def finish(obj, material, bevel=0.0, segments=2, smooth=False):
    if bevel:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = segments
        mod.limit_method = "ANGLE"
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.data.materials.clear()
    obj.data.materials.append(material)
    if smooth:
        for poly in obj.data.polygons:
            poly.use_smooth = True
    return obj


def cyl(name, r, depth, z, verts=64, x=0.0, y=0.0):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth, location=(x, y, z))
    o = bpy.context.active_object
    o.name = name
    return o


def box(name, size, loc, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    o.name = name
    return o


def ring(name, r_in, r_out, z, verts=72, start=0.0, end=2 * math.pi, height=0.02):
    """A flat (or slightly thick) annulus, optionally only an arc."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    n = verts
    full = abs(end - start - 2 * math.pi) < 1e-6
    steps = n if full else n + 1
    top_in, top_out, bot_in, bot_out = [], [], [], []
    for i in range(steps):
        a = start + (end - start) * i / n
        c, s = math.cos(a), math.sin(a)
        top_in.append(bm.verts.new((r_in * c, r_in * s, z + height / 2)))
        top_out.append(bm.verts.new((r_out * c, r_out * s, z + height / 2)))
        bot_in.append(bm.verts.new((r_in * c, r_in * s, z - height / 2)))
        bot_out.append(bm.verts.new((r_out * c, r_out * s, z - height / 2)))
    segs = range(n) if full else range(n)
    for i in segs:
        j = (i + 1) % steps
        bm.faces.new((top_in[i], top_out[i], top_out[j], top_in[j]))
        bm.faces.new((bot_in[j], bot_out[j], bot_out[i], bot_in[i]))
        bm.faces.new((top_out[i], bot_out[i], bot_out[j], top_out[j]))
        bm.faces.new((bot_in[i], top_in[i], top_in[j], bot_in[j]))
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    return o


def curved_panel(name, radius, a0, a1, z0, z1, thickness=0.12, verts=12, zsteps=1):
    """A section of a cylinder wall, facing the centre, with zsteps rows of faces."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    grids = []
    for r in (radius, radius + thickness):
        grid = []
        for i in range(verts + 1):
            a = a0 + (a1 - a0) * i / verts
            grid.append([bm.verts.new((r * math.cos(a), r * math.sin(a), z0 + (z1 - z0) * k / zsteps)) for k in range(zsteps + 1)])
        grids.append(grid)
    inner, outer = grids
    for i in range(verts):
        for k in range(zsteps):
            bm.faces.new((inner[i][k], inner[i + 1][k], inner[i + 1][k + 1], inner[i][k + 1]))
            bm.faces.new((outer[i + 1][k], outer[i][k], outer[i][k + 1], outer[i + 1][k + 1]))
        bm.faces.new((inner[i][-1], inner[i + 1][-1], outer[i + 1][-1], outer[i][-1]))
        bm.faces.new((outer[i][0], outer[i + 1][0], inner[i + 1][0], inner[i][0]))
    for k in range(zsteps):
        bm.faces.new((inner[0][k], inner[0][k + 1], outer[0][k + 1], outer[0][k]))
        bm.faces.new((outer[-1][k], outer[-1][k + 1], inner[-1][k + 1], inner[-1][k]))
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    return o


def bake_ao(samples=48):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    solids = [o for o in scene.objects if o.type == "MESH" and o.active_material
              and o.active_material.name not in GLOW_MATERIALS]
    for o in solids:
        if not o.data.color_attributes:
            o.data.color_attributes.new("AO", "BYTE_COLOR", "CORNER")
        o.data.color_attributes.active_color = o.data.color_attributes[0]
    bpy.ops.object.select_all(action="DESELECT")
    for o in solids:
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.bake(type="AO", target="VERTEX_COLORS")
        o.select_set(False)
    print(f"baked AO on {len(solids)} objects")


GLOW_MATERIALS = {"SeamGlow", "CyanGlow", "GoldGlow", "CoralGlow", "StationGlow", "StationScreen"}


def export(path):
    bpy.ops.object.select_all(action="SELECT")
    kw = dict(filepath=str(path), export_format="GLB", use_selection=True, export_apply=True,
              export_yup=True, export_lights=False, export_cameras=False)
    try:
        bpy.ops.export_scene.gltf(**kw, export_vertex_color="ACTIVE")
    except TypeError:
        bpy.ops.export_scene.gltf(**kw, export_colors=True)
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


# ------------------------------------------------------------------ materials

def materials():
    return {
        "deck": mat("DeckMetal", hexc("#2a3646"), metallic=0.55, roughness=0.5),
        "plate": mat("DeckPlate", hexc("#334255"), metallic=0.5, roughness=0.55),
        "trim": mat("TrimMetal", hexc("#46566a"), metallic=0.7, roughness=0.35),
        "wall": mat("WallPanel", hexc("#2c3a4b"), metallic=0.45, roughness=0.6),
        "inset": mat("WallInset", hexc("#18222e"), metallic=0.3, roughness=0.7),
        "seam": mat("SeamGlow", hexc("#0e3a55"), emit=hexc("#29d9f5"), strength=0.22),
        "cyan": mat("CyanGlow", hexc("#29d9f5"), emit=hexc("#29d9f5"), strength=3.0),
        "gold": mat("GoldGlow", hexc("#f4dca0"), emit=hexc("#f4dca0"), strength=2.5),
        "coral": mat("CoralGlow", hexc("#ff8a65"), emit=hexc("#ff8a65"), strength=2.0),
        "screen": mat("ScreenFrame", hexc("#1f2a38"), metallic=0.8, roughness=0.3),
    }


# ------------------------------------------------------------------ the bridge

def build_deck(M):
    # Floor: main disc, an inner plate, a raised outer rim.
    finish(cyl("Floor", 13.2, 0.3, -0.16, verts=64), M["deck"], bevel=0.04)
    finish(ring("FloorRim", 12.4, 13.3, 0.08, height=0.16), M["trim"], bevel=0.02)

    # Light rings (walkway edges) and radial seams.
    for r in (2.62, 4.45, 8.25):
        finish(ring(f"LightRing{r}", r, r + 0.05, 0.03, height=0.02), M["seam"])
    finish(ring("RimLight", 12.35, 12.42, 0.17, height=0.02), M["cyan"])
    for i in range(12):
        a = 2 * math.pi * i / 12 + math.pi / 12
        mid = (2.7 + 12.3) / 2
        finish(box(f"Seam{i}", (12.3 - 2.7, 0.025, 0.012), (mid * math.cos(a), mid * math.sin(a), 0.026), (0, 0, a)), M["seam"])

    # Claudia's dais: three stepped tiers, gold rim.
    for k, (r, h) in enumerate(((2.45, 0.1), (2.0, 0.2), (1.55, 0.3))):
        finish(cyl(f"DaisTier{k}", r, 0.1, h - 0.05, verts=96), M["trim"] if k != 2 else M["plate"], bevel=0.02)
    finish(ring("DaisRim", 1.52, 1.6, 0.31, height=0.02), M["gold"])
    finish(ring("DaisStepLight", 1.97, 2.02, 0.21, height=0.015), M["gold"])

    # Curved back wall behind the far stations (Blender +y is the scene's back, -z).
    R, a0, a1 = 14.2, math.radians(35), math.radians(145)
    n = 11
    for i in range(n):
        s = a0 + (a1 - a0) * i / n
        e = a0 + (a1 - a0) * (i + 1) / n
        finish(curved_panel(f"Wall{i}", R, s + 0.006, e - 0.006, 0.0, 6.2, zsteps=8), M["wall"], bevel=0.03)
        pad = (e - s) * 0.14
        finish(curved_panel(f"WallInset{i}", R - 0.06, s + pad, e - pad, 1.2, 5.0, thickness=0.05, zsteps=6), M["inset"])
        finish(curved_panel(f"WallInsetLight{i}", R - 0.1, s + pad, e - pad, 4.86, 4.9, thickness=0.02), M["cyan"])
        c = s
        finish(box(f"WallStrip{i}", (0.05, 0.05, 5.6), (R * 0.998 * math.cos(c), R * 0.998 * math.sin(c), 3.1)), M["cyan"])
    finish(curved_panel("WallCapLight", R - 0.02, a0, a1, 6.05, 6.12, thickness=0.04, verts=40), M["cyan"])
    finish(curved_panel("WallBaseLight", R - 0.02, a0, a1, 0.25, 0.3, thickness=0.04, verts=40), M["coral"])

    # Status-board frame: the web app draws the live bars at (0, 3.3, -10.5), 8.5 x 2.9.
    fx, fz, fy = 0.0, 3.3, 10.62  # Blender y = -scene z
    w, h, t = 8.5, 2.9, 0.16
    for name, size, loc in (
        ("FrameTop", (w + 2 * t, 0.12, t), (fx, fy, fz + h / 2 + t / 2)),
        ("FrameBottom", (w + 2 * t, 0.12, t), (fx, fy, fz - h / 2 - t / 2)),
        ("FrameLeft", (t, 0.12, h), (fx - w / 2 - t / 2, fy, fz)),
        ("FrameRight", (t, 0.12, h), (fx + w / 2 + t / 2, fy, fz)),
    ):
        finish(box(name, size, (loc[0], loc[1], loc[2])), M["screen"], bevel=0.02)
    finish(box("FrameBack", (w, 0.04, h), (fx, fy + 0.08, fz)), M["wall"])
    finish(box("FrameGlow", (w + 2 * t, 0.02, 0.03), (fx, fy - 0.07, fz - h / 2 - t)), M["cyan"])
    for sx in (-1, 1):  # support struts down to the floor
        finish(box(f"FrameStrut{sx}", (0.18, 0.18, fz - h / 2), (sx * (w / 2 - 0.6), fy + 0.1, (fz - h / 2) / 2)), M["trim"], bevel=0.02)

    # Pillars around the open front half, each with a light strip facing inwards.
    for i, deg in enumerate((8, 30, 150, 172)):  # sides and back only: nothing between camera and deck
        a = math.radians(deg)
        x, y = 13.6 * math.cos(a), 13.6 * math.sin(a)
        p = cyl(f"Pillar{i}", 0.34, 6.6, 3.3, verts=6, x=x, y=y)
        p.rotation_euler[2] = a
        finish(p, M["trim"], bevel=0.03)
        finish(box(f"PillarLight{i}", (0.04, 0.06, 5.8), (13.26 * math.cos(a), 13.26 * math.sin(a), 3.3), (0, 0, a)), M["cyan"])

    # Rear overhead truss, following the back wall.
    finish(curved_panel("Truss", 12.6, math.radians(20), math.radians(160), 6.6, 6.9, thickness=0.5, verts=48), M["trim"], bevel=0.03)
    finish(curved_panel("TrussLight", 12.58, math.radians(20), math.radians(160), 6.58, 6.62, thickness=0.02, verts=48), M["cyan"])

    # Relay tower base at scene (10.5, 0, -8.5) -> Blender (10.5, 8.5).
    finish(cyl("RelayBase", 1.1, 0.25, 0.12, verts=6, x=10.5, y=8.5), M["trim"], bevel=0.03)
    finish(cyl("RelayCollar", 0.55, 0.5, 0.45, verts=24, x=10.5, y=8.5), M["plate"], bevel=0.02)
    finish(ring("RelayRing", 0.9, 0.98, 0.26, height=0.02), M["cyan"])
    bpy.data.objects["RelayRing"].location = (10.5, 8.5, 0)


# ------------------------------------------------------------------ one station

def build_pedestal(M):
    glow = mat("StationGlow", hexc("#29d9f5"), emit=hexc("#29d9f5"), strength=2.5)
    screen = mat("StationScreen", hexc("#29d9f5"), emit=hexc("#29d9f5"), strength=1.2)
    finish(cyl("Plinth", 0.86, 0.14, 0.07, verts=6), M["trim"], bevel=0.025)
    finish(cyl("PlinthTop", 0.74, 0.04, 0.16, verts=6), M["plate"], bevel=0.01)
    finish(ring("PlinthGlow", 0.62, 0.72, 0.185, verts=6, height=0.012), glow)
    # Console: scene position (0, 0.35, 0.9) tilted -0.5 rad -> Blender y = -0.9.
    finish(box("ConsoleLeg", (0.12, 0.12, 0.34), (0, -0.9, 0.17)), M["trim"], bevel=0.02)
    finish(box("ConsoleBody", (1.5, 0.7, 0.07), (0, -0.9, 0.38), (-0.5, 0, 0)), M["trim"], bevel=0.02)
    finish(box("ConsoleScreen", (1.32, 0.52, 0.012), (0, -0.9 + 0.02, 0.425), (-0.5, 0, 0)), screen)


# ------------------------------------------------------------------ main

reset()
M = materials()
build_deck(M)
bake_ao()
export(OUT / "deck.glb")

reset()
M = materials()
build_pedestal(M)
bake_ao()
export(OUT / "pedestal.glb")
