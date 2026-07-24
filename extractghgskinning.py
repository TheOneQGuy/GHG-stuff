from pathlib import Path
from sys import argv
import traceback

import subprocess

import math
from struct import unpack

def main():

    global bones
    global GHG_bytes; global GHG_file
    global script_directory
    global extractor_output

    bones=[]

    try:
        GHG_file = Path(argv[1])
        GHG_bytes = GHG_file.read_bytes()
    except:
        input("Drag a GHG broski")
        exit()

    script_directory = Path(__file__).resolve().parent

    get_skeleton() # bones list with each bone being a dictionary having "name", "idx", "parent_idx", "position", "rotation", and "scale" keys.

    get_extractor_output()

    get_blends()

    get_list_of_parts() # parts dictionary with format id:info, info being a dictionary having "blendnames" and "blendweights" keys.


def get_skeleton():

    global LOGH_offset

    LOGH_index = 0 # all LOGHs seem to be the exact same, so just use the first one
    
    LOGH_offset = GHG_bytes.replace(b"LOGH",b"AAAA",LOGH_index).index(b"LOGH")
    print(f"LOGH offset: {LOGH_offset:02X}")

    LOGH_version = int(GHG_bytes[LOGH_offset+4:LOGH_offset+8].hex(), 16)

    if LOGH_version >= 14:
        LOGH_11()
    else:
        LOGH_0A()
    # else:
    #     print(f"Unsupported LOGH version {LOGH_version:02X}")

    # json_file=GHG_file.with_suffix(".json")

    # text = json.dumps(bones, indent=2, ensure_ascii=False)

    # # Turn arrays into one line
    # for key in ("position", "rotation", "scale","unknown_1"):
    #     text = sub(
    #         rf'("{key}"\s*:\s*)\[\s*([^\]]*?)\s*\]',
    #         lambda m: m.group(1) + "[" + " ".join(m.group(2).split()) + "]",
    #         text,
    #         flags=DOTALL
    #     )

    # json_file.write_text(text, encoding="utf-8")

def get_extractor_output():
    
    global extractor_output
    
    extractor_names = ("ExtractDx11MESHFix.exe", "ExtractNxgMESHFix.exe", "ExtractDx11MESH.exe", "ExtractNxgMESH.exe")

    # find extractor name
    for i in extractor_names:
        if (script_directory/i).exists():
            extractor_name = i
            print(f"Using extractor {extractor_name}.")
            break

    # get GHG from argv

    # get extractmesh output

    extractor_output = ""

    process = subprocess.Popen(
        [extractor_name, GHG_file],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    for line in process.stdout:
        extractor_output+=line
        if "Press enter to close..." in line:
            break
    return extractor_output


def get_blends():
    """
    Get a list of all vertex lists with blend indices
    and blend weights from the extractor output.
    """
    global number_of_parts
    number_of_parts = get_extractor_value("Number of Parts: 0x")
    keyword_attrlengths = {
        "4float":16,
        "3float":12,
        "2float":8,
        "4half":8,
        "4mini":4,
        "4char":4,
        "2half":4
        }
    
    global vertexlists; global skinned_vertexlists
    vertexlists = {} # id:list, each list has dicts, each blend indices and blend weights key has a list in it
    skinned_vertexlists = []
    
    c=0
    while True:
        current_vertexlist = []
        try:
            id = get_extractor_value("New Vertex List 0x", c, length=4)
        except ValueError:
            break
        number_of_vertices = get_extractor_value("Number of Vertices: ", c)
        vertexlist_info_raw = (
            extractor_output[
                get_string_index("New Vertex List 0x", c)
                :get_string_index("Number of Vertices: ", c)
            ]
        )
        if not "blendWeight0" in vertexlist_info_raw:
            c+=1
            continue
        attrlenghts_sum = 0
        for i in keyword_attrlengths.keys():
            attrlenghts_sum +=(
                vertexlist_info_raw.count(i)
                *keyword_attrlengths[i]
            )
        # includes extra data but it doesn't matter
        vertexlist_bytes = GHG_bytes[get_file_offset("Number of Vertices: ", c):]
        for vertex in range(number_of_vertices):
            current_blendindices = []
            current_blendweights = []
            current_blendindices_offset = (vertex + 1) * attrlenghts_sum - 8
            for blendindex in (
                    vertexlist_bytes
                    [current_blendindices_offset
                    :current_blendindices_offset+4]
                ):
                current_blendindices.append(int(blendindex))
            for blendweight in (
                    vertexlist_bytes
                    [current_blendindices_offset+4
                    :current_blendindices_offset+8]
                ):
                current_blendweights.append(float(blendweight)/255)
            current_vertexlist.append(
                {
                "blendindices":current_blendindices,
                "blendweights":current_blendweights
                }
            )

        vertexlists[id]=current_vertexlist
        skinned_vertexlists.append(id)
        c+=1
        #sleep(1)


def get_list_of_parts():
    
    global parts; parts = {}

    for i in range(number_of_parts):

        current_part = []
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

        for j in skinned_vertexlists:
            if (
                f"New Vertex List {j:#06x}" in current_part_info or
                f"Vertex List Reference to {j:#06x}" in current_part_info
            ):
                part_vertexlist_id = j
                break
        else:
            # part doesn't use skinned vertex lists, skip
            continue

        offset_vertices = get_extractor_value("Offset Vertices: 0x", string=current_part_info)
        number_vertices = get_extractor_value("Number Vertices: 0x", string=current_part_info)
        bone_indices_raw = (
            get_extractor_value(
                "Number Vertices: 0x",
                offset = 8 + 1 + 8 + 5, #number vertices + newline + file offset + 5 spaces
                length = 80,
                integer_output = False,
                string = current_part_info
            )
        )

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

        part_vertices = (
            vertexlists[part_vertexlist_id][
                offset_vertices:
                offset_vertices+number_vertices
            ]
        )

        for vertex in part_vertices:
            new_blendindices = []
            for blendindex in vertex["blendindices"]:
                #print(i,bone_indices,blendindex,number_of_parts)
                new_blendindices.append(
                    bones[
                        bone_indices[blendindex]
                    ]
                    ["name"]
                    if blendindex != 255 and bone_indices[blendindex] != 255 else "None"
                )
            current_part.append(
                {"blendnames":new_blendindices,
                "blendweights":vertex["blendweights"]}
            )
        
        parts[i]=current_part

def get_extractor_value(
        substring:str,
        index:int=0,
        offset:int=0,
        length:int=8,
        integer_output:bool=True,
        string:str=None
    ):
    if string is None:
        string = extractor_output
    """
    Get the value that comes after a string in
    the extractor output as a base 10 integer.
    IndexError if you try to get an index that
    doesn't exist.

    """
    value_offset = (
        get_string_index(substring, index, string=string)
        +len(substring)
        +offset
    )

    value = string[value_offset:value_offset+length]

    return int(value,16) if integer_output else value

def get_string_index(
        substring:str,
        index:int=0,
        string:str=None
    )->int:
    if string is None:
        string=extractor_output
    """
    Return the offset for the nth appearance of
    a string in the extractor output.
    """
    return(
        string
        .replace(substring,","*len(substring),index)
        .index(substring)
    )

def get_file_offset(
        substring:str,
        index:int=0,
        string:str=None
    )->int:
    if string is None:
        string = extractor_output
    """
    Get the GHG offset at the left of the line
    that contains a substring.
    """
    substring_index = get_string_index(substring, index, string=string)
    offset_start = string.rfind("\n", 0, substring_index) + 1
    return(
        int(
        string[offset_start:offset_start+8]
        , 16)
    )

def LOGH_11():

    ROTV_data_start_offset = LOGH_offset + 4 + 4 + 4 + 4
    current_bone_till_end = GHG_bytes[ROTV_data_start_offset:]
    ROTV_items_count = int(GHG_bytes[LOGH_offset+12:LOGH_offset+16].hex(), 16)
    print(f"ROTV items count: {ROTV_items_count}")

    for k in range(ROTV_items_count):
        current_bone_idx = k
        current_bone = {
            "name":"name",
            "idx":current_bone_idx,
            "parent_idx":0,
            "position":0,
            "rotation":0,
            "scale":0,
            # "matrix":((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
            # "unknown_0":0,
            # "unknown_1":(0,0,0)
            }

        namelen = int(current_bone_till_end[:2].hex(), 16) - 1

        current_bone["name"] = current_bone_till_end[2:2+namelen].decode("utf-8", errors="replace")

        # print(current_bone["name"])

        c = 2 + namelen + 1 + 64 # skip over matrix in this section; it's useless
        

        unknown_1=[]
        for i in range(3):
            unknown_1.append(unpack('>f',current_bone_till_end[c:c+4])[0])
            c+=4

        current_bone["parent_idx"] = current_bone_till_end[c]

        # current_bone["unknown_0"] = current_bone_till_end[c+1]

        # current_bone["unknown_1"]=unknown_1

        bones.append(current_bone)

        # print(current_bone)

        current_bone_till_end=current_bone_till_end[c+2:]
    

    c = 8
    for k in range(ROTV_items_count):
        matrix = []
        for i in range(4):
            row=[]
            for j in range(4):
                row.append(unpack('>f',current_bone_till_end[c:c+4])[0])
                #print(current_bone_till_end[c:c+4])
                c+=4
            matrix.append(row)
        #bones[k]["matrix"]=matrix
        decomposed = decompose_matrix(matrix, column_major=True)

        bones[k]["position"]=decomposed[0]
        bones[k]["rotation"]=decomposed[1]
        bones[k]["scale"]=decomposed[2]


    # inverse bind if we ever need it
    # c += 8
    # for k in range(ROTV_items_count):
    #     matrix = []
    #     for i in range(4):
    #         row=[]
    #         for j in range(4):
    #             row.append(unpack('>f',current_bone_till_end[c:c+4])[0])
    #             c+=4
    #         matrix.append(row)
    #     bones[k]["matrix"]==matrix
    #     decomposed = decompose_matrix(matrix, column_major=True)

    #     bones[k]["position"]=decomposed[0]
    #     bones[k]["rotation"]=decomposed[1]
    #     bones[k]["scale"]=decomposed[2]

def LOGH_0A():

    # get bone names list
    default_string_offset = GHG_bytes.index(b"default_string")
    name_strings = GHG_bytes[default_string_offset:]

    ROTV_data_start_offset = LOGH_offset + 4 + 4 + 4
    current_bone_till_end = GHG_bytes[ROTV_data_start_offset:]
    ROTV_items_count = int(GHG_bytes[LOGH_offset+8:LOGH_offset+12].hex(), 16)
    print(f"ROTV items count: {ROTV_items_count}")

    for k in range(ROTV_items_count):
        current_bone_idx = k
        current_bone = {
            "name":"name",
            "idx":current_bone_idx,
            "parent_idx":0,
            "position":0,
            "rotation":0,
            "scale":0,
            # "matrix":((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
            # "unknown_0":0,
            # "unknown_1":(0,0,0)
            }

        name_string_offset = int(current_bone_till_end[:4].hex(), 16)

        current_bone["name"] = name_strings[name_string_offset:].split(b'\x00', 1)[0].decode("utf-8", errors="replace")

        # print(current_bone["name"])

        c = 4 + 64 # skip over matrix in this section; it's useless

        unknown_1=[]
        for i in range(3):
            # unknown_1.append(unpack('>f',current_bone_till_end[c:c+4])[0])
            c+=4

        current_bone["parent_idx"] = current_bone_till_end[c]

        # current_bone["unknown_0"] = current_bone_till_end[c+1]

        # current_bone["unknown_1"] = unknown_1

        bones.append(current_bone)

        # print(current_bone)

        current_bone_till_end=current_bone_till_end[c+2:]
    
    c = 4
    for k in range(ROTV_items_count):
        matrix = []
        for i in range(4):
            row=[]
            for j in range(4):
                row.append(unpack('>f',current_bone_till_end[c:c+4])[0])
                #print(current_bone_till_end[c:c+4])
                c+=4
            matrix.append(row)
        #bones[k]["matrix"]=matrix
        decomposed = decompose_matrix(matrix, column_major=True)

        bones[k]["position"]=decomposed[0]
        bones[k]["rotation"]=decomposed[1]
        bones[k]["scale"]=decomposed[2]


    # inverse bind if we ever need it
    # c += 8
    # for k in range(ROTV_items_count):
    #     matrix = []
    #     for i in range(4):
    #         row=[]
    #         for j in range(4):
    #             row.append(unpack('>f',current_bone_till_end[c:c+4])[0])
    #             c+=4
    #         matrix.append(row)
    #     bones[k]["matrix"]==matrix
    #     decomposed = decompose_matrix(matrix, column_major=True)

    #     bones[k]["position"]=decomposed[0]
    #     bones[k]["rotation"]=decomposed[1]
    #     bones[k]["scale"]=decomposed[2]

def vec_len(v):
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])

def quat_from_mat3(m):
    # m is 3x3: [[m00,m01,m02],[m10,m11,m12],[m20,m21,m22]]
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

def decompose_matrix(mat, column_major=False):
    """
    mat = 4x4 nested list:
      [[...], [...], [...], [...]]
    If your matrix is stored the other way around, set column_major=True.
    Returns: position, rotation_quat, scale
    """

    if column_major:
        # translation in last row
        px, py, pz = mat[3][0], mat[3][1], mat[3][2]

        # basis vectors are columns
        xaxis = [mat[0][0], mat[1][0], mat[2][0]]
        yaxis = [mat[0][1], mat[1][1], mat[2][1]]
        zaxis = [mat[0][2], mat[1][2], mat[2][2]]
    else:
        # translation in last column
        px, py, pz = mat[0][3], mat[1][3], mat[2][3]

        # basis vectors are rows
        xaxis = [mat[0][0], mat[0][1], mat[0][2]]
        yaxis = [mat[1][0], mat[1][1], mat[1][2]]
        zaxis = [mat[2][0], mat[2][1], mat[2][2]]

    sx = vec_len(xaxis)
    sy = vec_len(yaxis)
    sz = vec_len(zaxis)

    if sx != 0: xaxis = [v / sx for v in xaxis]
    if sy != 0: yaxis = [v / sy for v in yaxis]
    if sz != 0: zaxis = [v / sz for v in zaxis]

    rot3 = [
        [xaxis[0], xaxis[1], xaxis[2]],
        [yaxis[0], yaxis[1], yaxis[2]],
        [zaxis[0], zaxis[1], zaxis[2]],
    ]

    quat = quat_from_mat3(rot3)

    return (px, py, pz), quat, (sx, sy, sz)


def find_file_path(name:str):
    """
    Checks if a file with the given name exists in the
    same directory as the ghg OR if it exists in the same
    directory\\filename, then returns the Path object of
    the newer file.
    """
    GHG_location = GHG_file.parent
    next_to_GHG = GHG_location / name
    in_folder = GHG_location / GHG_file.stem / name

    if next_to_GHG.exists() and in_folder.exists():
        if next_to_GHG.stat().st_mtime > in_folder.stat().st_mtime:
            return(next_to_GHG)
        else:
            return(in_folder)
    
    if next_to_GHG.exists():
        return(next_to_GHG)
    
    if in_folder.exists():
        return(in_folder)
    
    return None


if __name__=="__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    input("Press enter to exit.")