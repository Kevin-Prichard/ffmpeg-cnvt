# ffmpeg-cnvt.py  

Description: Python wrapper around ffmpeg and ffprobe to perform basic conversions, alterations and muxing.  
Author: Josh Buchbinder  

## General usage

`ffmpeg-cnvt` is a python script that uses ffmpeg to perform some basic media file manipulation.
The only required arguments are input and output specifiers.  
The input may be a single file,
multiple files using a wildcard or a printf style numeric identifier such as `TLPS%04d.JPG`
when using the -sequence option.  
The output may be a single file or a directory where output files will be created.  

By default `ffmpeg-cnvt` will attempt to copy all streams of all types from the input to the output without
processing them, which can be used to change the media container using `-container`.
Note that some stream types or codecs may not be supported with all containers.  

To copy only a single stream of a type from the primary input, for instance the second audio stream, the `-singlestream` 
option can be used such as `-singlestream a 1` where `a` indicates audio streams and `1` is 
the zero based index of audio streams in the input file.  To prevent streams of 
a type from being copied from the primary input, `-nocopy` can be used.  

To add additional streams to the output other than those from the primary input, `-addfile`  

