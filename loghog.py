import traceback
from sys import argv
from pathlib import Path
import math
from struct import pack, unpack
import json
import re

def main():

    global ROTV_items_count
    global current_bone_till_end
    global bones
    global GHG_bytes
    global LOGH_offset

    try:
        GHG_file = Path(argv[1])
        GHG_bytes = GHG_file.read_bytes()
    except:
        input("Drag a GHG to the .py file.")
        exit()

    bones=[]

    LOGH_index = 0 # all LOGHs seem to be the exact same, so just use the first one
    
    LOGH_offset = GHG_bytes.replace(b"LOGH",b"FUCK",LOGH_index).index(b"LOGH")
    print(f"LOGH offset: {LOGH_offset:02X}")

    # supported_LOGH_versions = [10, 17]

    LOGH_version = int(GHG_bytes[LOGH_offset+4:LOGH_offset+8].hex(), 16)

    if LOGH_version in (16,17) :
        LOGH_11()
    elif LOGH_version in (4, 9, 10):
        LOGH_0A()
    else:
        print(f"Unsupported LOGH version {LOGH_version:02X}")

    json_file=GHG_file.with_suffix(".json")

    text = json.dumps(bones, indent=2, ensure_ascii=False)

    # Turn arrays into one line
    for key in ("position", "rotation", "scale","unknown_1"):
        text = re.sub(
            rf'("{key}"\s*:\s*)\[\s*([^\]]*?)\s*\]',
            lambda m: m.group(1) + "[" + " ".join(m.group(2).split()) + "]",
            text,
            flags=re.DOTALL
        )

    json_file.write_text(text, encoding="utf-8")

def LOGH_11():

    ROTV_data_start_offset = LOGH_offset + 4 + 4 + 4 + 4
    current_bone_till_end = GHG_bytes[ROTV_data_start_offset:]
    ROTV_items_count = int(GHG_bytes[LOGH_offset+12:LOGH_offset+16].hex(), 16)
    print(f"ROTV items count: {ROTV_items_count}")

    c=0

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
            "unknown_0":0,
            "unknown_1":(0,0,0)
            }

        namelen = int(current_bone_till_end[:2].hex(), 16) - 1

        current_bone["name"] = current_bone_till_end[2:2+namelen].decode("utf-8", errors="replace")

        print(current_bone["name"])

        c = 2 + namelen + 1 + 64 # skip over matrix in this section; it's useless

        # matrix = []
        # for i in range(4):
        #     row=[]
        #     for j in range(4):
        #         row.append(unpack('>f',current_bone_till_end[c:c+4])[0])
        #         #print(current_bone_till_end[c:c+4])
        #         c+=4
        #     matrix.append(row)
        # #bones[k]["matrix"]=matrix
        # decomposed = decompose_matrix(matrix, column_major=True)

        # current_bone["position"]=decomposed[0]
        # current_bone["rotation"]=decomposed[1]
        # current_bone["scale"]=decomposed[2]

        unknown_1=[]
        for i in range(3):
            unknown_1.append(unpack('>f',current_bone_till_end[c:c+4])[0])
            c+=4

        current_bone["parent_idx"] = current_bone_till_end[c]

        current_bone["unknown_0"] = current_bone_till_end[c+1]

        current_bone["unknown_1"]=unknown_1

        bones.append(current_bone)

        print(current_bone)

        current_bone_till_end=current_bone_till_end[c+2:]
    

    print(current_bone_till_end[c:c+4])
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
            "unknown_0":0,
            "unknown_1":(0,0,0)
            }

        name_string_offset = int(current_bone_till_end[:4].hex(), 16)

        current_bone["name"] = name_strings[name_string_offset:].split(b'\x00', 1)[0].decode("utf-8", errors="replace")

        print(current_bone["name"])

        c = 4 + 64 # skip over matrix in this section; it's useless

        unknown_1=[]
        for i in range(3):
            unknown_1.append(unpack('>f',current_bone_till_end[c:c+4])[0])
            c+=4

        current_bone["parent_idx"] = current_bone_till_end[c]

        current_bone["unknown_0"] = current_bone_till_end[c+1]

        current_bone["unknown_1"] = unknown_1

        bones.append(current_bone)

        print(current_bone)

        current_bone_till_end=current_bone_till_end[c+2:]
    

    print(current_bone_till_end[c:c+4])
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


if __name__=="__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    input("Press enter to exit.")