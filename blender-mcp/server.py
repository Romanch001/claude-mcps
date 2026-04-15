"""
Blender MCP Server — remote HTTP/SSE endpoint for claude.ai
Generates Blender Python (bpy) scripts and provides expert Blender guidance.
No external API required.
"""
import os
import math
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
server = Server("blender-mcp")
sse_transport = SseServerTransport("/messages/")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="generate_blender_script",
            description=(
                "Generate a runnable Blender Python (bpy) script for a given 3D modelling, "
                "automation, or rendering task. Returns executable code to paste into "
                "Blender's Text Editor and run with Alt+P."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Describe the Blender task, e.g. 'create a 5×5 grid of cubes', "
                            "'batch-export all meshes as OBJ', 'add procedural noise displacement', "
                            "'rig a simple armature for a humanoid', 'render animation to PNG sequence'."
                        )
                    },
                    "blender_version": {
                        "type": "string",
                        "description": "Target Blender version, e.g. '4.1', '3.6'. Defaults to 4.x.",
                        "default": "4.1"
                    }
                },
                "required": ["task"]
            }
        ),
        Tool(
            name="explain_blender_concept",
            description=(
                "Explain any Blender concept: modifiers, nodes (Shader/Geometry), "
                "render engines (EEVEE/Cycles/Workbench), rigging, UV unwrapping, "
                "compositing, grease pencil, physics, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "The concept to explain, e.g. 'Subdivision Surface modifier', 'Cycles vs EEVEE', 'inverse kinematics', 'UV seams'."
                    }
                },
                "required": ["concept"]
            }
        ),
        Tool(
            name="get_bpy_api_reference",
            description="Get bpy API usage examples for a module, operator, or data-block.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "API module or concept, e.g. 'bpy.data.objects', 'bpy.ops.mesh', 'bpy.context', 'modifiers', 'materials'."
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="list_shortcuts",
            description="List keyboard shortcuts for a Blender editor or mode.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "Editor/mode name: '3d viewport', 'edit mode', 'sculpt', 'shader editor', 'uv editor', 'animation', 'node editor'."
                    }
                },
                "required": ["mode"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "generate_blender_script":
        result = _gen_script(arguments.get("task", ""), arguments.get("blender_version", "4.1"))
    elif name == "explain_blender_concept":
        result = _explain(arguments.get("concept", ""))
    elif name == "get_bpy_api_reference":
        result = _api_ref(arguments.get("query", ""))
    elif name == "list_shortcuts":
        result = _shortcuts(arguments.get("mode", ""))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


def _gen_script(task: str, ver: str) -> str:
    t = task.lower()

    if "grid" in t and "cube" in t:
        return f'''\
# Blender {ver} — Create N×N grid of cubes
# Task: {task}
import bpy

ROWS, COLS, SPACING = 5, 5, 2.5

# Clear existing mesh objects
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.select_by_type(type="MESH")
bpy.ops.object.delete()

for r in range(ROWS):
    for c in range(COLS):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(r * SPACING, c * SPACING, 0))
        bpy.context.active_object.name = f"Cube_{{r}}_{{c}}"

print(f"Created {{ROWS * COLS}} cubes in a {{ROWS}}×{{COLS}} grid")
'''

    if "export" in t and "obj" in t:
        return f'''\
# Blender {ver} — Batch-export every mesh as a separate .obj
# Task: {task}
import bpy, os

EXPORT_DIR = bpy.path.abspath("//obj_exports/")
os.makedirs(EXPORT_DIR, exist_ok=True)

for obj in list(bpy.data.objects):
    if obj.type != "MESH":
        continue
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    fp = os.path.join(EXPORT_DIR, obj.name + ".obj")
    bpy.ops.export_scene.obj(filepath=fp, use_selection=True)
    print(f"Exported: {{fp}}")

print("Done!")
'''

    if "material" in t or "shader" in t:
        return f'''\
# Blender {ver} — Create a Principled BSDF material and apply to selection
# Task: {task}
import bpy

mat = bpy.data.materials.new("GeneratedMat")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()

bsdf = nodes.new("ShaderNodeBsdfPrincipled")
bsdf.location = (0, 0)
bsdf.inputs["Base Color"].default_value = (0.2, 0.5, 0.8, 1.0)
bsdf.inputs["Metallic"].default_value = 0.0
bsdf.inputs["Roughness"].default_value = 0.4

out = nodes.new("ShaderNodeOutputMaterial")
out.location = (300, 0)
links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

for obj in bpy.context.selected_objects:
    if obj.type == "MESH":
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        print(f"Applied to {{obj.name}}")
'''

    if "render" in t or "animation" in t:
        return f'''\
# Blender {ver} — Render animation frames to PNG sequence
# Task: {task}
import bpy

scene = bpy.context.scene
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = bpy.path.abspath("//renders/frame_")
scene.render.engine = "CYCLES"  # or "BLENDER_EEVEE_NEXT"
scene.cycles.samples = 128
scene.frame_start = 1
scene.frame_end = 250

# Render all frames
bpy.ops.render.render(animation=True)
print("Render complete — frames saved to //renders/")
'''

    # Generic template
    return f'''\
# Blender {ver} — {task}
# Paste into Text Editor, then Run Script (Alt+P)
import bpy

scene = bpy.context.scene
active = bpy.context.active_object
selected = bpy.context.selected_objects

print(f"Scene: {{scene.name}}")
print(f"Active: {{active.name if active else 'none'}}")
print(f"Selected: {{[o.name for o in selected]}}")

# --- Implement your logic here ---
# Common patterns:
#   Add object:       bpy.ops.mesh.primitive_cube_add(location=(0,0,0))
#   Delete selected:  bpy.ops.object.delete()
#   Move object:      obj.location = (x, y, z)
#   Access by name:   obj = bpy.data.objects["Cube"]
#   Apply modifier:   bpy.ops.object.modifier_apply(modifier="Subdivision")
#   Enter edit mode:  bpy.ops.object.mode_set(mode="EDIT")
#   Update viewport:  bpy.context.view_layer.update()

print("Script finished")
'''


def _explain(concept: str) -> str:
    c = concept.lower()
    db = {
        "subdivision": """\
**Subdivision Surface Modifier**
Divides mesh faces into smaller faces for a smoother appearance.

Settings:
- **Catmull-Clark** (default): smooths and rounds — use for organic shapes
- **Simple**: divides without smoothing — preserves sharp geometry
- **Levels Viewport / Render**: keep viewport at 2-3, render at 3-6

Tips:
- Add edge loops near sharp corners to control rounding
- Crease edges with Shift+E (value 1.0 = perfectly sharp)
- Apply (▾ button) to bake subdivision into real geometry
""",
        "eevee": """\
**EEVEE vs Cycles vs Workbench**

| Engine | Speed | Quality | Best for |
|--------|-------|---------|----------|
| **Workbench** | Fastest | Lowest | Modelling previews |
| **EEVEE** | Real-time | Good | Stylised, games, fast turnarounds |
| **Cycles** | Slow | Photorealistic | Product vis, VFX, film |

EEVEE uses rasterisation (approximations); Cycles path-traces light physically.
Enable *Screen Space Reflections* in EEVEE for reflective surfaces.
""",
        "armature": """\
**Armature & Rigging**

1. Add Armature: Shift+A → Armature
2. Edit Mode (Tab): position bones inside the mesh
3. Parent mesh→armature: select mesh, Shift-select armature, Ctrl+P → With Automatic Weights
4. Pose Mode (Ctrl+Tab on armature): animate bones

Useful constraints: IK (Inverse Kinematics), Copy Rotation, Limit Rotation, Damped Track.
""",
        "geometry node": """\
**Geometry Nodes**
Procedural modelling system (Blender 3.0+).

Workflow:
1. Select object → Add Modifier → Geometry Nodes → New
2. Node editor opens; build a graph from left (input) to right (output)
3. Common nodes: Set Position, Instance on Points, Distribute Points on Faces, Join Geometry, Transform Geometry

Geometry Nodes can replace mesh modifiers and enable non-destructive, parametric design.
""",
    }
    for k, v in db.items():
        if k in c:
            return v
    return (
        f"**{concept}**\n\nBlender covers this under its official manual:\n"
        f"https://docs.blender.org/manual/en/latest/ — search for '{concept}'.\n\n"
        "For Q&A, visit https://blender.stackexchange.com/"
    )


def _api_ref(query: str) -> str:
    q = query.lower()
    if "bpy.data.object" in q or "objects" in q:
        return """\
```python
import bpy

# Iterate all objects
for obj in bpy.data.objects:
    print(obj.name, obj.type)  # MESH, CURVE, LIGHT, CAMERA …

# Access by name
obj = bpy.data.objects["Cube"]
obj.location = (1, 2, 3)
obj.scale    = (2, 2, 2)
obj.rotation_euler[2] = 1.5708  # 90° in radians

# Delete programmatically
bpy.data.objects.remove(obj, do_unlink=True)
```
"""
    if "bpy.ops.mesh" in q or "primitive" in q:
        return """\
```python
import bpy
# Add primitives (run in Object Mode)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0,0,0))
bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=32, ring_count=16)
bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=2)
bpy.ops.mesh.primitive_plane_add(size=10)
bpy.ops.mesh.primitive_torus_add(major_radius=1, minor_radius=0.25)

# Edit-mode ops (must be in EDIT mode)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0,0,1)})
bpy.ops.mesh.loop_cut(number_cuts=2)
bpy.ops.object.mode_set(mode='OBJECT')
```
"""
    if "material" in q or "node" in q:
        return """\
```python
import bpy

mat = bpy.data.materials.new("MyMat")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()

bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
out  = nt.nodes.new("ShaderNodeOutputMaterial")
out.location = (300, 0)
nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

# Assign to active object
bpy.context.active_object.data.materials.append(mat)
```
"""
    return (
        f"# bpy reference: {query}\n"
        "Full API docs: https://docs.blender.org/api/current/\n\n"
        "```python\nimport bpy\n# explore interactively:\ndir(bpy.data)\ndir(bpy.ops)\ndir(bpy.context)\n```"
    )


def _shortcuts(mode: str) -> str:
    m = mode.lower()
    if "edit" in m:
        return """\
**Edit Mode Shortcuts**
- 1/2/3 — vertex / edge / face select mode
- A — select all | Alt+A — deselect
- E — extrude | I — inset | Ctrl+B — bevel
- Ctrl+R — loop cut (scroll to add cuts)
- K — knife tool | F — fill face/edge
- G/R/S — grab / rotate / scale (+ X/Y/Z to constrain)
- GG — edge slide | V — rip
- M — merge | Alt+M — merge dialog
- Shift+N — recalculate normals
- Alt+click — select loop | Ctrl+click — select path
- O — proportional editing (scroll to change falloff)
"""
    if "sculpt" in m:
        return """\
**Sculpt Mode Shortcuts**
- F — brush radius | Shift+F — brush strength
- Ctrl (hold) — subtract mode
- Shift (hold) — smooth temporarily
- X — toggle X-axis symmetry
- B — box mask | M — mask fill | Alt+M — clear mask
- Tab — toggle in/out of Sculpt Mode
- Numpad 1/3/7 — front/side/top view
"""
    if "shader" in m or "node" in m:
        return """\
**Shader / Node Editor Shortcuts**
- Shift+A — add node
- Ctrl+J — frame selected nodes
- M — mute node
- Ctrl+G — make node group | Alt+G — ungroup
- H — hide node sockets
- F — link nodes (drag from socket)
- Ctrl+Shift+click — quick preview (Cycles viewer)
"""
    # default: 3D viewport / object mode
    return """\
**3D Viewport — Object Mode Shortcuts**
Navigation: MMB orbit | Shift+MMB pan | Scroll zoom
Numpad: 1 front | 3 right | 7 top | 5 perspective toggle | 0 camera

Objects: G move | R rotate | S scale (+ X/Y/Z constrain)
         A select all | B box select | C circle select
         Shift+A add | X delete | Ctrl+J join | Alt+D linked duplicate

Modes: Tab edit/object | Ctrl+Tab mode pie
Panels: N side panel | T toolbar | F9 last op properties
"""


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "blender-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
