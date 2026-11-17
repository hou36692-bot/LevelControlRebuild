"""Build 50 original low-poly prop meshes and ten prefab recipes, without external assets."""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".local-assets/lc2-props"
CATALOG = ROOT / "ue/Plugins/LevelControlRuntime/Resources/catalog.json"


def box(x, y, z, width, depth, height):
    return ("box", (x, y, z), (width, depth, height))


def cylinder(x, y, z, radius, height, top=None):
    return ("round", (x, y, z), (radius, height, radius if top is None else top))


def legs(width, depth, height, thickness=7):
    return [box(x, y, height / 2, thickness, thickness, height)
            for x in [-width / 2 + thickness, width / 2 - thickness]
            for y in [-depth / 2 + thickness, depth / 2 - thickness]]


def table(width=140, depth=80, height=75):
    return legs(width, depth, height) + [box(0, 0, height, width, depth, 8)]


def chair():
    return table(48, 48, 44) + [box(0, 22, 70, 48, 6, 52)]


def shelf(width, depth, height, levels):
    return [box(x, 0, height / 2, 8, depth, height) for x in [-width / 2, width / 2]] + [
        box(0, 0, z * height / (levels - 1), width, depth, 6) for z in range(levels)]


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    definitions = [
        ("bench", "furniture", "wood-oak", table(180, 50, 42) + [box(0, 24, 72, 180, 8, 55)]),
        ("stool", "furniture", "wood-walnut", table(38, 38, 45)),
        ("table", "furniture", "wood-oak", table()),
        ("crate", "storage", "wood-oak", [box(0, 0, 40, 80, 70, 80)] +
         [box(x, 0, 40, 6, 76, 84) for x in [-34, 34]]),
        ("barrel", "storage", "wood-walnut", [cylinder(0, 0, 45, 34, 90)] +
         [cylinder(0, 0, z, 36, 6) for z in [12, 75]]),
        ("chest", "storage", "wood-walnut", [box(0, 0, 30, 100, 55, 60),
         box(0, 0, 65, 104, 59, 12), box(0, -30, 40, 12, 5, 20)]),
        ("planter", "decoration", "stone-limestone", [box(0, 0, 5, 90, 70, 10)] +
         [box(x, 0, 30, 10, 70, 60) for x in [-40, 40]] +
         [box(0, y, 30, 80, 10, 60) for y in [-30, 30]]),
        ("bollard", "street", "metal-steel", [cylinder(0, 0, 45, 12, 90), cylinder(0, 0, 8, 20, 16)]),
        ("lamppost", "street", "metal-steel", [cylinder(0, 0, 150, 7, 300),
         cylinder(0, 0, 10, 25, 20), box(0, 0, 290, 48, 48, 50), cylinder(0, 0, 322, 35, 20, 0)]),
        ("signboard", "street", "wood-painted", [box(0, 0, 100, 10, 10, 200), box(0, 0, 165, 140, 12, 70)]),
        ("picnic-table", "furniture", "wood-oak", table(180, 80, 75) +
         [box(0, y, 43, 180, 32, 8) for y in [-70, 70]] +
         [box(x, 0, 25, 10, 180, 10) for x in [-65, 65]]),
        ("chair", "furniture", "wood-walnut", chair()),
        ("desk", "furniture", "wood-walnut", table(130, 60, 75) + [box(42, 0, 37, 40, 55, 70)]),
        ("shelf", "storage", "wood-oak", shelf(120, 40, 180, 4)),
        ("bookcase", "storage", "wood-walnut", shelf(100, 35, 190, 6) + [box(0, 18, 95, 110, 5, 190)]),
        ("cabinet", "storage", "wood-painted", [box(0, 0, 55, 100, 45, 110),
         box(-5, -24, 60, 3, 4, 18), box(5, -24, 60, 3, 4, 18)]),
        ("locker", "storage", "metal-steel", [box(0, 0, 100, 45, 45, 200),
         box(15, -24, 105, 4, 5, 20)] + [box(0, -24, z, 25, 3, 2) for z in [170, 176, 182]]),
        ("rack", "storage", "metal-steel", shelf(180, 75, 200, 3)),
        ("cart", "furniture", "metal-steel", table(100, 60, 35) + [box(0, 28, 70, 100, 5, 60)] +
         [cylinder(x, y, 12, 12, 12) for x in [-40, 40] for y in [-24, 24]]),
        ("wheelbarrow", "furniture", "metal-rust", [box(0, 0, 45, 100, 65, 15),
         box(-45, 0, 65, 10, 65, 45)] + [box(25, y, 50, 170, 8, 8) for y in [-25, 25]] +
         [cylinder(-50, 0, 22, 20, 25)]),
        ("archway", "structure", "stone-limestone", [box(x, 0, 150, 45, 70, 300) for x in [-125, 125]] +
         [box(0, 0, 290, 290, 70, 45)]),
        ("gate", "structure", "metal-steel", [box(x, 0, 110, 6, 10, 220) for x in range(-100, 101, 25)] +
         [box(0, 0, z, 220, 12, 8) for z in [30, 190]]),
        ("fence", "structure", "wood-oak", [box(x, 0, 60, 9, 10, 120) for x in range(-100, 101, 25)] +
         [box(0, 0, z, 220, 12, 10) for z in [25, 90]]),
        ("rail", "structure", "metal-steel", [cylinder(x, 0, 50, 5, 100) for x in [-100, 0, 100]] +
         [box(0, 0, 100, 220, 8, 8)]),
        ("stairs", "structure", "stone-slate", [box(i * 35, 0, (i + 1) * 9, 35, 140, (i + 1) * 18) for i in range(6)]),
        ("ramp-block", "structure", "stone-limestone", [box(i * 15, 0, (i + 1) * 2, 15, 150, (i + 1) * 4) for i in range(14)]),
        ("podium", "structure", "wood-walnut", [box(0, 0, 10, 130, 100, 20), box(0, 0, 65, 85, 65, 110), box(0, 0, 122, 130, 90, 15)]),
        ("round-pillar", "structure", "stone-limestone", [cylinder(0, 0, 140, 30, 280), cylinder(0, 0, 10, 45, 20), cylinder(0, 0, 270, 45, 20)]),
        ("column-base", "structure", "stone-slate", [box(0, 0, 10, 110, 110, 20), cylinder(0, 0, 40, 45, 60), cylinder(0, 0, 75, 55, 15)]),
        ("obelisk", "landmark", "stone-slate", [box(0, 0, 15, 110, 110, 30), cylinder(0, 0, 170, 42, 310, 6)]),
        ("fountain", "landmark", "stone-limestone", [cylinder(0, 0, 20, 150, 40), cylinder(0, 0, 80, 25, 140), cylinder(0, 0, 130, 85, 20)]),
        ("well", "landmark", "stone-brick", [cylinder(0, 0, 40, 90, 80)] +
         [box(x, 0, 140, 15, 15, 220) for x in [-100, 100]] + [box(0, 0, 245, 240, 100, 15)]),
        ("birdbath", "decoration", "stone-limestone", [cylinder(0, 0, 50, 15, 100), cylinder(0, 0, 100, 65, 15), cylinder(0, 0, 6, 40, 12)]),
        ("statue-plinth", "landmark", "stone-slate", [box(0, 0, 10, 120, 120, 20), box(0, 0, 90, 80, 80, 160), box(0, 0, 175, 115, 115, 15)]),
        ("firepit", "decoration", "stone-brick", [box(math.cos(i*math.tau/10)*65, math.sin(i*math.tau/10)*65, 20, 40, 35, 40) for i in range(10)]),
        ("brazier", "decoration", "metal-rust", [cylinder(0, 0, 80, 35, 40, 55)] + legs(60, 60, 65)),
        ("lantern", "decoration", "metal-brass", [box(0, 0, 3, 30, 30, 6), box(0, 0, 40, 36, 36, 6)] + legs(30, 30, 40, 3)),
        ("umbrella", "decoration", "cloth-red", [cylinder(0, 0, 120, 4, 240), cylinder(0, 0, 240, 150, 45, 0)]),
        ("canopy", "structure", "cloth-linen", legs(300, 220, 250, 10) + [box(0, 0, 255, 320, 240, 12)]),
        ("kiosk", "structure", "wood-painted", legs(220, 160, 240, 12) + [box(0, 0, 240, 260, 190, 15), box(0, -65, 55, 220, 20, 110)]),
        ("trashbin", "street", "metal-steel", [cylinder(0, 0, 45, 28, 90), cylinder(0, 0, 92, 32, 8)]),
        ("mailbox", "street", "metal-rust", [box(0, 0, 55, 12, 12, 110), box(0, 0, 120, 55, 80, 40)]),
        ("hydrant", "street", "metal-rust", [cylinder(0, 0, 45, 18, 90), box(0, 0, 55, 75, 24, 24), cylinder(0, 0, 95, 24, 15, 10)]),
        ("terminal", "street", "metal-steel", [box(0, 0, 60, 60, 40, 120), box(0, -22, 130, 70, 10, 60)]),
        ("toolbox", "storage", "metal-rust", [box(0, 0, 18, 65, 32, 36), box(0, 0, 40, 25, 8, 8)]),
        ("pallet", "storage", "wood-oak", [box(x, 0, 15, 15, 100, 8) for x in range(-50, 51, 20)] +
         [box(0, y, 7, 120, 12, 14) for y in [-40, 0, 40]]),
        ("roadblock", "street", "stone-slate", [box(0, 0, 20, 200, 65, 40), box(0, 0, 65, 200, 30, 60)]),
        ("traffic-cone", "street", "emissive-warm", [box(0, 0, 3, 45, 45, 6), cylinder(0, 0, 38, 19, 70, 3)]),
        ("beacon", "street", "emissive-cool", [cylinder(0, 0, 55, 15, 110), cylinder(0, 0, 120, 25, 30), cylinder(0, 0, 6, 30, 12)]),
        ("flagpole", "landmark", "metal-steel", [cylinder(0, 0, 220, 4, 440), box(55, 0, 365, 110, 4, 80), cylinder(0, 0, 8, 30, 16)]),
    ]
    assert len(definitions) == 50
    props = []
    for name, category, surface, shapes in definitions:
        vertices, faces = [], []
        for kind, center, size in shapes:
            first = len(vertices) + 1
            if kind == "box":
                vertices.extend([(center[0]+x*size[0]/2, center[1]+y*size[1]/2,
                                  center[2]+z*size[2]/2)
                                 for z in [-1, 1] for y in [-1, 1] for x in [-1, 1]])
                local = [(0,2,3,1), (4,5,7,6), (0,1,5,4), (2,6,7,3), (0,4,6,2), (1,3,7,5)]
            else:
                radius, height, top = size
                sides = 16
                vertices.extend([(center[0]+r*math.cos(i*math.tau/sides),
                                  center[1]+r*math.sin(i*math.tau/sides), center[2]+z)
                                 for r, z in [(radius, -height/2), (top, height/2)]
                                 for i in range(sides)])
                local = [tuple(reversed(range(sides))), tuple(range(sides, 2*sides))] + [
                    (i, (i+1)%sides, (i+1)%sides+sides, i+sides) for i in range(sides)]
            for face in local:
                faces.extend([(first+face[0], first+face[i], first+face[i+1])
                              for i in range(1, len(face)-1)])
        minimum = [min(v[i] for v in vertices) for i in range(3)]
        maximum = [max(v[i] for v in vertices) for i in range(3)]
        dimensions = [maximum[i]-minimum[i] for i in range(3)]
        normalized = [[(v[i]-(minimum[i]+maximum[i])/2)*100/dimensions[i] for i in range(3)]
                      for v in vertices]
        # Unreal's OBJ path preserves source Z-up coordinates.
        lines = [f"v {v[0]:.7f} {v[1]:.7f} {v[2]:.7f}" for v in normalized]
        uv_lines, face_lines = [], []
        for face in faces:
            points = [normalized[index-1] for index in face]
            a = [points[1][i]-points[0][i] for i in range(3)]
            b = [points[2][i]-points[0][i] for i in range(3)]
            normal = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
            axes = [i for i in range(3) if i != max(range(3), key=lambda i: abs(normal[i]))]
            first_uv = len(uv_lines)+1
            uv_lines.extend(f"vt {p[axes[0]]/100+0.5:.7f} {p[axes[1]]/100+0.5:.7f}" for p in points)
            face_lines.append("f " + " ".join(f"{index}/{first_uv+i}" for i,index in enumerate(face)))
        lines += uv_lines + face_lines
        (OUTPUT / f"{name}.obj").write_text("\n".join(lines)+"\n", encoding="ascii")
        props.append({"id": "prop/"+name, "category": category, "tags": [category, name],
                      "size_cm": dimensions, "surface": surface,
                      "mesh": "/Game/LC2/Props/"+name.replace("-", "_")})
    recipes = [
        ("rest-stop", [("bench",0,0), ("lamppost",140,0), ("trashbin",-140,0)]),
        ("dining-set", [("table",0,0), ("chair",0,-90), ("chair",0,90), ("chair",-110,0), ("chair",110,0)]),
        ("gate-section", [("gate",0,0), ("round-pillar",-140,0), ("round-pillar",140,0)]),
        ("courtyard-center", [("fountain",0,0), ("bench",0,-260), ("bench",0,260)]),
        ("market-stall", [("canopy",0,0), ("table",0,-50), ("crate",120,30), ("barrel",-120,30)]),
        ("reading-corner", [("bookcase",0,70), ("chair",0,-60), ("lantern",65,-60)]),
        ("workshop", [("desk",0,0), ("stool",0,-70), ("toolbox",90,0), ("rack",0,100)]),
        ("checkpoint", [("roadblock",0,0), ("traffic-cone",-150,0), ("traffic-cone",150,0), ("beacon",190,0)]),
        ("monument", [("obelisk",0,0), ("brazier",-130,-100), ("brazier",130,-100)]),
        ("garden-entry", [("archway",0,0), ("planter",-220,0), ("planter",220,0), ("signboard",320,0)]),
    ]
    sizes = {p["id"]: p["size_cm"] for p in props}
    prefabs = [{"id": "prefab/"+name, "category": "prefab", "tags": [name], "components": [
        {"name": f"part-{index:02d}", "asset": "prop/"+asset,
         "position_cm": [x,y,sizes["prop/"+asset][2]/2], "rotation_deg": [0,0,0]}
        for index, (asset,x,y) in enumerate(parts)]} for name, parts in recipes]
    CATALOG.write_text(json.dumps({"version": 1, "license": "Project-authored procedural geometry",
                                   "props": props, "prefabs": prefabs}, indent=2)+"\n", encoding="utf-8")
    print(f"Generated {len(props)} props and {len(prefabs)} prefabs in {OUTPUT}")


if __name__ == "__main__":
    main()
