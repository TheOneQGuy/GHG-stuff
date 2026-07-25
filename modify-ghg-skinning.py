
bl_info = {
    "name": "GHG Importer / Exporter (Skeleton + Skinned parts)",
    "author": "Queue Wafer",
    "version": (2, 0, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > GHG Skeleton + Skinned parts (.ghg), File > Export > Parts skinning (.ghg)",
    "description": "Import a GHG skeleton and skinned parts, modify their skinning, and export the skining.",
    "category": "Import-Export",
}

import math
import os
import re
import subprocess
import traceback
from pathlib import Path
from struct import unpack

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import AddonPreferences, Operator
from bpy_extras.io_utils import ImportHelper, ExportHelper, axis_conversion
from mathutils import Matrix, Quaternion, Vector


ROOT_PARENT_SENTINEL = 255
EXTRACTOR_NAMES = (
    "ExtractDx11MESHFix.exe",
    "ExtractNxgMESHFix.exe",
    "ExtractDx11MESH.exe",
    "ExtractNxgMESH.exe",
)

EXTRACTOR_FAMILIES = {
    "ANY": EXTRACTOR_NAMES,
    "DX11": (
        "ExtractDx11MESH.exe",
        "ExtractDx11MESHFix.exe",
    ),
    "DX11Fix": (
        "ExtractDx11MESHFix.exe",
        "ExtractDx11MESH.exe",
    ),
    "NXG": (
        "ExtractNxgMESH.exe",
        "ExtractNxgMESHFix.exe",
    ),
    "NXGFix": (
        "ExtractNxgMESHFix.exe",
        "ExtractNxgMESH.exe",
    ),
}

ATTR_LENGTHS = {
    "4float": 16,
    "3float": 12,
    "2float": 8,
    "4half": 8,
    "4mini": 4,
    "4char": 4,
    "2half": 4,
}


def _addon_prefs():
    addon = bpy.context.preferences.addons.get(__package__ or __name__)
    return addon.preferences if addon else None


def _normalize_extractor_mode(mode: str | None) -> str:
    if mode in (None, "", "LEGACY"):
        return "ANY"
    return mode


class GHGAddonPreferences(AddonPreferences):
    bl_idname = __package__ or __name__

    extractor_folder: StringProperty(
        name="Extractor Folder",
        subtype="DIR_PATH",
        default="",
        description="Folder containing ExtractDx11MESHFix.exe / ExtractNxgMESHFix.exe",
    )

    extractor_mode: EnumProperty(
        name="Extractor Mode",
        items=[
            ('ANY', "Any", "Use the first extractor executable found"),
            ('DX11', "DX11", "Use ExtractDx11MESH first, then ExtractDx11MESHFix if needed"),
            ('DX11Fix', "DX11Fix", "Use ExtractDx11MESHFix first, then ExtractDx11MESH if needed"),
            ('NXG', "NXG", "Use ExtractNxgMESH first, then ExtractNxgMESHFix if needed"),
            ('NXGFix', "NXGFix", "Use ExtractNxgMESHFix first, then ExtractNxgMESH if needed"),
        ],
        default='ANY',
        description="Choose which extractor family to use",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "extractor_folder")
        layout.prop(self, "extractor_mode")


def _load_extractor_settings_from_prefs(op):
    prefs = _addon_prefs()
    if prefs is None:
        return
    if not getattr(op, "extractor_folder", "").strip() and getattr(prefs, "extractor_folder", "").strip():
        op.extractor_folder = prefs.extractor_folder
    if getattr(op, "extractor_mode", None) in {None, ""}:
        op.extractor_mode = _normalize_extractor_mode(prefs.extractor_mode)


def _save_extractor_settings_to_prefs(op):
    prefs = _addon_prefs()
    if prefs is None:
        return
    prefs.extractor_folder = getattr(op, "extractor_folder", "")
    prefs.extractor_mode = _normalize_extractor_mode(getattr(op, "extractor_mode", "ANY"))


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


def _find_extractor(extractor_directory: Path, extractor_mode: str = "ANY") -> Path:
    names = EXTRACTOR_FAMILIES.get(_normalize_extractor_mode(extractor_mode), EXTRACTOR_NAMES)
    for name in names:
        path = extractor_directory / name
        if path.exists():
            return path
    raise FileNotFoundError(
        "Could not find an extractor executable in the selected folder. "
        "Expected one of: " + ", ".join(names)
    )


def _run_extractor(extractor_directory: Path, ghg_file: Path, extractor_mode: str = "ANY") -> str:
    exe = _find_extractor(extractor_directory, extractor_mode=extractor_mode)
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

    if logh_version >= 14:
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

    else:
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
    # else:
    #     raise ValueError(f"Unsupported LOGH version: {logh_version}")

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

        if len(bone_indices_raw)==80:
            bone_indices = []
            for j in range((len(bone_indices_raw)//3)):
                bone_idx = j*3
                bone_indices.append(
                    int(
                        bone_indices_raw[bone_idx:bone_idx+2],
                        16
                    )
                )
        else:
            bone_indices = range(len(bones))

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

    extractor_mode: EnumProperty(
        name="Extractor Mode",
        items=[
            ('ANY', "Any", "Use the first extractor executable found"),
            ('DX11', "DX11", "Use ExtractDx11MESH first, then ExtractDx11MESHFix if needed"),
            ('DX11Fix', "DX11Fix", "Use ExtractDx11MESHFix first, then ExtractDx11MESH if needed"),
            ('NXG', "NXG", "Use ExtractNxgMESH first, then ExtractNxgMESHFix if needed"),
            ('NXGFix', "NXGFix", "Use ExtractNxgMESHFix first, then ExtractNxgMESH if needed"),
        ],
        default='ANY',
        description="Choose which extractor family to use",
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
        _load_extractor_settings_from_prefs(self)
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

        _save_extractor_settings_to_prefs(self)

        selected_armature = _selected_armature_from_context(context)

        try:
            ghg_bytes = _read_ghg_bytes(ghg_file)
            bones = _parse_bones_from_ghg(ghg_bytes)
        except Exception as e:
            traceback.print_exc()
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
            extractor_output = _run_extractor(extractor_directory, ghg_file, self.extractor_mode)
            parts = _parse_blend_and_part_data(extractor_output, ghg_bytes, bones_for_names)
        except Exception as e:
            traceback.print_exc()
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
                traceback.print_exc()
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
                traceback.print_exc()
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



# -----------------------------------------------------------------------------
# Export side (separate from import-side state)
# -----------------------------------------------------------------------------

def _export_get_bone_names_from_ghg_bytes(exp_ghg_bytes: bytes):
    # Reuse the pure skeleton parser, but keep export-side state separate.
    return [bone["name"] for bone in _parse_bones_from_ghg(exp_ghg_bytes)]


def _export_part_id_from_object_name(name: str):
    # Use the last 4 characters of the object name before Blender's ".001" suffix.
    # Example: "foo_dx110001" -> part id 1 from "0001".
    base_name = re.sub(r"\.[0-9]{3}$", "", name)
    if len(base_name) < 4:
        return None
    suffix = base_name[-4:]
    if not suffix.isdigit():
        return None
    return int(suffix)


def _export_build_parts_from_selected_objects(context):
    """
    Build:
      export_parts[part_id] = {
        "blendnames": [[...], [...], ...],   # per-vertex lists
        "blendweights": [[...], [...], ...], # per-vertex lists
        "object_name": str,
      }
    """
    export_parts = {}

    for obj in getattr(context, "selected_objects", []):
        if getattr(obj, "type", None) != "MESH":
            continue

        part_id = _export_part_id_from_object_name(obj.name)
        if part_id is None:
            continue

        mesh = obj.data
        per_vertex_names = []
        per_vertex_weights = []

        for vertex in mesh.vertices:
            weighted = []
            for group_ref in vertex.groups:
                try:
                    weight = float(group_ref.weight)
                except Exception:
                    continue
                if weight <= 0.0:
                    continue
                group_name = obj.vertex_groups[group_ref.group].name
                if group_name:
                    weighted.append((group_name, weight))

            weighted.sort(key=lambda item: (-item[1], item[0]))

            total_weight = sum(weight for _, weight in weighted)
            if total_weight > 0.0:
                normalized = [(name, weight / total_weight) for name, weight in weighted]
            else:
                normalized = []

            per_vertex_names.append([name for name, _ in normalized])
            per_vertex_weights.append([weight for _, weight in normalized])

        export_parts[part_id] = {
            "blendnames": per_vertex_names,
            "blendweights": per_vertex_weights,
            "object_name": obj.name,
        }

    return export_parts


def _export_get_extractor_output(exp_extractor_dir: Path, exp_ghg_file: Path, exp_extractor_mode: str = "ANY") -> str:
    return _run_extractor(exp_extractor_dir, exp_ghg_file, exp_extractor_mode)


def _export_get_vertexlist_offsets(exp_extractor_output: str, exp_ghg_bytes: bytes):
    exp_number_of_parts = _get_extractor_value("Number of Parts: 0x", string=exp_extractor_output)
    exp_vertexlists = {}
    exp_skinned_vertexlist_ids = []

    c = 0
    while True:
        try:
            vertex_list_id = _get_extractor_value("New Vertex List 0x", c, length=4, string=exp_extractor_output)
        except ValueError:
            break

        number_of_vertices = _get_extractor_value("Number of Vertices: ", c, string=exp_extractor_output)
        vertexlist_info_raw = exp_extractor_output[
            _get_string_index("New Vertex List 0x", c, string=exp_extractor_output):
            _get_string_index("Number of Vertices: ", c, string=exp_extractor_output)
        ]

        if "blendWeight0" not in vertexlist_info_raw:
            c += 1
            continue

        attrlengths_sum = 0
        for key, size in ATTR_LENGTHS.items():
            attrlengths_sum += vertexlist_info_raw.count(key) * size

        start_offset = _get_file_offset("Number of Vertices: ", c, string=exp_extractor_output)

        exp_vertexlists[vertex_list_id] = {
            "offset": start_offset,
            "attrlengths_sum": attrlengths_sum,
            "number_of_vertices": number_of_vertices,
        }
        exp_skinned_vertexlist_ids.append(vertex_list_id)
        c += 1

    return exp_number_of_parts, exp_vertexlists, exp_skinned_vertexlist_ids


def _export_part_spans(exp_extractor_output: str):
    """
    Returns a sorted list of tuples: (part_id, start_index, end_index)
    """
    spans = []
    matches = list(re.finditer(r"Part 0x([0-9A-Fa-f]{8})", exp_extractor_output))
    for idx, match in enumerate(matches):
        part_id = int(match.group(1), 16)
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(exp_extractor_output)
        spans.append((part_id, start, end))
    return spans


def _export_write_part_blends(
    exp_ghg_bytes: bytes,
    exp_ghg_file: Path,
    exp_bone_names: list[str],
    exp_extractor_output: str,
    exp_vertexlists: dict,
    exp_skinned_vertexlist_ids: list,
    export_parts: dict,
):
    part_spans = {part_id: (start, end) for part_id, start, end in _export_part_spans(exp_extractor_output)}
    updated_bytes = exp_ghg_bytes

    for part_id, part_info in export_parts.items():
        if part_id not in part_spans:
            print(f"Export skipped part {part_id}: part block not found in extractor output.")
            continue

        start_string_index, end_string_index = part_spans[part_id]
        extractor_part_info = exp_extractor_output[start_string_index:end_string_index]

        part_vertexlist_id = None
        for vertexlist_id in exp_skinned_vertexlist_ids:
            if (
                f"New Vertex List {vertexlist_id:#06x}" in extractor_part_info or
                f"Vertex List Reference to {vertexlist_id:#06x}" in extractor_part_info
            ):
                part_vertexlist_id = vertexlist_id
                break

        if part_vertexlist_id is None:
            print(f"Export skipped part {part_id}: no skinned vertex list reference found.")
            continue

        if part_vertexlist_id not in exp_vertexlists:
            print(f"Export skipped part {part_id}: vertex list metadata missing.")
            continue

        offset_vertices = _get_extractor_value("Offset Vertices: 0x", string=extractor_part_info)
        number_vertices = _get_extractor_value("Number Vertices: 0x", string=extractor_part_info)

        blendnames_by_vertex = part_info.get("blendnames", [])
        blendweights_by_vertex = part_info.get("blendweights", [])

        if len(blendnames_by_vertex) != len(blendweights_by_vertex):
            print(f"Export skipped part {part_id}: blendnames/blendweights length mismatch.")
            continue

        if len(blendnames_by_vertex) != number_vertices:
            print(
                f"Export skipped part {part_id}: vertex count mismatch "
                f"(Blender {len(blendnames_by_vertex)} vs GHG {number_vertices})."
            )
            continue

        used_bone_names = []
        for names in blendnames_by_vertex:
            if len(names) > 4:
                print(f"Export skipped part {part_id}: a vertex uses more than 4 bones.")
                used_bone_names = []
                break
            for name in names:
                if name and name != "None" and name not in used_bone_names:
                    used_bone_names.append(name)

        if not used_bone_names:
            print(f"Export skipped part {part_id}: no usable bone names found.")
            continue

        # Keep only names that exist in the target GHG skeleton.
        missing = [name for name in used_bone_names if name not in exp_bone_names]
        if missing:
            print(f"Export skipped part {part_id}: missing bones in GHG: {missing}")
            continue

        bone_names_in_order_of_use = used_bone_names

        # Some extractor outputs include a bone-index remap table for this part.
        bone_indices_raw = _get_extractor_value(
            "Number Vertices: 0x",
            offset=8 + 1 + 8 + 5,
            length=80,
            integer_output=False,
            string=extractor_part_info,
        )
        has_bone_indices = len(bone_indices_raw) == 80

        if has_bone_indices:
            bone_indices_offset = _get_extractor_value(
                "Number Vertices: 0x",
                offset=8 + 1,
                length=8,
                integer_output=True,
                string=extractor_part_info,
            )
            bone_indices_ints = [exp_bone_names.index(name) for name in used_bone_names]
            bone_indices_to_write = bytes(bone_indices_ints) + bytes([0xFF]) * (27 - len(bone_indices_ints))
            updated_bytes = (
                updated_bytes[:bone_indices_offset] +
                bone_indices_to_write +
                updated_bytes[bone_indices_offset + 27:]
            )

        used_vertexlist_offset = exp_vertexlists[part_vertexlist_id]["offset"]
        used_vertexlist_attrlensum = exp_vertexlists[part_vertexlist_id]["attrlengths_sum"]

        for i in range(number_vertices):
            names = blendnames_by_vertex[i]
            weights = blendweights_by_vertex[i]

            idx_bytes = bytes([bone_names_in_order_of_use.index(name) for name in names])
            wgt_bytes = bytes([max(0, min(255, int(round(weight * 255.0)))) for weight in weights])

            idx_bytes = idx_bytes + bytes([0x00]) * (4 - len(idx_bytes))
            wgt_bytes = wgt_bytes + bytes([0x00]) * (4 - len(wgt_bytes))

            write_start = used_vertexlist_offset + (i + 1) * used_vertexlist_attrlensum - 8
            write_end = write_start + 8
            updated_bytes = updated_bytes[:write_start] + idx_bytes + wgt_bytes + updated_bytes[write_end:]

    if not exp_ghg_file.exists():
        raise FileNotFoundError(f"Target GHG file does not exist: {exp_ghg_file}")

    exp_ghg_file.write_bytes(updated_bytes)
    return updated_bytes


class EXPORT_OT_ghg_skeleton_parts(Operator, ExportHelper):
    bl_idname = "export_scene.ghg_skeleton_parts"
    bl_label = "Export GHG Skeleton + Skinned parts"
    bl_description = "Export selected mesh parts back into an existing GHG file"
    bl_options = {"UNDO"}

    filename_ext = ".ghg"
    filter_glob: StringProperty(default="*.ghg", options={"HIDDEN"})

    extractor_folder: StringProperty(
        name="Extractor Folder",
        default="",
        description="Folder containing ExtractDx11MESHFix.exe / ExtractNxgMESHFix.exe",
    )

    extractor_mode: EnumProperty(
        name="Extractor Mode",
        items=[
            ('ANY', "Any", "Use the first extractor executable found"),
            ('DX11', "DX11", "Use ExtractDx11MESH first, then ExtractDx11MESHFix if needed"),
            ('DX11Fix', "DX11Fix", "Use ExtractDx11MESHFix first, then ExtractDx11MESH if needed"),
            ('NXG', "NXG", "Use ExtractNxgMESH first, then ExtractNxgMESHFix if needed"),
            ('NXGFix', "NXGFix", "Use ExtractNxgMESHFix first, then ExtractNxgMESH if needed"),
        ],
        default='ANY',
        description="Choose which extractor family to use",
    )

    skeleton_source_file: StringProperty(
        name="Skeleton Source GHG",
        subtype="FILE_PATH",
        default="",
        description="Optional GHG used only to read skeleton bone names; leave empty to use the export target GHG",
    )

    def invoke(self, context, event):
        _load_extractor_settings_from_prefs(self)
        if not self.extractor_folder.strip():
            self.extractor_folder = str(_addon_dir())
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        exp_ghg_file = Path(self.filepath).expanduser()

        if not exp_ghg_file.exists():
            self.report({"ERROR"}, f"Cannot export: file does not exist: {exp_ghg_file}")
            return {"CANCELLED"}

        if exp_ghg_file.is_dir():
            self.report({"ERROR"}, f"Cannot export to a folder: {exp_ghg_file}")
            return {"CANCELLED"}

        exp_extractor_dir = _normalize_dir_path(self.extractor_folder)
        if exp_extractor_dir is None or not exp_extractor_dir.exists() or not exp_extractor_dir.is_dir():
            exp_extractor_dir = _addon_dir()

        _save_extractor_settings_to_prefs(self)

        try:
            exp_ghg_bytes = _read_ghg_bytes(exp_ghg_file)

            skeleton_source_path = self.skeleton_source_file.strip()
            if skeleton_source_path:
                exp_skeleton_source_file = Path(skeleton_source_path).expanduser()
                if not exp_skeleton_source_file.exists() or exp_skeleton_source_file.is_dir():
                    self.report({"ERROR"}, f"Skeleton source GHG does not exist or is a folder: {exp_skeleton_source_file}")
                    return {"CANCELLED"}
            else:
                exp_skeleton_source_file = exp_ghg_file

            exp_skeleton_bytes = _read_ghg_bytes(exp_skeleton_source_file)
            exp_bone_names = _export_get_bone_names_from_ghg_bytes(exp_skeleton_bytes)

            exp_extractor_output = _export_get_extractor_output(exp_extractor_dir, exp_ghg_file, self.extractor_mode)
            exp_number_of_parts, exp_vertexlists, exp_skinned_vertexlist_ids = _export_get_vertexlist_offsets(exp_extractor_output, exp_ghg_bytes)
            export_parts = _export_build_parts_from_selected_objects(context)

            if not export_parts:
                self.report({"WARNING"}, "No selected mesh parts could be exported.")
                return {"CANCELLED"}

            _export_write_part_blends(
                exp_ghg_bytes=exp_ghg_bytes,
                exp_ghg_file=exp_ghg_file,
                exp_bone_names=exp_bone_names,
                exp_extractor_output=exp_extractor_output,
                exp_vertexlists=exp_vertexlists,
                exp_skinned_vertexlist_ids=exp_skinned_vertexlist_ids,
                export_parts=export_parts,
            )

        except Exception:
            traceback.print_exc()
            self.report({"ERROR"}, "Export failed. See the System Console for the full traceback.")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported to existing GHG: {exp_ghg_file.name}")
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_ghg_skeleton_parts.bl_idname, text="GHG Skeleton + Skinned parts (.ghg)")


def menu_func_export(self, context):
    self.layout.operator(EXPORT_OT_ghg_skeleton_parts.bl_idname, text="Export GHG Parts skinnig (.ghg)")


def register():
    try:
        unregister()
    except Exception:
        pass

    bpy.utils.register_class(GHGAddonPreferences)
    bpy.utils.register_class(IMPORT_OT_ghg_skeleton_parts)
    bpy.utils.register_class(EXPORT_OT_ghg_skeleton_parts)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except Exception:
        pass

    try:
        bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    except Exception:
        pass

    try:
        bpy.utils.unregister_class(EXPORT_OT_ghg_skeleton_parts)
    except Exception:
        pass

    try:
        bpy.utils.unregister_class(IMPORT_OT_ghg_skeleton_parts)
    except Exception:
        pass

    try:
        bpy.utils.unregister_class(GHGAddonPreferences)
    except Exception:
        pass


if __name__ == "__main__":
    register()
