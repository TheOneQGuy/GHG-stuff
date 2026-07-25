from pathlib import Path
from sys import argv
import traceback

import subprocess

import math
from struct import unpack, pack

KEYWORD_ATTRLENGTHS = {
    "4float":16,
    "3float":12,
    "2float":8,
    "4half":8,
    "4mini":4,
    "4char":4,
    "2half":4
    }

def main():

    global bone_names; global parts
    global GHG_bytes; global GHG_file
    global script_directory
    global extractor_output

    bone_names=[]
    parts = {} # <- id:info, info being a dictionary having "blendnames" and "blendweights" keys. This list comes from blender meshes.
               # Unused blendnames (blendweight==0) should be removed when importing from blender.

    try:
        GHG_file = Path(argv[1])
        GHG_bytes = GHG_file.read_bytes()
    except:
        input("Drag a GHG broski")
        exit()

    script_directory = Path(__file__).resolve().parent

    get_bone_names() # bone names list from ghg we're importing to

    get_extractor_output()

    get_vertexlist_offsets()

    write_part_blends() # blend indices, blend weights, bone indices


def get_bone_names():

    global LOGH_offset

    LOGH_index = 0 # all LOGHs seem to be the exact same, so just use the first one
    
    LOGH_offset = GHG_bytes.replace(b"LOGH",b"AAAA",LOGH_index).index(b"LOGH")
    print(f"LOGH offset: {LOGH_offset:02X}")

    LOGH_version = int(GHG_bytes[LOGH_offset+4:LOGH_offset+8].hex(), 16)

    if LOGH_version in (16,17) :
        LOGH_11()
    elif LOGH_version in (4, 9,10):
        LOGH_0A()
    else:
        print(f"Unsupported LOGH version {LOGH_version:02X}")

def LOGH_11():

    ROTV_data_start_offset = LOGH_offset + 4 + 4 + 4 + 4
    current_bone_till_end = GHG_bytes[ROTV_data_start_offset:]
    ROTV_items_count = int(GHG_bytes[LOGH_offset+12:LOGH_offset+16].hex(), 16)

    for k in range(ROTV_items_count):
        namelen = int(current_bone_till_end[:2].hex(), 16) - 1
        current_bone_name = current_bone_till_end[2:2+namelen].decode("utf-8", errors="replace")
        c = 2 + namelen + 1 + 64 + 12 + 2 # skip over everything else, we only need bone names
        bone_names.append(current_bone_name)
        current_bone_till_end=current_bone_till_end[c:]

def LOGH_0A():

    # get strings list
    default_string_offset = GHG_bytes.index(b"default_string")
    name_strings = GHG_bytes[default_string_offset:]

    ROTV_data_start_offset = LOGH_offset + 4 + 4 + 4
    current_bone_till_end = GHG_bytes[ROTV_data_start_offset:]
    ROTV_items_count = int(GHG_bytes[LOGH_offset+8:LOGH_offset+12].hex(), 16)

    for k in range(ROTV_items_count):
        name_string_offset = int(current_bone_till_end[:4].hex(), 16)
        current_bone_name = name_strings[name_string_offset:].split(b'\x00', 1)[0].decode("utf-8", errors="replace")
        c = 4 + 64 + 12 + 2 # skip over everything else, we only need bone names
        bone_names.append(current_bone_name)
        current_bone_till_end=current_bone_till_end[c:]

def get_extractor_output():
    
    global extractor_output
    
    extractor_names = ("ExtractDx11MESHFix.exe", "ExtractNxgMESHFix.exe", "ExtractDx11MESH.exe", "ExtractNxgMESH.exe")

    # find extractor name
    for i in extractor_names:
        if (script_directory/i).exists():
            extractor_name = i
            print(f"Using extractor {extractor_name}.")
            break

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


def get_vertexlist_offsets() -> dict[int:int]:
    """
    Get a list of all vertex lists with offsets for their
    start from the extractor output.
    """
    global number_of_parts
    number_of_parts = get_extractor_value("Number of Parts: 0x")
    
    global vertexlists; global skinned_vertexlist_ids
    vertexlists = {} # dict[id:{"offset":offset, "attrlengths_sum":attrlenghts_sum}]]
    skinned_vertexlist_ids = []
    
    c=0
    while True:
        try:
            id = get_extractor_value("New Vertex List 0x", c, length=4)
        except ValueError:
            break
        
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
        for i in KEYWORD_ATTRLENGTHS.keys():
            attrlenghts_sum +=(
                vertexlist_info_raw.count(i)
                *KEYWORD_ATTRLENGTHS[i]
            )
        
        start_offset = get_file_offset("Number of Vertices: ", c)

        vertexlists[id]=({
            "offset":start_offset,
            "attrlengths_sum":attrlenghts_sum
        })

        skinned_vertexlist_ids.append(id)

        c+=1


def write_part_blends():

    for part_index, part_info in parts.items():

        start_string_index = extractor_output.index(f"Part {part_index:#010x}")
        if part_index == number_of_parts-1:
            end_string_index = -1
        else:
            end_string_index = extractor_output.index(f"Part {part_index+1:#010x}")
        
        extractor_part_info = (
            extractor_output[
                start_string_index:
                end_string_index
            ]
        )

        for j in skinned_vertexlist_ids:
            if (
                f"New Vertex List {j:#06x}" in extractor_part_info or
                f"Vertex List Reference to {j:#06x}" in extractor_part_info
            ):
                part_vertexlist_id = j
                break
        else:
            print(f"Part {part_index} was skipped due to not being skinned in the GHG.")
            continue

        offset_vertices = get_extractor_value("Offset Vertices: 0x", string=extractor_part_info)
        number_vertices = get_extractor_value("Number Vertices: 0x", string=extractor_part_info)

        bone_indices_raw = (
            get_extractor_value(
                "Number Vertices: 0x",
                offset = 8 + 1 + 8 + 5, #number vertices + newline + file offset + 5 spaces
                length = 80,
                integer_output = False,
                string = extractor_part_info
            )
        )
        has_bone_indices = len(bone_indices_raw)==80

        if len(part_info) != number_vertices:
            print(f"Part {part_index} was skipped due to not being the same across blender and the GHG.")
            continue

        used_bone_names = set()

        skip_part=False
        for item in part_index:
            for bone_name in item["blendnames"]:
                used_bone_names.add(bone_name)
            if len(item["blendnames"]) > 4:
                skip_part = True
                break
        if skip_part:
            print(f"Part {part_index} was skipped due to some vertices using too many bones (more than 4).")
            continue
                
        if len(used_bone_names) > 27 and has_bone_indices:
            print(f"Part {part_index} was skipped due to using too many bones (more than 27).")
            continue

        used_bone_names = tuple(used_bone_names)
        if has_bone_indices:
            bone_indices_offset = (
                get_extractor_value(
                    "Number Vertices: 0x",
                    offset=8 + 1,
                    length=8,
                    integer_output=True,
                    string=extractor_part_info
                )
            )
            bone_indices_ints = []
            bone_names_in_order_of_use = []
            skip_part
            for bone in used_bone_names:
                try:
                    bone_indices_ints.append(bone_names.index(bone))
                    bone_names_in_order_of_use.append(bone_names[bone_names.index(bone)])
                except ValueError:
                    skip_part = True
                    break
            if skip_part:
                print(f"Part {part_index} was skipped due to using bones that don't exist in the GHG.")
                continue #todo: add ADDITIONALMODEL support by letting the user choose a SUPER_CHARACTER skeleton to link to

            
            bone_indices_to_write = (
                bytes(bone_indices_ints)
                + [0xFF] * (27 - len(bone_indices_ints))
            )

            global GHG_bytes; GHG_bytes = (
                GHG_bytes[:bone_indices_offset] +
                bone_indices_to_write +
                GHG_bytes[bone_indices_offset+27:]
            )
        else:
            bone_names_in_order_of_use = bone_names

        used_vertexlist_offset = vertexlists[part_vertexlist_id]["offset"]
        used_vertexlist_attrlensum = vertexlists[part_vertexlist_id]["attrlengths_sum"]

        for i in range(number_vertices):
            blendindices_bytes = bytes()
            blendweights_bytes = bytes()
            for blendname, blendweight in zip(
                part_info["blendnames"], part_info["blendweights"]
            ):
                blendindices_bytes += bytes(
                    bone_names_in_order_of_use.index(blendname)
                )
                blendweights_bytes += bytes(
                    int(blendweight * 255)
                )

            blendindices_bytes += (4 - len(part_info["blendnames"])) * [0x00]
            blendweights_bytes += (4 - len(part_info["blendweights"])) * [0x00]
            
            GHG_bytes = (
                GHG_bytes[:
                  + used_vertexlist_offset
                  + (i+1)*used_vertexlist_attrlensum
                  - 8
                ]

                +blendindices_bytes
                +blendweights_bytes

                +GHG_bytes[
                  + used_vertexlist_offset
                  + (i+2)*used_vertexlist_attrlensum
                :]
            )
        

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