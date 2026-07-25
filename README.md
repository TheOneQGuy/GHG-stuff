# GHG-stuff
Tools for working with GHG skeleton and skinning stuff. AI help was used for some parts.

## Modify-GHG-Skinning (main tool)
Blender plugin allowing you to import the skeleton and skinned parts from an NXG/DX11/NTT GHG file. You can then change the skinning of the parts based on the exsiting skeleton and export the updated skinning back to the GHG (Custom skinnned meshes finally!). Install via Edit > Preferences > Add-Ons > Install from Disk...

## LogHog
Extracts a json containing a GHG's skeleton data such as name, index, parent index, position, rotation, and scale for each bone. Useful for correcting bone indices when manually porting GHG to a slightly different skeleton.

## import_from_ghg_template
Gets the skeleton plus blend weight data for each part from a GHG. Doesn't output anything on its own and is supposed to be used for other projects because it doesn't have the blender stuff and thus is easier to read the GHG stuff from it.

## export_to_ghg_template
Same as above but for importing the skinning back to the GHG from a parts dictionary.
