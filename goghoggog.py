
bl_info = {
    "name": "GHG Importer (Skeleton + Skinned parts)",
    "author": "Queue Wafer",
    "version": (2, 0, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > GHG Skeleton + Skinned parts (.ghg)",
    "description": "Import a GHG skeleton and selected mesh parts with blend weights from extractor output",
    "category": "Import-Export",
}

import math
import os
import re
import subprocess
from pathlib import Path
from struct import unpack

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper, axis_conversion
from mathutils import Matrix, Quaternion, Vector


ROOT_PARENT_SENTINEL = 255
EXTRACTOR_NAMES = (
    "ExtractDx11MESHFix.exe",
    "ExtractNxgMESHFix.exe",
    "ExtractDx11MESH.exe",
    "ExtractNxgMESH.exe",
)

ATTR_LENGTHS = {
    "4float": 16,
    "3float": 12,
    "2float": 8,
    "4half": 8,
    "4mini": 4,
    "4char": 4,
    "2half": 4,
}


def _addon_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except Exception:
        return Path.cwd()


def _safe_vec3(v, default=(0.0, 0.0, 0.0)):
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return Vector(default)
    return Vector((float(v[0]), float(v[1]), float(v[2])))


def _safe_quat(v):
    # Input is [x, y, z, w]
    if not isinstance(v, (list, tuple)) or len(v) < 4:
        return Quaternion((1.0, 0.0, 0.0, 0.0))
    return Quaternion((float(v[3]), float(v[0]), float(v[1]), float(v[2])))


def _safe_scale(v):
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return Vector((1.0, 1.0, 1.0))
    return Vector((float(v[0]), float(v[1]), float(v[2])))


def _vec_len(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _quat_from_mat3(m):
    trace = m[0][0] + m[1][1] + m[2][2]

    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s

    return (x, y, z, w)


def _decompose_matrix(mat, column_major=False):
    if column_major:
        px, py, pz = mat[3][0], mat[3][1], mat[3][2]
        xaxis = [mat[0][0], mat[1][0], mat[2][0]]
        yaxis = [mat[0][1], mat[1][1], mat[2][1]]
        zaxis = [mat[0][2], mat[1][2], mat[2][2]]
    else:
        px, py, pz = mat[0][3], mat[1][3], mat[2][3]
        xaxis = [mat[0][0], mat[0][1], mat[0][2]]
        yaxis = [mat[1][0], mat[1][1], mat[1][2]]
        zaxis = [mat[2][0], mat[2][1], mat[2][2]]

    sx = _vec_len(xaxis)
    sy = _vec_len(yaxis)
    sz = _vec_len(zaxis)

    if sx:
        xaxis = [v / sx for v in xaxis]
    if sy:
        yaxis = [v / sy for v in yaxis]
    if sz:
        zaxis = [v / sz for v in zaxis]

    rot3 = [
        [xaxis[0], xaxis[1], xaxis[2]],
        [yaxis[0], yaxis[1], yaxis[2]],
        [zaxis[0], zaxis[1], zaxis[2]],
    ]
    quat = _quat_from_mat3(rot3)
    return (px, py, pz), quat, (sx, sy, sz)


def _parse_part_selection(text, max_part_index):
    if not text or not text.strip():
        return []

    parts = []
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                start = int(a)
                end = int(b)
                if end < start:
                    start, end = end, start
                parts.extend(range(start, end + 1))
        elif token.isdigit():
            parts.append(int(token))

    result = []
    seen = set()
    for p in parts:
        if 0 <= p <= max_part_index and p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _find_extractor(extractor_directory: Path) -> Path:
    for name in EXTRACTOR_NAMES:
        path = extractor_directory / name
        if path.exists():
            return path
    raise FileNotFoundError(
        "Could not find an extractor executable in the selected folder. "
        "Expected one of: " + ", ".join(EXTRACTOR_NAMES)
    )


def _run_extractor(extractor_directory: Path, ghg_file: Path) -> str:
    exe = _find_extractor(extractor_directory)
    process = subprocess.Popen(
        [str(exe), str(ghg_file)],
        cwd=str(extractor_directory),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    output = []
    prompt_seen = False

    assert process.stdout is not None
    for line in process.stdout:
        output.append(line)
        if "Press enter to close..." in line:
            prompt_seen = True
            if process.stdin:
                try:
                    process.stdin.write("\n")
                    process.stdin.flush()
                except Exception:
                    pass

    try:
        process.wait(timeout=30)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass

    if process.returncode not in (0, None) and not prompt_seen:
        # Keep the output; the caller can inspect it if needed.
        pass

    return "".join(output)


def _get_string_index(substring, index=0, string=""):
    return string.replace(substring, "," * len(substring), index).index(substring)


def _get_extractor_value(substring, index=0, offset=0, length=8, integer_output=True, string=""):
    value_offset = _get_string_index(substring, index, string=string) + len(substring) + offset
    value = string[value_offset:value_offset + length]
    return int(value, 16) if integer_output else value


def _get_file_offset(substring, index=0, string=""):
    substring_index = _get_string_index(substring, index, string=string)
    offset_start = string.rfind("\n", 0, substring_index) + 1
    return int(string[offset_start:offset_start + 8], 16)


def _read_ghg_bytes(ghg_file: Path) -> bytes:
    return ghg_file.read_bytes()


def _parse_bones_from_ghg(ghg_bytes: bytes):
    bones = []
    logh_index = 0
    logh_offset = ghg_bytes.replace(b"LOGH", b"AAAA", logh_index).index(b"LOGH")
    logh_version = int(ghg_bytes[logh_offset + 4:logh_offset + 8].hex(), 16)

    if logh_version in (16, 17):
        rotv_data_start_offset = logh_offset + 4 + 4 + 4 + 4
        current_bone_till_end = ghg_bytes[rotv_data_start_offset:]
        rotv_items_count = int(ghg_bytes[logh_offset + 12:logh_offset + 16].hex(), 16)

        for k in range(rotv_items_count):
            current_bone = {
                "name": "name",
                "idx": k,
                "parent_idx": ROOT_PARENT_SENTINEL,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0],
            }

            namelen = int(current_bone_till_end[:2].hex(), 16) - 1
            current_bone["name"] = current_bone_till_end[2:2 + namelen].decode("utf-8", errors="replace")

            c = 2 + namelen + 1 + 64
            for _ in range(3):
                c += 4

            current_bone["parent_idx"] = current_bone_till_end[c]
            bones.append(current_bone)
            current_bone_till_end = current_bone_till_end[c + 2:]

        c = 8
        for k in range(rotv_items_count):
            matrix = []
            for _ in range(4):
                row = []
                for _ in range(4):
                    row.append(unpack(">f", current_bone_till_end[c:c + 4])[0])
                    c += 4
                matrix.append(row)
            pos, rot, scl = _decompose_matrix(matrix, column_major=True)
            bones[k]["position"] = list(pos)
            bones[k]["rotation"] = list(rot)
            bones[k]["scale"] = list(scl)

    elif logh_version in (4, 9, 10):
        default_string_offset = ghg_bytes.index(b"default_string")
        name_strings = ghg_bytes[default_string_offset:]

        rotv_data_start_offset = logh_offset + 4 + 4 + 4
        current_bone_till_end = ghg_bytes[rotv_data_start_offset:]
        rotv_items_count = int(ghg_bytes[logh_offset + 8:logh_offset + 12].hex(), 16)

        for k in range(rotv_items_count):
            current_bone = {
                "name": "name",
                "idx": k,
                "parent_idx": ROOT_PARENT_SENTINEL,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0],
            }

            name_string_offset = int(current_bone_till_end[:4].hex(), 16)
            current_bone["name"] = name_strings[name_string_offset:].split(b"\x00", 1)[0].decode("utf-8", errors="replace")

            c = 4 + 64
            for _ in range(3):
                c += 4

            current_bone["parent_idx"] = current_bone_till_end[c]
            bones.append(current_bone)
            current_bone_till_end = current_bone_till_end[c + 2:]

        c = 4
        for k in range(rotv_items_count):
            matrix = []
            for _ in range(4):
                row = []
                for _ in range(4):
                    row.append(unpack(">f", current_bone_till_end[c:c + 4])[0])
                    c += 4
                matrix.append(row)
            pos, rot, scl = _decompose_matrix(matrix, column_major=True)
            bones[k]["position"] = list(pos)
            bones[k]["rotation"] = list(rot)
            bones[k]["scale"] = list(scl)
    else:
        raise ValueError(f"Unsupported LOGH version: {logh_version}")

    return bones


def _parse_blend_and_part_data(extractor_output: str, ghg_bytes: bytes, bones: list):
    number_of_parts = _get_extractor_value("Number of Parts: 0x", string=extractor_output)

    vertexlists = {}
    skinned_vertexlists = []

    c = 0
    while True:
        try:
            vertex_list_id = _get_extractor_value("New Vertex List 0x", c, length=4, string=extractor_output)
        except ValueError:
            break

        number_of_vertices = _get_extractor_value("Number of Vertices: ", c, string=extractor_output)
        vertexlist_info_raw = extractor_output[
            _get_string_index("New Vertex List 0x", c, string=extractor_output):
            _get_string_index("Number of Vertices: ", c, string=extractor_output)
        ]

        if "blendWeight0" not in vertexlist_info_raw:
            c += 1
            continue

        attr_lengths_sum = 0
        for key, length in ATTR_LENGTHS.items():
            attr_lengths_sum += vertexlist_info_raw.count(key) * length

        vertexlist_bytes = ghg_bytes[_get_file_offset("Number of Vertices: ", c, string=extractor_output):]
        current_vertexlist = []

        for vertex in range(number_of_vertices):
            current_blendindices = []
            current_blendweights = []
            current_blendindices_offset = (vertex + 1) * attr_lengths_sum - 8

            for blendindex in vertexlist_bytes[current_blendindices_offset:current_blendindices_offset + 4]:
                current_blendindices.append(int(blendindex))

            for blendweight in vertexlist_bytes[current_blendindices_offset + 4:current_blendindices_offset + 8]:
                current_blendweights.append(float(blendweight) / 255.0)

            current_vertexlist.append({
                "blendindices": current_blendindices,
                "blendweights": current_blendweights,
            })

        vertexlists[vertex_list_id] = current_vertexlist
        skinned_vertexlists.append(vertex_list_id)
        c += 1

    parts = {}
    for i in range(number_of_parts):
        start_string_index = extractor_output.index(f"Part {i:#010x}")
        if i == number_of_parts-1:
            end_string_index = -1
        else:
            end_string_index = extractor_output.index(f"Part {i+1:#010x}")
        
        current_part_info = (
            extractor_output[
                start_string_index:
                end_string_index
            ]
        )

        part_vertexlist_id = None
        for j in skinned_vertexlists:
            if (
                f"New Vertex List {j:#06x}" in current_part_info or
                f"Vertex List Reference to {j:#06x}" in current_part_info
            ):
                part_vertexlist_id = j
                break

        if part_vertexlist_id is None:
            continue

        offset_vertices = _get_extractor_value(
            "Offset Vertices: 0x",
            string=current_part_info
        )
        number_vertices = _get_extractor_value(
            "Number Vertices: 0x",
            string=current_part_info
        )
        bone_indices_raw = _get_extractor_value(
            "Number Vertices: 0x",
            offset=8 + 1 + 8 + 5,
            length=80,
            integer_output=False,
            string=current_part_info,
        )

        # bone_indices = [int(x, 16) for x in re.findall(r"[0-9A-Fa-f]{2}", bone_indices_raw)]

        bone_indices = []
        for j in range((len(bone_indices_raw)//3)):
            bone_idx = j*3
            bone_indices.append(
                int(
                    bone_indices_raw[bone_idx:bone_idx+2],
                    16
                )
            )

        part_vertices = vertexlists[part_vertexlist_id][offset_vertices:offset_vertices + number_vertices]

        current_part = []

        for vertex in part_vertices:
            new_blendindices = []
            for blendindex in vertex["blendindices"]:
                if blendindex != 255 and blendindex < len(bone_indices) and bone_indices[blendindex] != 255:
                    bone_idx = bone_indices[blendindex]
                    if bone_idx < len(bones):
                        new_blendindices.append(bones[bone_idx]["name"])
                    else:
                        new_blendindices.append("None")
                else:
                    new_blendindices.append("None")

            current_part.append({
                "blendnames": new_blendindices,
                "blendweights": vertex["blendweights"],
            })

        parts[i] = current_part

    return parts


def _compute_world_matrices(bones, use_scale=True):
    by_idx = {int(b.get("idx", i)): b for i, b in enumerate(bones)}
    world = {}

    def resolve(idx):
        if idx in world:
            return world[idx]
        bone = by_idx[idx]
        local = _build_local_matrix(bone, use_scale=use_scale)
        parent_idx = int(bone.get("parent_idx", ROOT_PARENT_SENTINEL))
        if parent_idx == ROOT_PARENT_SENTINEL or parent_idx not in by_idx:
            world[idx] = local
        else:
            world[idx] = resolve(parent_idx) @ local
        return world[idx]

    for idx in by_idx:
        resolve(idx)

    return by_idx, world


def _build_local_matrix(bone, use_scale=True):
    pos = _safe_vec3(bone.get("position", [0.0, 0.0, 0.0]))
    rot = _safe_quat(bone.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    scl = _safe_scale(bone.get("scale", [1.0, 1.0, 1.0])) if use_scale else Vector((1.0, 1.0, 1.0))

    t = Matrix.Translation(pos)
    r = rot.to_matrix().to_4x4()
    s = Matrix.Diagonal((scl.x, scl.y, scl.z, 1.0))
    return t @ r @ s


def _bone_direction_from_children(idx, bones_by_idx, world_mats):
    bone = bones_by_idx[idx]
    child_positions = []
    for child in bones_by_idx.values():
        if int(child.get("parent_idx", ROOT_PARENT_SENTINEL)) == idx:
            cidx = int(child.get("idx"))
            child_positions.append(world_mats[cidx].translation)

    if child_positions:
        avg = Vector((0.0, 0.0, 0.0))
        for p in child_positions:
            avg += p
        avg /= len(child_positions)
        return avg - world_mats[idx].translation

    local_rot = _safe_quat(bone.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    if local_rot.angle != 0.0:
        return local_rot @ Vector((0.0, 1.0, 0.0))
    return Vector((0.0, 1.0, 0.0))


def _create_armature_from_bones(bones, armature_name="ImportedArmature", use_scale=True, source_forward='-Z', source_up='Y'):
    axis_fix = axis_conversion(from_forward=source_forward, from_up=source_up).to_4x4()
    by_idx, world_mats = _compute_world_matrices(bones, use_scale=use_scale)

    arm_data = bpy.data.armatures.new(armature_name)
    arm_obj = bpy.data.objects.new(armature_name, arm_data)
    bpy.context.collection.objects.link(arm_obj)

    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = {}
    for bone in bones:
        idx = int(bone.get("idx"))
        name = str(bone.get("name", f"Bone_{idx}"))
        edit_bones[idx] = arm_data.edit_bones.new(name)

    for bone in bones:
        idx = int(bone.get("idx"))
        parent_idx = int(bone.get("parent_idx", ROOT_PARENT_SENTINEL))
        eb = edit_bones[idx]

        wm = axis_fix @ world_mats[idx]
        head = wm.translation

        direction = _bone_direction_from_children(idx, by_idx, world_mats)
        if direction.length < 1e-8:
            direction = Vector((0.0, 0.1, 0.0))
        direction = direction.normalized() * 0.1

        eb.head = head
        eb.tail = head + direction

        if parent_idx != ROOT_PARENT_SENTINEL and parent_idx in edit_bones:
            eb.parent = edit_bones[parent_idx]

    for bone in bones:
        idx = int(bone.get("idx"))
        eb = edit_bones[idx]
        child_heads = []
        for child in bones:
            if int(child.get("parent_idx", ROOT_PARENT_SENTINEL)) == idx:
                cidx = int(child.get("idx"))
                child_heads.append((axis_fix @ world_mats[cidx]).translation)

        if child_heads:
            avg = Vector((0.0, 0.0, 0.0))
            for p in child_heads:
                avg += p
            avg /= len(child_heads)
            if (avg - eb.head).length > 1e-8:
                eb.tail = avg

    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


def _find_ghg_related_file(ghg_file: Path, name: str):
    base_dir = ghg_file.parent
    candidate1 = base_dir / name
    candidate2 = base_dir / ghg_file.stem / name

    if candidate1.exists() and candidate2.exists():
        return candidate1 if candidate1.stat().st_mtime >= candidate2.stat().st_mtime else candidate2
    if candidate1.exists():
        return candidate1
    if candidate2.exists():
        return candidate2
    return None


def _import_obj_mesh(filepath: Path):
    before = set(bpy.data.objects)

    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=str(filepath))
    else:
        bpy.ops.import_scene.obj(filepath=str(filepath))

    after = set(bpy.data.objects)
    imported = [obj for obj in (after - before) if obj.type == "MESH"]
    if not imported:
        imported = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    return imported


def _assign_weights_to_mesh(mesh_obj, armature_obj, part_vertices):
    mesh = mesh_obj.data
    if len(mesh.vertices) != len(part_vertices):
        raise ValueError(
            f"Vertex count mismatch for {mesh_obj.name}: "
            f"mesh has {len(mesh.vertices)}, part data has {len(part_vertices)}"
        )

    for vertex in part_vertices:
        for bone_name in vertex["blendnames"]:
            if bone_name and bone_name != "None" and mesh_obj.vertex_groups.get(bone_name) is None:
                mesh_obj.vertex_groups.new(name=bone_name)

    for vidx, vertex in enumerate(part_vertices):
        for bone_name, weight in zip(vertex["blendnames"], vertex["blendweights"]):
            if not bone_name or bone_name == "None" or weight <= 0.0:
                continue
            group = mesh_obj.vertex_groups.get(bone_name)
            if group is None:
                group = mesh_obj.vertex_groups.new(name=bone_name)
            group.add([vidx], float(weight), "REPLACE")

    if mesh_obj.parent != armature_obj:
        mesh_obj.parent = armature_obj
    mesh_obj.matrix_parent_inverse = armature_obj.matrix_world.inverted_safe()

    arm_mod = mesh_obj.modifiers.get("Armature")
    if arm_mod is None:
        arm_mod = mesh_obj.modifiers.new(name="Armature", type="ARMATURE")
    arm_mod.object = armature_obj


def _normalize_dir_path(path_text: str):
    if not path_text:
        return None
    p = Path(path_text).expanduser()
    return p


def _selected_armature_from_context(context):
    active = getattr(context, "active_object", None)
    if active is not None and active.type == "ARMATURE":
        return active

    for obj in getattr(context, "selected_objects", []):
        if obj.type == "ARMATURE":
            return obj

    return None


def _armature_bones_as_list(armature_obj):
    return [{"name": bone.name} for bone in armature_obj.data.bones]


class IMPORT_OT_ghg_skeleton_parts(Operator, ImportHelper):
    bl_idname = "import_scene.ghg_skeleton_parts"
    bl_label = "Import GHG Skeleton + Skinned parts"
    bl_description = "Import a GHG skeleton and selected mesh parts with blend weights"
    bl_options = {"UNDO"}

    filename_ext = ".ghg"
    filter_glob: StringProperty(default="*.ghg", options={"HIDDEN"})

    extractor_folder: StringProperty(
        name="Extractor Folder",
        # subtype="DIR_PATH", ## <-- can't open folder selection window while already inside a file selection
        default="",
        description="Folder containing ExtractDx11MESHFix.exe / ExtractNxgMESHFix.exe",
    )

    parts_text: StringProperty(
        name="Part Numbers",
        default="0",
        description="Comma-separated part numbers, or ranges like 0-3,7,9",
    )

    # use_scale: BoolProperty( ## <-- you'd never want not to use scale
    #     name="Use Scale",
    #     default=True,
    #     description="Apply the scale field when building the armature",
    # )

    source_forward: EnumProperty(
        name="Source Forward",
        items=[
            ('X', 'X', 'Source forward is +X'),
            ('Y', 'Y', 'Source forward is +Y'),
            ('Z', 'Z', 'Source forward is +Z'),
            ('-X', '-X', 'Source forward is -X'),
            ('-Y', '-Y', 'Source forward is -Y'),
            ('-Z', '-Z', 'Source forward is -Z'),
        ],
        default='-Z',
    )

    source_up: EnumProperty(
        name="Source Up",
        items=[
            ('X', 'X', 'Source up is +X'),
            ('Y', 'Y', 'Source up is +Y'),
            ('Z', 'Z', 'Source up is +Z'),
            ('-X', '-X', 'Source up is -X'),
            ('-Y', '-Y', 'Source up is -Y'),
            ('-Z', '-Z', 'Source up is -Z'),
        ],
        default='Y',
    )

    def invoke(self, context, event):
        if not self.extractor_folder.strip():
            self.extractor_folder = str(_addon_dir())
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        ghg_file = Path(self.filepath).expanduser()

        if not ghg_file.exists():
            self.report({"ERROR"}, f"File not found: {ghg_file}")
            return {"CANCELLED"}

        if ghg_file.is_dir():
            self.report({"ERROR"}, f"You selected a folder, not a .ghg file: {ghg_file}")
            return {"CANCELLED"}

        if ghg_file.suffix.lower() != ".ghg":
            self.report({"WARNING"}, f"The selected file does not end in .ghg: {ghg_file.name}")

        extractor_directory = _normalize_dir_path(self.extractor_folder)
        if extractor_directory is None or not extractor_directory.exists() or not extractor_directory.is_dir():
            extractor_directory = _addon_dir()

        selected_armature = _selected_armature_from_context(context)

        try:
            ghg_bytes = _read_ghg_bytes(ghg_file)
            bones = _parse_bones_from_ghg(ghg_bytes)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to parse skeleton: {e}")
            return {"CANCELLED"}

        if bones:
            bones_for_names = bones
        else:
            if selected_armature is None:
                self.report({"ERROR"}, "This GHG has no skeleton, so select an Armature before importing.")
                return {"CANCELLED"}
            bones_for_names = _armature_bones_as_list(selected_armature)

        try:
            extractor_output = _run_extractor(extractor_directory, ghg_file)
            parts = _parse_blend_and_part_data(extractor_output, ghg_bytes, bones_for_names)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to parse blend / part data: {e}")
            return {"CANCELLED"}

        if bones:
            try:
                armature_obj = _create_armature_from_bones(
                    bones,
                    armature_name=ghg_file.stem + "_ARMATURE",
                    use_scale=True, #self.use_scale, ## <-- No reason not to use scale
                    source_forward=self.source_forward,
                    source_up=self.source_up,
                )
            except Exception as e:
                self.report({"ERROR"}, f"Failed to create armature: {e}")
                return {"CANCELLED"}
        else:
            armature_obj = selected_armature

        selected_part_ids = _parse_part_selection(self.parts_text, max_part_index=max(parts.keys(), default=-1))
        self.report({"INFO"}, "".join(str(x) for x in parts.keys()))
        if not selected_part_ids:
            self.report({"WARNING"}, "No valid part numbers were entered.")
            return {"FINISHED"}

        imported_any = False
        missing = []
        failed = []

        for part_id in selected_part_ids:
            part_obj_name = f"{ghg_file.stem}{part_id:04d}.obj"
            part_obj_file = _find_ghg_related_file(ghg_file, part_obj_name)
            if part_obj_file is None:
                missing.append(part_id)
                continue

            if part_id not in parts:
                failed.append(part_id)
                continue

            try:
                imported_meshes = _import_obj_mesh(part_obj_file)
                for mesh_obj in imported_meshes:
                    _assign_weights_to_mesh(mesh_obj, armature_obj, parts[part_id])
                imported_any = True
            except Exception as e:
                self.report({"ERROR"}, f"Failed to import part {part_id}: {e}")
                return {"CANCELLED"}

        if missing:
            self.report({"WARNING"}, f"Missing OBJ files for parts: {', '.join(map(str, missing))}")
        if failed:
            self.report({"WARNING"}, f"No blend data found for parts: {', '.join(map(str, failed))}")

        if imported_any:
            self.report({"INFO"}, "GHG skeleton and selected parts imported")
        else:
            self.report({"WARNING"}, "Skeleton imported, but no selected parts were imported")

        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_ghg_skeleton_parts.bl_idname, text="GHG Skeleton + Skinned parts (.ghg)")



# register logic for opening via the scripting tab, so if you click the play button multiple times there's only one import button still. Not needed if loading as an addon.
# def register():
#     old_func = getattr(bpy.types, "_ghg_import_menu_func", None)
#     if old_func is not None:
#         try:
#             bpy.types.TOPBAR_MT_file_import.remove(old_func)
#         except Exception:
#             pass

#     old_class = getattr(bpy.types, "_ghg_import_operator_class", None)
#     if old_class is not None:
#         try:
#             bpy.utils.unregister_class(old_class)
#         except Exception:
#             pass

#     bpy.utils.register_class(IMPORT_OT_ghg_skeleton_parts)

#     bpy.types._ghg_import_menu_func = menu_func_import
#     bpy.types._ghg_import_operator_class = IMPORT_OT_ghg_skeleton_parts

#     bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def register():
    bpy.utils.register_class(IMPORT_OT_ghg_skeleton_parts)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(IMPORT_OT_ghg_skeleton_parts)


if __name__ == "__main__":
    register()
