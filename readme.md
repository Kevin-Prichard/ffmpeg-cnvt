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

### Selecting container

By default `ffmpeg-cnvt` will attempt to copy all streams of all types from the input to the output without
processing them, which can be used to change the media container using `-container`.
Note that some stream types or codecs may not be supported with all containers.  

### Selecting streams

To copy only a single stream of a type from the primary input, for instance the second audio stream, the `-singlestream`
option can be used such as `-singlestream a 1` where `a` indicates audio streams and `1` is
the zero based index of audio streams in the input file.  To prevent streams of
a type from being copied from the primary input, `-nocopy` can be used.  

### Adding additional streams

To add additional streams to the output other than those from the primary input, `-addfile`
is used to specify the stream type and path to the media file to import streams from.  
As with the primary input, all streams of the selected type will be copied unless
`-addstream` is used to specify a specific stream.

### Ordering streams

By default, streams added using `-addfile` will be ordered after the streams of
the same type from the primary input unless `-addfirst` is used.  The first of
the added streams can be marked as the default stream of a type using `-adddefault`,
all other streams of that type from the primary or secondary inputs will be marked
as not default.

### Limiting length and looping streams

When combining streams of different lengths, ffmpeg by default will continue encoding
until the end of the last stream is reached.  The length can be limited by specifying
a time period using `-duration` or limited to the length of the shortest stream
using `-shortest`.  This can be combined with `-addlooped` to have the streams of a
shorter added input looped.  

### Reencoding streams

To process streams rather than simply copying them, a codec must be specified using
`-codec` or `-addcodec`.  To display the codecs currently supported by `ffmpeg-cnvt`
use `-codecs`.  There are additional arguments (`-tune`, `-preset`, `-bitrate`,
`-mono`, `-stereo` etc) that can be used to control the encoding codec parameters.

### Resizing video

By default, `ffmpeg-cnvt` will output video at the same resolution it was input.
If a resolution is specified using one of the resolution arguments (`-hd`, `-fhd`,
`-qhd`, `-uhd`) or by specifying `-width` and/or `-height` the output will be
scaled to fit within the supplied dimensions while maintaining the aspect ratio.
If `-crop` is used, the output video will be cropped from the input video using
the position supplised to `-crop`.
