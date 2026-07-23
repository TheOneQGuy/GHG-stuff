# GHG-stuff
Tools for working with GHG skeleton and skinning stuff. AI help was used for some parts. Currently unfinished.

## GogHogGog (main tool)
Blender plugin allowing you to import the skeleton and skinned parts from an NXG/DX11/NTT GHG file. No exporting yet. Install via Edit > Preferences > Add-Ons > Install from Disk...

## ExtractGHGSkinning
Gets the skeleton plus blend weight data for each part from a GHG. Doesn't output anything on its own and is supposed to be used for other projects because it doesn't have the blender stuff.

## LogHog
Extracts a json containing a GHG's skeleton data such as name, index, parent index, position, rotation, and scale for each bone. Useful for correcting bone indices when manually porting GHG to a slightly different skeleton.
