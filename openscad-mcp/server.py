"""
OpenSCAD MCP Server — remote HTTP/SSE endpoint for claude.ai
Generates OpenSCAD code for 3D printing and CSG modelling.
No external API required.
"""
import os
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
server = Server("openscad-mcp")
sse_transport = SseServerTransport("/messages/")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="generate_openscad",
            description=(
                "Generate OpenSCAD code (.scad file content) for a described 3D object. "
                "OpenSCAD uses a declarative scripting language for CSG modelling. "
                "Great for 3D printing parametric parts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "Describe the 3D object, e.g. 'a phone stand with 15° angle', "
                            "'a hex bolt M6x20', 'a keycap with Cherry MX stem', "
                            "'a parametric box with lid', 'gear with 20 teeth'."
                        )
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Optional dimension overrides in mm, e.g. {\"width\": 80, \"height\": 50}."
                    }
                },
                "required": ["description"]
            }
        ),
        Tool(
            name="explain_openscad_syntax",
            description="Explain OpenSCAD language features: primitives, transforms, boolean ops, modules, functions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic to explain: 'primitives', 'transforms', 'booleans', 'modules', 'functions', 'for loops', 'hull', 'minkowski'."
                    }
                },
                "required": ["topic"]
            }
        ),
        Tool(
            name="estimate_print_specs",
            description="Estimate 3D print time, filament usage, and slicer settings for an object.",
            inputSchema={
                "type": "object",
                "properties": {
                    "volume_cm3": {
                        "type": "number",
                        "description": "Object volume in cm³."
                    },
                    "infill_percent": {
                        "type": "number",
                        "description": "Infill percentage (0-100). Default 20.",
                        "default": 20
                    },
                    "layer_height_mm": {
                        "type": "number",
                        "description": "Layer height in mm. Default 0.2.",
                        "default": 0.2
                    }
                },
                "required": ["volume_cm3"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "generate_openscad":
        result = _gen_openscad(arguments.get("description", ""), arguments.get("parameters", {}))
    elif name == "explain_openscad_syntax":
        result = _explain_syntax(arguments.get("topic", ""))
    elif name == "estimate_print_specs":
        result = _estimate_print(
            arguments.get("volume_cm3", 0),
            arguments.get("infill_percent", 20),
            arguments.get("layer_height_mm", 0.2)
        )
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


def _gen_openscad(desc: str, params: dict) -> str:
    d = desc.lower()

    if "phone stand" in d or "phone holder" in d:
        angle = params.get("angle", 15)
        return f'''\
// OpenSCAD: Parametric Phone Stand
// {desc}

angle       = {angle};   // degrees of tilt
base_w      = {params.get("width", 80)};
base_d      = {params.get("depth", 60)};
base_h      = {params.get("base_height", 4)};
slot_w      = {params.get("slot_width", 8)};
slot_depth  = {params.get("slot_depth", 15)};
back_h      = {params.get("back_height", 70)};
back_t      = {params.get("back_thickness", 3)};
$fn         = 32;

module phone_stand() {{
    // Base plate
    cube([base_w, base_d, base_h], center=true);

    // Phone slot (front ledge)
    translate([0, -base_d/2 + slot_depth/2, base_h/2 + slot_depth/2])
        cube([base_w * 0.8, slot_depth, slot_depth], center=true);

    // Angled back support
    translate([0, base_d/4, base_h/2])
    rotate([-angle, 0, 0])
        cube([base_w * 0.8, back_t, back_h], center=true);
}}

phone_stand();
'''

    if "box" in d:
        return f'''\
// OpenSCAD: Parametric Box with optional lid
// {desc}

outer_w = {params.get("width", 100)};
outer_d = {params.get("depth", 70)};
outer_h = {params.get("height", 50)};
wall_t  = {params.get("wall_thickness", 2)};
lid_h   = {params.get("lid_height", 8)};
$fn     = 32;

module box_body() {{
    difference() {{
        cube([outer_w, outer_d, outer_h]);
        translate([wall_t, wall_t, wall_t])
            cube([outer_w-2*wall_t, outer_d-2*wall_t, outer_h]);
    }}
}}

module lid() {{
    difference() {{
        cube([outer_w, outer_d, lid_h]);
        translate([wall_t*1.5, wall_t*1.5, wall_t])
            cube([outer_w-3*wall_t, outer_d-3*wall_t, lid_h]);
    }}
}}

// Print body and lid side-by-side
box_body();
translate([outer_w + 5, 0, 0]) lid();
'''

    if "gear" in d:
        teeth = int(params.get("teeth", 20))
        m = params.get("module", 2)  # gear module
        return f'''\
// OpenSCAD: Spur Gear
// {desc}
// Uses MCAD library (include with: use <MCAD/gears.scad>)
// If MCAD not available, install from: https://github.com/openscad/MCAD

use <MCAD/gears.scad>

teeth       = {teeth};
mod         = {m};     // gear module (tooth size)
thickness   = {params.get("thickness", 8)};
bore_d      = {params.get("bore_diameter", 5)};
$fn         = 64;

difference() {{
    gear(mod, teeth, thickness, 0);  // MCAD gear
    cylinder(h=thickness+1, d=bore_d, center=true, $fn=32);  // bore hole
}}
'''

    if "bolt" in d or "screw" in d:
        return f'''\
// OpenSCAD: Hex Bolt (simplified)
// {desc}

thread_d    = {params.get("diameter", 6)};   // M6 = 6 mm
thread_l    = {params.get("length", 20)};
head_across = thread_d * 1.75;              // across-flats
head_h      = thread_d * 0.65;
$fn         = 6;                            // hex = 6 sides

union() {{
    // Hex head
    cylinder(h=head_h, d=head_across);

    // Shaft (simplified — no actual thread geometry)
    translate([0, 0, head_h])
        cylinder(h=thread_l, d=thread_d, $fn=32);
}}
'''

    # Generic template
    return f'''\
// OpenSCAD: {desc}
// Adjust parameters at the top, then press F6 to render, F7 to export STL.

$fn = 64;  // smoothness (lower for faster preview)

// --- Parameters ---
width  = {params.get("width", 50)};
height = {params.get("height", 30)};
depth  = {params.get("depth", 20)};

// --- Model ---
difference() {{
    // Outer shell
    cube([width, depth, height]);

    // Hollow interior (example — remove if solid)
    translate([2, 2, 2])
        cube([width-4, depth-4, height]);
}}

// --- Common OpenSCAD patterns ---
// union()       {{ ... }}         // merge
// difference()  {{ A; B; }}       // subtract B from A
// intersection(){{ A; B; }}       // keep overlap
// translate([x,y,z])  object();
// rotate([rx,ry,rz])  object();
// scale([sx,sy,sz])   object();
// linear_extrude(h)   polygon([...]);
// rotate_extrude()    polygon([...]);
// hull()              {{ ... }}   // convex hull
// minkowski()         {{ ... }}   // Minkowski sum
'''


def _explain_syntax(topic: str) -> str:
    t = topic.lower()
    db = {
        "primitives": """\
**OpenSCAD Primitives**
```openscad
sphere(r=10);                        // sphere, radius 10
cylinder(h=20, r=5);                 // cylinder
cylinder(h=20, r1=5, r2=2);         // cone
cube([50, 30, 20]);                  // rectangular box
cube(20, center=true);               // centered cube
polygon([[0,0],[10,0],[5,8]]);       // 2D polygon
circle(r=15);                        // 2D circle
square([40, 20], center=true);       // 2D rectangle
text("Hello", size=10);              // 2D text
```
""",
        "transform": """\
**OpenSCAD Transforms**
```openscad
translate([10, 0, 5])  sphere(3);
rotate([0, 45, 90])    cube(10);
scale([2, 1, 0.5])     sphere(5);
mirror([1, 0, 0])      cube(10);    // mirror across YZ plane
resize([20, 0, 30])    sphere(10);  // resize to exact dims (0 = auto)
multmatrix(M)          children();  // arbitrary 4×4 transform
```
""",
        "boolean": """\
**OpenSCAD Boolean Operations**
```openscad
union() {                   // merge — default when stacking
    cube(10);
    sphere(7);
}
difference() {              // subtract Tool from Base
    cube(20);               // Base
    sphere(12);             // Tool (removed)
}
intersection() {            // keep only shared volume
    cube(15, center=true);
    sphere(10);
}
```
""",
        "module": """\
**OpenSCAD Modules (reusable components)**
```openscad
module rounded_box(w, d, h, r=3) {
    hull() {
        for (x=[r, w-r], y=[r, d-r])
            translate([x, y, 0]) cylinder(h=h, r=r, $fn=32);
    }
}

rounded_box(60, 40, 20);
translate([70, 0, 0]) rounded_box(30, 30, 15, r=5);
```
""",
        "hull": """\
**hull() — Convex Hull**
Creates the tightest convex shape enclosing all children.
```openscad
hull() {
    translate([0, 0, 0]) sphere(5);
    translate([50, 0, 0]) sphere(10);
}
// Result: a "bullet" or "pillow" shape connecting the two spheres
```
Use hull() for smooth connections between primitives.
""",
    }
    for k, v in db.items():
        if k in t:
            return v
    return f"**{topic}**: see https://openscad.org/documentation.html for the full language reference."


def _estimate_print(vol: float, infill: float = 20, layer_h: float = 0.2) -> str:
    import math
    # Rough estimates
    shell_vol = vol * 0.25  # shells ~25% of total
    fill_vol = (vol - shell_vol) * (infill / 100)
    total_plastic_cm3 = shell_vol + fill_vol
    # PLA density ~1.24 g/cm³
    mass_g = total_plastic_cm3 * 1.24
    # 1.75mm filament: cross-section = π*(0.875)²  = 2.405 mm²
    filament_cm = (total_plastic_cm3 * 1000) / 2.405  # mm³/mm² = mm, then /10 for cm
    filament_m = filament_cm / 100

    # Print speed ~40 mm/s, flow rate ~5 mm³/s
    time_h = (total_plastic_cm3 * 1000) / 5 / 3600

    return f"""\
**3D Print Estimate**
Object volume: {vol:.1f} cm³
Infill: {infill:.0f}% | Layer height: {layer_h} mm

Plastic used: ~{total_plastic_cm3:.1f} cm³  (~{mass_g:.0f} g of PLA)
Filament: ~{filament_m:.1f} m of 1.75 mm filament

Estimated print time: ~{time_h:.1f} hours (at 40 mm/s, 0.4 mm nozzle)

**Recommended slicer settings:**
- Nozzle: 215°C | Bed: 60°C (PLA)
- Layer height: {layer_h} mm
- Infill: {infill:.0f}% (gyroid or grid)
- Walls: 3 perimeters
- Support: depends on overhangs >45°

*Note: estimates are approximate — actual values depend on geometry and slicer.*
"""


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "openscad-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
