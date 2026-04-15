"""
FreeCAD MCP Server — remote HTTP/SSE endpoint for claude.ai
Generates FreeCAD Python scripting code and provides CAD guidance.
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
server = Server("freecad-mcp")
sse_transport = SseServerTransport("/messages/")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="generate_freecad_script",
            description=(
                "Generate a FreeCAD Python script for a parametric CAD task. "
                "Returns executable Python code runnable in FreeCAD's console or macro editor."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Describe the CAD task, e.g. 'create a box 100×50×25 mm with "
                            "a 10mm hole through it', 'build an L-bracket', "
                            "'revolve a profile to make a vase shape', 'export to STEP file'."
                        )
                    },
                    "workbench": {
                        "type": "string",
                        "description": "FreeCAD workbench to use: 'Part', 'PartDesign', 'Sketcher', 'Draft', 'Mesh'. Default: PartDesign.",
                        "default": "PartDesign"
                    }
                },
                "required": ["task"]
            }
        ),
        Tool(
            name="explain_freecad_workbench",
            description="Explain what a FreeCAD workbench does and when to use it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workbench": {
                        "type": "string",
                        "description": "Workbench name: 'Part', 'PartDesign', 'Sketcher', 'Draft', 'FEM', 'Path', 'TechDraw', 'Mesh'."
                    }
                },
                "required": ["workbench"]
            }
        ),
        Tool(
            name="calculate_cad_dimensions",
            description="Calculate dimensions, volumes, surface areas, or tolerances for common mechanical shapes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "shape": {
                        "type": "string",
                        "description": "Shape type: 'box', 'cylinder', 'sphere', 'cone', 'torus', 'gear'."
                    },
                    "dimensions": {
                        "type": "object",
                        "description": "Key-value pairs of dimensions in mm, e.g. {\"length\": 100, \"width\": 50, \"height\": 25}."
                    }
                },
                "required": ["shape", "dimensions"]
            }
        ),
        Tool(
            name="get_freecad_constraints",
            description="Get a list of Sketcher constraints and when to use each for parametric design.",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {
                        "type": "string",
                        "description": "Optional context: 'horizontal', 'vertical', 'concentric', 'equal', 'symmetric', 'fix', 'tangent'."
                    }
                },
                "required": []
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "generate_freecad_script":
        result = _gen_script(arguments.get("task", ""), arguments.get("workbench", "PartDesign"))
    elif name == "explain_freecad_workbench":
        result = _explain_wb(arguments.get("workbench", ""))
    elif name == "calculate_cad_dimensions":
        result = _calc_dims(arguments.get("shape", ""), arguments.get("dimensions", {}))
    elif name == "get_freecad_constraints":
        result = _constraints(arguments.get("context", ""))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


def _gen_script(task: str, wb: str) -> str:
    t = task.lower()

    if "box" in t or "bracket" in t:
        return f'''\
# FreeCAD {wb} Script: {task}
import FreeCAD as App
import Part

doc = App.newDocument("GeneratedPart")

# --- Parametric Box ---
length, width, height = 100.0, 50.0, 25.0  # mm — adjust as needed
box = doc.addObject("Part::Box", "MainBox")
box.Length = length
box.Width  = width
box.Height = height

{"# --- Through Hole ---" if "hole" in t else ""}
{"hole = doc.addObject('Part::Cylinder', 'Hole')" if "hole" in t else ""}
{"hole.Radius = 5.0  # mm radius" if "hole" in t else ""}
{"hole.Height = height + 2" if "hole" in t else ""}
{"hole.Placement.Base = App.Vector(length/2, width/2, -1)" if "hole" in t else ""}
{"cut = doc.addObject('Part::Cut', 'BoxWithHole')" if "hole" in t else ""}
{"cut.Base = box" if "hole" in t else ""}
{"cut.Tool = hole" if "hole" in t else ""}

doc.recompute()
App.Console.PrintMessage("Part created successfully\\n")

# Export to STEP
# import ImportGui
# ImportGui.export([doc.ActiveObject], "/tmp/output.step")
'''

    if "vase" in t or "revolve" in t:
        return f'''\
# FreeCAD Script: Revolve a profile to create a vase
import FreeCAD as App
import Part, math

doc = App.newDocument("VasePart")

# Define the 2D profile as a wire (cross-section in XZ plane)
pts = [
    App.Vector(0, 0, 0),
    App.Vector(30, 0, 0),
    App.Vector(25, 0, 20),
    App.Vector(35, 0, 60),
    App.Vector(20, 0, 100),
    App.Vector(20, 0, 110),
    App.Vector(0, 0, 110),
]
edges = []
for i in range(len(pts) - 1):
    edges.append(Part.LineSegment(pts[i], pts[i+1]).toShape())

wire = Part.Wire(edges)

# Revolve around Z-axis 360°
axis = App.Vector(0, 0, 1)
shape = wire.revolve(App.Vector(0,0,0), axis, 360)

part = doc.addObject("Part::Feature", "Vase")
part.Shape = shape
doc.recompute()
App.Console.PrintMessage("Vase created\\n")
'''

    if "export" in t or "step" in t:
        return f'''\
# FreeCAD Script: Export active part to STEP file
import FreeCAD as App
import ImportGui, os

doc = App.ActiveDocument
if not doc:
    raise RuntimeError("No active document")

output_path = os.path.expanduser("~/freecad_export.step")
active_objects = [obj for obj in doc.Objects if hasattr(obj, "Shape")]

if not active_objects:
    raise RuntimeError("No Part objects found in document")

ImportGui.export(active_objects, output_path)
App.Console.PrintMessage(f"Exported to: {{output_path}}\\n")
'''

    return f'''\
# FreeCAD Script: {task}
import FreeCAD as App
import Part

doc = App.newDocument("NewPart")

# --- Start modelling here ---
# PartDesign workflow (recommended for solid parts):
#   body = doc.addObject("PartDesign::Body", "Body")
#   sketch = body.newObject("Sketcher::SketchObject", "Sketch")
#   sketch.Placement = App.Placement(App.Vector(0,0,0), App.Rotation(0,0,0,1))
#   # Add constraints via sketch.addConstraint(...)
#   pad = body.newObject("PartDesign::Pad", "Pad")
#   pad.Profile = sketch
#   pad.Length = 20.0
#
# Part workflow (CSG booleans):
#   box = doc.addObject("Part::Box", "Box")
#   box.Length, box.Width, box.Height = 100, 50, 25
#   cyl = doc.addObject("Part::Cylinder", "Cyl")
#   cut = doc.addObject("Part::Cut", "Result")
#   cut.Base, cut.Tool = box, cyl

doc.recompute()
App.Console.PrintMessage("Done\\n")
'''


def _explain_wb(wb: str) -> str:
    w = wb.lower()
    db = {
        "part": """\
**Part Workbench**
Classic CSG (Constructive Solid Geometry) approach.
- Create primitives (Box, Cylinder, Sphere, Cone, Torus)
- Combine with Boolean operations: Union, Cut, Common (intersection)
- Best for: quick shapes, imported geometry manipulation
- Limitation: not parametric — editing history is linear
""",
        "partdesign": """\
**PartDesign Workbench** ← recommended for most mechanical parts
Feature-based, fully parametric modelling inside a *Body*.
1. Sketcher → draw 2D profile with constraints
2. Pad (extrude), Pocket (cut), Revolve, Loft, Sweep
3. Fillet, Chamfer, Draft, Thickness
4. Each feature is editable and the model updates downstream

Best for: mechanical parts that need revision, manufacturing drawings.
""",
        "sketcher": """\
**Sketcher Workbench**
Draw constrained 2D profiles used as input for PartDesign/Part.
- Geometric constraints: coincident, parallel, perpendicular, tangent
- Dimensional constraints: distance, angle, radius
- Fully constrained sketch = green (zero degrees of freedom)
- Under-constrained = white/yellow, over-constrained = red

Tip: always fully constrain sketches before padding/pocketing.
""",
        "draft": """\
**Draft Workbench**
2D/2.5D drawing and annotation.
- Lines, polylines, circles, arcs, polygons
- Array (rectangular, polar, path)
- Snap to grid and objects
- Best for: floor plans, technical 2D drawings, quick 3D layouts
- Exports to DXF/DWG
""",
        "fem": """\
**FEM Workbench** (Finite Element Analysis)
- Define material properties, mesh, boundary conditions, loads
- Solvers: CalculiX (structural), Elmer (multiphysics), Z88
- Results: stress, displacement, eigenfrequency
- Requires a meshed solid body as input
""",
    }
    for k, v in db.items():
        if k in w:
            return v
    return f"**{wb} Workbench** — see https://wiki.freecad.org/Workbenches for full documentation."


def _calc_dims(shape: str, dims: dict) -> str:
    import math
    s = shape.lower()
    try:
        if s == "box":
            l, w, h = float(dims.get("length", 0)), float(dims.get("width", 0)), float(dims.get("height", 0))
            vol = l * w * h
            sa = 2 * (l*w + l*h + w*h)
            return f"Box {l}×{w}×{h} mm\nVolume: {vol:.2f} mm³ = {vol/1000:.4f} cm³\nSurface area: {sa:.2f} mm²"
        if s in ("cylinder", "cyl"):
            r, h = float(dims.get("radius", 0)), float(dims.get("height", 0))
            vol = math.pi * r**2 * h
            sa = 2*math.pi*r*(r+h)
            return f"Cylinder r={r} h={h} mm\nVolume: {vol:.2f} mm³\nSurface area: {sa:.2f} mm²"
        if s == "sphere":
            r = float(dims.get("radius", 0))
            vol = (4/3)*math.pi*r**3
            sa = 4*math.pi*r**2
            return f"Sphere r={r} mm\nVolume: {vol:.2f} mm³\nSurface area: {sa:.2f} mm²"
        if s == "cone":
            r, h = float(dims.get("radius", 0)), float(dims.get("height", 0))
            slant = math.sqrt(r**2 + h**2)
            vol = (1/3)*math.pi*r**2*h
            sa = math.pi*r*(r+slant)
            return f"Cone r={r} h={h} mm\nVolume: {vol:.2f} mm³\nSurface area: {sa:.2f} mm²"
    except Exception as e:
        return f"Calculation error: {e}"
    return f"Shape '{shape}' not yet supported. Supported: box, cylinder, sphere, cone."


def _constraints(context: str = "") -> str:
    return """\
**Sketcher Constraints Reference**

Geometric:
- **Coincident** — two points share location
- **Collinear** — two lines on same infinite line
- **Horizontal / Vertical** — line is purely H or V
- **Parallel** — two lines run in same direction
- **Perpendicular** — two lines are 90° to each other
- **Tangent** — line/arc meets arc/circle smoothly
- **Equal** — two edges have same length/radius
- **Symmetric** — geometry mirrors across a line/point
- **Block** — fix element in place

Dimensional:
- **Fix Horizontal Distance / Fix Vertical Distance**
- **Fix Distance** (length of edge)
- **Fix Radius / Fix Diameter**
- **Fix Angle** (between two lines)
- **Fix Point onto Object** (point constrained to line/circle)

Tip: A fully-constrained sketch shows all white geometry (zero DoF).
"""


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "freecad-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
