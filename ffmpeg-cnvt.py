#!/usr/bin/env python3

# ffmpeg-cnvt.py - Python wrappr for ffmpeg to do simple video file conversions and muxing.

__version_info__ = (0, 9, 1)
__version__ = '.'.join([str(v) for v in __version_info__])
__author__ = "Josh Buchbinder"
__copyright__ = "Copyright 2021, Josh Buchbinder"

import argparse
import datetime
import errno
import glob
import os
import pathlib
import platform
import subprocess
import sys
import time

HELP_EPILOG = """* Argument may be used multiple times with different stream types.  If multiple arguments with the same stream type are supplied the first will be used and the rest will be ignored.

This script will perform simple media file conversions and muxing using ffmpeg.

- Notes:
. Unless options are provided specifying otherwise, \
all streams (video, audio, subtitle) and chapters will be copied without processing.
. Stream indexes are relative to the stream type, not the total stream index, so 0 is the first stream of that type.
. If video is scaled, output pixel aspect ratio will be 1:1.
. Video aspect ratio will always be maintained, so only width or height need be provided.  \
Video will be resized to fit within output dimensions.
. Streams are always output in the order Video -> Audio -> Subtitles -> Data -> Attachments.
. You can only specify audio and video options once so it is not possible to process multiple streams with different settings.

- Examples:
. Convert mp4 to mkv copying all streams and chapters and metadata:
python ffmpeg-cnvt.py input_file.mp4 output_file.mkv
. Convert multiple mkv files in /in to mp4/h264/aac files in /out (low common denominator for media files):
python ffmpeg-cnvt.py /in/*.mkv /out -container mp4 -codec a aac -codec v h264 -convertvonly -convertaonly -suffix
. Convert video streams to h265, copy all other streams:
python ffmpeg-cnvt.py input_file.mkv output_file.mkv -codec v h265 -crf 17
. Extract a specific audio stream to mp3 file:
python ffmpeg-cnvt.py input_file.mkv output_file.mp3 -codec a mp3 -singlestream a 0 -nocopy v -nocopy s 
. Convert JPEG image sequence to UHD h265 rec709 video by croppping images in the center:
python ffmpeg-cnvt.py /imgs/TLPS%04d.JPG output_file.mp4 -sequence -crop center -uhd -vcodec h265 -sdr -8bit
. Output mp4 with video/chapters/subtitles from input_video.mp4 and audio from input_audio.mp3 looped:
python ffmpeg-cnvt.py input_video.mp4 output.mp4 -nocopy a -shortest -addcodec a aac -addlooped a -addfile a input_audio.mp3
. Extract SRT subtitles from subrip stream:
python ffmpeg-cnvt.py input_file.mkv output_file.srt -singlestream s 0 -nocopy v -nocopy s -nocopy c
. Copy mkv and attach cover image:
python ffmpeg-cnvt.py input_file.mkv output_file.mkv -addattach cover.jpg -attachtype image/jpeg
. Re-encode the audio track of a file to AAC and add as an additional track:
python ffmpeg-cnvt.py input_file.mkv output_file.mkv -addfile a input_file.mkv -addcodec a aac
"""

LOG_FILENAME = "ffmpeg-cnvt.log"
LOG_RETRY_COUNT = 5
LOG_RETRY_DELAY = 0.3
SEC_PER_MIN = 60
SEC_PER_HOUR = SEC_PER_MIN * 60
SEC_PER_DAY = SEC_PER_HOUR * 24
SEC_PER_WEEK = SEC_PER_DAY * 7
ATTACHTYPE_DEFAULT = "application/octet-stream"
SPECIAL_CHARS = " $^'"

AUDIO_CODECS = ["aac", "ac3", "eac3", "dts", "flac", "opus", "mp3", "wav"]
VIDEO_PRESET_ARGS = ["h264", "h265", "h264nv", "h265nv"]
VIDEO_CRF_ARGS = ["h264", "h265", "vp8", "vp9"]
VIDEO_CODECS = list(set(VIDEO_PRESET_ARGS) | set(VIDEO_CRF_ARGS))
SUBTITLE_CODECS = ["ssa", "ass", "dvbsub", "dvdsub", "srt", "sub"]
CONTAINER_ARGS = ["mov", "mkv", "mp4"]
RES_ARGS = ["hd", "fhd", "qhd", "uhd"]
DEPTH_ARGS = ["8bit", "10bit"]
COLOR_ARGS = ["sdr", "hdr"]
LENGTH_ARGS = ["shortest", "duration"]
CHANNELS_ARGS = ["mono", "stereo"]
STREAM_TYPES_LONG = ["video", "audio", "subtitle", "attachment", "data"]
STREAM_TYPES_SHORT = ["v", "a", "s", "t", "d"]
STREAM_TYPES_DICT = dict(zip(STREAM_TYPES_SHORT, STREAM_TYPES_LONG))
PRESET_CHOICES = ["slowest", "slow", "medium", "fast", "default", "hp", "hq", "bd", "ll",
                  "llhq", "llhp", "lossless", "losslesshp", "p1", "p2", "p3", "p4", "p5", "p6", "p7"]
TUNE_CHOICES = ["hq", "ll", "ull", "lossless", "psnr", "ssim"]


# Bit of a hacky way of validating argparse nargs=2
def static_vars(**kwargs):
    def decorate(func):
        for k in kwargs:
            setattr(func, k, kwargs[k])
        return func
    return decorate


# Validates argparse nargs=2 first arg is stream type second arg is int
@static_vars(firstarg=True)
def ValidateStreamIntArg(value):
    if ValidateStreamIntArg.firstarg:
        if not value in STREAM_TYPES_SHORT:
            raise ValueError("Invalid stream type: {}".format(value))
    else:
        value = int(value)
    ValidateStreamIntArg.firstarg = not ValidateStreamIntArg.firstarg
    return value


# Validates argparse nargs=2 first arg is stream type second arg is input file
@static_vars(firstarg=True)
def ValidateStreamFileArg(value):
    if ValidateStreamFileArg.firstarg:
        if not value in STREAM_TYPES_SHORT:
            raise ValueError("Invalid stream type: {}".format(value))
    else:
        argparse.FileType('r')(value)
    ValidateStreamFileArg.firstarg = not ValidateStreamFileArg.firstarg
    return value


# Validates argparse nargs=2 first arg is stream type second arg is string
@static_vars(firstarg=True)
def ValidateStreamStringArg(value):
    if ValidateStreamStringArg.firstarg:
        if not value in STREAM_TYPES_SHORT:
            raise ValueError("Invalid stream type: {}".format(value))
    ValidateStreamStringArg.firstarg = not ValidateStreamStringArg.firstarg
    return value


# Validates argparse nargs=2 first arg is stream type second arg is codec for that stream
@static_vars(lasttype=None)
def ValidateStreamCodecArg(value):
    if not ValidateStreamCodecArg.lasttype:
        if not value in STREAM_TYPES_SHORT:
            raise ValueError("Invalid stream type: {}".format(value))
        ValidateStreamCodecArg.lasttype = value
    else:
        if 'v' == ValidateStreamCodecArg.lasttype:
            if not value in VIDEO_CODECS:
                raise ValueError("Invalid video codec: {}".format(value))
        elif 'a' == ValidateStreamCodecArg.lasttype:
            if not value in AUDIO_CODECS:
                raise ValueError("Invalid audio codec: {}".format(value))
        elif 's' == ValidateStreamCodecArg.lasttype:
            if not value in SUBTITLE_CODECS:
                raise ValueError("Invalid subtitle codec: {}".format(value))
        else:
            raise ValueError("Codecs not currently supported for stream type {}".format(
                ValidateStreamCodecArg.lasttype))
        ValidateStreamCodecArg.lastype = None
    return value


# Creates and populates the argparse ArgumentParser
def create_argparser():

    def list_to_choice_str(l):
        return "{" + ','.join(l) + "}"

    epilog = "\n(Stream types: {} m=metadata c=chapters)\n".format(
        ' '.join(["{}={}".format(s, l) for s, l in STREAM_TYPES_DICT.items()]))
    epilog += HELP_EPILOG

    parser = argparse.ArgumentParser(description='Convert video with ffmpeg.',
                                     epilog=epilog, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--version', action="version", version=__version__)
    parser.add_argument(
        "input", help="Input file or wildcard or printf formatted input.")
    parser.add_argument("output", help="Output file or directory.")
    parser.add_argument('-container', choices=CONTAINER_ARGS,
                        help="Specify output container.")
    parser.add_argument('-codec', nargs=2, action='append', type=ValidateStreamCodecArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'CODEC'), help="Process streams of a type from primary input with encoding codec. *")
    parser.add_argument('-bitrate', nargs=2, action='append', type=ValidateStreamStringArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'RATE'), help="Set bitrate for streams of a type.  Can be in the form 1M or 300K. *")
    parser.add_argument('-lang', nargs=2, action='append', type=ValidateStreamStringArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'LANG'), help="Set language for primary streams of a type. *")
    parser.add_argument('-nocopy', choices=STREAM_TYPES_SHORT + ['c', 'm'], action='append',
                        help="Do not copy or process streams of this type. *")
    parser.add_argument('-singlestream', nargs=2, action='append', type=ValidateStreamIntArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'INDEX'), help="Process a single stream instead of all streams of a type. *")
    parser.add_argument('-convertonly', choices=STREAM_TYPES_SHORT, action='append',
                        help="Do not process streams of a type if their encoding codec matches the output codece. *")
    parser.add_argument('-addfile', nargs=2, action='append', type=ValidateStreamFileArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'INDEX'), help="Add streams of a type from a second input file. *")
    parser.add_argument('-addcodec', nargs=2, action='append', type=ValidateStreamCodecArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'CODEC'), help="Codec to be used for added streams of a type. *")
    parser.add_argument('-addstream', nargs=2, action='append', type=ValidateStreamIntArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'INDEX'), help="When adding streams from a file, process a single stream instead of all streams. *")
    parser.add_argument('-addfirst', choices=STREAM_TYPES_SHORT, action='append',
                        help="Order these types of added streams first (t is aTtachment). *")
    parser.add_argument('-adddefault', choices=STREAM_TYPES_SHORT, action='append',
                        help="Set the first added stream of this type to the default stream.  Others will be set not default. *")
    parser.add_argument('-addlang', nargs=2, action='append', type=ValidateStreamStringArg, metavar=(list_to_choice_str(
        STREAM_TYPES_SHORT), 'LANG'), help="Set language for added streams of a type. *")
    parser.add_argument('-addlooped', choices=STREAM_TYPES_SHORT, action='append',
                        help="Loop added input streams of a type. *")
    audiochannelgroup = parser.add_mutually_exclusive_group()
    audiochannelgroup.add_argument(
        '-mono', action="store_true", help="Output mono audio.")
    audiochannelgroup.add_argument(
        '-stereo', action="store_true", help="Output stereo audio.")
    parser.add_argument('-preset', choices=PRESET_CHOICES,
                        help="Preset settings for video encoder.")
    parser.add_argument('-tune', choices=TUNE_CHOICES,
                        help="Tune option for video encoding.")
    parser.add_argument(
        '-tier', choices=["main, high"], help="Specify output video tier.")
    parser.add_argument('-crf', type=int, help="Video encoding CRF value.")
    parser.add_argument('-deshake', action="store_true",
                        help="Process video with deshake filter.")
    depthgroup = parser.add_mutually_exclusive_group()
    depthgroup.add_argument('-8bit', action="store_true",
                            help="Output 8 bit yuv420p video.")
    depthgroup.add_argument('-10bit', action="store_true",
                            help="Output 10 bit yuv420p10le video.")
    colorspacegroup = parser.add_mutually_exclusive_group()
    colorspacegroup.add_argument(
        '-sdr', action="store_true", help="Output bt709 SDR video.")
    colorspacegroup.add_argument(
        '-hdr', action="store_true", help="Output bt2020 HDR video.")
    resolutiongroup = parser.add_mutually_exclusive_group()
    resolutiongroup.add_argument(
        '-hd', action="store_true", help="Output 1280x720 video.")
    resolutiongroup.add_argument(
        '-fhd', action="store_true", help="Output 1920x1080 video.")
    resolutiongroup.add_argument(
        '-qhd', action="store_true", help="Output 2560x1440 video.")
    resolutiongroup.add_argument(
        '-uhd', action="store_true", help="Output 3840x2160 video.")
    parser.add_argument('-width', type=int, help="Output video width.")
    parser.add_argument('-height', type=int, help="Output video height.")
    parser.add_argument('-crop',
                        choices=["center", "left", "right", "top", "bottom",
                                 "topleft", "topright", "bottomleft", "bottomright"],
                        help="Crop video instead of resizing.")
    parser.add_argument('-padding', type=int, default=4,
                        help="Output resolution padding, most codecs must be divisible by 2 or 4. (Default=4).")
    parser.add_argument("-sequence", action="store_true",
                        help="Input is a printf formatted image sequence.")
    parser.add_argument("-framerate", default="30000/1001",
                        help="Output frame rate, can be numerator/denominator (default=30000/1001).")
    parser.add_argument('-nounknown', action="store_true",
                        help="Strip unknown data from output.")
    parser.add_argument(
        '-addattach', metavar='PATHTOFILE', help="Attach file as an attachment stream.")
    parser.add_argument('-attachtype', default=ATTACHTYPE_DEFAULT, metavar='MIMETYPE',
                        help="Mime type for attached file (default={}).".format(ATTACHTYPE_DEFAULT))
    lengthgroup = parser.add_mutually_exclusive_group()
    lengthgroup.add_argument('-shortest', action="store_true",
                             help="Stop encoding when shortest track is finished.")
    lengthgroup.add_argument(
        '-duration', metavar='TIME', help="Duration of output, can be seconds or in the form hh:mm:ss[.xxx].")
    parser.add_argument('-stoponerror', action="store_true",
                        help="Stop if ffmpeg returns an error.")
    parser.add_argument('-setaffinity', action="store_true",
                        help="Remove CPU core 0 from affinity mask of ffmpeg subprocess.")
    parser.add_argument('-dryrun', action="store_true",
                        help="Do not execute ffmpeg, display command line values.")
    parser.add_argument('-overwrite', action="store_true",
                        help="Overwrite existing files.")
    parser.add_argument('-verbose', action="store_true",
                        help="Enable verbose console output.")
    parser.add_argument('-ffmpegbin', default="ffmpeg", metavar='PATHTOBIN',
                        help="Specify the ffmpeg binary to use (default=ffmpeg).")
    parser.add_argument('-ffprobebin', default="ffprobe", metavar='PATHTOBIN',
                        help="Specify the ffmpeg binary to use (default=ffprobe).")
    parser.add_argument('-nolog', action="store_true",
                        help="Do not log to ~/{}".format(LOG_FILENAME))
    parser.add_argument('-logfile', metavar='PATHTOLOG',
                        help="File to output logging to (default=~/{}).".format(LOG_FILENAME))
    parser.add_argument('-suffix', action="store_true",
                        help="Add suffix to file name showing encoding options.")

    return parser


# Returns the input string with quotes around if it contains special characters
def quote_if_needed(text):
    if any(elem in text for elem in SPECIAL_CHARS):
        return '"' + text + '"'
    return text


# Returns a command line string quoted as needed from a list of string elements
def cmdline_str(cmdline):
    return ' '.join([quote_if_needed(elem) for elem in cmdline])


# Executes ffprobe on a file and returns the output as a string
def probe_string(verbose, ffprobe_bin, input_file, probe_args):
    args = [ffprobe_bin] + probe_args + [input_file]
    if verbose:
        print(cmdline_str(args))
    proc = subprocess.run(args, capture_output=True)
    return proc.stdout.decode('utf-8').strip()


# Queries a video stream property using ffprobe and returns the output from ffprobe as a string
# stream_num and stream_spec default to the first video stream
def probe_media_property(verbose, ffprobe_bin, input_file, property, stream_num=0, stream_spec='v'):
    probe_args = ["-v", "error", "-select_streams", "{}:{}".format(stream_spec, stream_num),
                  "-show_entries", "stream=" + property, "-of", "default=nw=1:nk=1"]
    return probe_string(verbose, ffprobe_bin, input_file, probe_args)


# Returns the count of a specific stream type in a file using ffprobe
def probe_stream_count(verbose, ffprobe_bin, input_file, stream_type):
    probe_args = ["-v", "error", "-show_entries", "stream=codec_type"]
    probe_str = probe_string(verbose, ffprobe_bin, input_file, probe_args)
    if stream_type:
        return probe_str.count("codec_type=" + stream_type)
    return probe_str


# Gets first filename matching printf style path
def printf_filename(input_path):
    pct_pos = input_path.rfind('%')
    if -1 == pct_pos:
        return input_path
    d_pos = input_path.find('d', pct_pos)
    if -1 == d_pos:
        return input_path
    try:
        chars = int(input_path[pct_pos + 1:d_pos])
    except:
        print("Unable to parse printf %01d string in path")
        return input_path

    return input_path[0:pct_pos] + '1'.zfill(chars) + input_path[d_pos + 1:]


# Returns a string representing length of time in seconds
def timer_string(seconds):
    ret_str = ""
    if seconds >= SEC_PER_WEEK:
        ret_str += "{} weeks ".format(int(seconds / SEC_PER_WEEK))
        seconds %= SEC_PER_WEEK
    if seconds >= SEC_PER_DAY:
        ret_str += "{} days ".format(int(seconds / SEC_PER_DAY))
        seconds %= SEC_PER_DAY
    if seconds >= SEC_PER_HOUR:
        ret_str += "{} hours ".format(int(seconds / SEC_PER_HOUR))
        seconds %= SEC_PER_HOUR
    if seconds >= SEC_PER_MIN:
        ret_str += "{} mins ".format(int(seconds / SEC_PER_MIN))
        seconds %= SEC_PER_MIN
    ret_str += "{} seconds".format(int(seconds))
    return ret_str


# Executes a subprocess and sets the CPU affinity to not use the primary core
def run_with_afinity(cmdline, setaffinity, verbose):
    proc = subprocess.Popen(cmdline)
    if setaffinity:
        # Remove core 0 from affinity mask
        # Note: This method is only available on some UNIX platforms.
        affinity = os.sched_getaffinity(proc.pid)
        affinity_mask = affinity[1:]
        if verbose:
            print("PID {}: Changing CPU affinity mask {} to {}".format(
                proc.pid, affinity, affinity_mask))
        os.sched_setaffinity(proc.pid, affinity_mask)

    #  Wait for process to terminate and return returncode
    return proc.wait()


# Writes text to log file
def log_to_file(message, args, no_print=False):
    if not no_print or args.verbose:
        print(message)

    if args.nolog:
        return

    if args.logfile:
        if os.path.isdir(args.logfile):
            log_file_name = args.logfile + '/' + LOG_FILENAME
        else:
            log_file_name = args.logfile
    else:
        log_file_name = str(pathlib.Path.home()) + '/' + LOG_FILENAME
    header = "[{}][{}] ".format(str(os.getpid()).zfill(
        6), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    for _ in range(LOG_RETRY_COUNT):
        try:
            with open(log_file_name, "at") as log_file:
                log_file.write(header + message + "\n")
            break
        except Exception as e:
            print("Retrying log write... ({})".format(e))
            time.sleep(LOG_RETRY_DELAY)
            continue


# Returns a string built from command line arguments set
def suffix_str(args, width, height):
    retstr = ""
    sep = '_'

    for stream_type in STREAM_TYPES_SHORT:
        if stream_type_bool(args.nocopy, stream_type):
            retstr += sep + "no" + STREAM_TYPES_DICT[stream_type]
        elif stream_type_bool(args.codec, stream_type):
            retstr += sep + \
                video_arg_to_codec(stream_type_arg(args.codec, stream_type))

            if 'v' == stream_type:
                if args.sdr:
                    retstr += sep + "SDR"
                elif args.hdr:
                    retstr += sep + "HDR"
                if args.crf:
                    retstr += sep + "CRF-{}".format(args.crf)

                if args.preset:
                    retstr += sep + "preset-{}".format(args.preset)

                if args.tune:
                    retstr += sep + "tune-{}".format(args.tune)

                if width or height:
                    retstr += sep + "{}x{}".format(width, height)
                    if args.crop:
                        retstr += sep + "crop={}".args.crop

            if 'a' == stream_type:
                if args.mono:
                    retstr += "-1.0"
                elif args.stereo:
                    retstr += "-2.0"

    return retstr


# Round up to closest resolution divisible by padding
def pad_resolution(res, padding):
    mod = res % padding
    if not mod:
        return res
    return res + padding - mod


# Returns codec id from video argument
def video_arg_to_codec(arg):
    if arg == "h264nv":
        return "h264"
    elif arg == "h264n5":
        return "h264"
    return arg


# Returns codec id from audio argument
def audio_arg_to_codec(arg):
    return arg


# Returns the encoder string for a video codec name
def video_codec_to_encoder(codec, args):
    if codec is None:
        return ["copy"]

    if not codec in VIDEO_CODECS:
        raise Exception("Invalid video codec: {}".format(codec))

    # Video codec
    if codec == "h264":
        output_vcodec = ["libx264"]
    elif codec == "h265":
        output_vcodec = ["libx265"]
    elif codec == "h264nv":
        output_vcodec = ["h264_nvenc"]
    elif codec == "h265nv":
        output_vcodec = ["hevc_nvenc"]
    elif codec == "vp8":
        output_vcodec = ["libvpx"]
    elif codec == "vp9":
        output_vcodec = ["libvpx-vp9"]
    else:
        output_vcodec = [codec]

    # Preset
    if args.preset and codec in VIDEO_PRESET_ARGS:
        output_vcodec += ["-preset", args.preset]

    # CRF
    if args.crf and codec in VIDEO_CRF_ARGS:
        output_vcodec += ["-crf", str(args.crf)]

    # Tune
    if args.tune:
        output_vcodec += ["-tune", args.tune]

    # Tier
    if args.tier:
        if args.tier == "main":
            output_vcodec.append("-no-high-tier")
        elif args.tier == "high":
            output_vcodec.append("-high-tier")

    # Video bitrate
    if args.vbitrate:
        output_vcodec += ["-b:v", args.vbitrate]

    # Pixel format
    if getattr(args, "8bit"):
        output_vcodec += ["-pix_fmt", "yuv420p"]
    elif getattr(args, "10bit"):
        output_vcodec += ["-pix_fmt", "yuv420p10le"]

    # Color space
    if args.sdr:
        output_vcodec += ["-colorspace:v", "bt709", "-color_primaries:v", "bt709", "-color_trc:v", "bt709",
                          "-color_range:v", "tv"]
    elif args.hdr:
        output_vcodec += ["-x265-params",
                          'keyint=60:bframes=3:vbv-bufsize=75000:vbv-maxrate=75000:hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display="G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,500)":max-cli=0,0']

    return output_vcodec


# Returns the encoder string for a audio codec name
def audio_codec_to_encoder(codec, args):
    if codec is None:
        return ["copy"]

    if not codec in AUDIO_CODECS:
        raise Exception("Invalid audio codec: {}".format(codec))

    # Audio codec
    if "opus" == codec:
        output_acodec = ["opus", "-strict", "-2"]
    elif "dts" == codec:
        output_acodec = ["dca"]
    elif "mp3" == codec:
        output_acodec = ["libmp3lame"]
    elif "wav" == codec:
        output_acodec = ["wavpack"]
    else:
        output_acodec = [codec]

    # Channels
    if args.stereo:
        output_acodec += ["-ac", "2"]
    elif args.mono:
        output_acodec += ["-ac", "1"]

    return output_acodec


# Returns the encoder string for an audio codec name
def subtitle_codec_to_encoder(codec, args):
    if codec is None:
        return ["copy"]

    if not codec in SUBTITLE_CODECS:
        raise Exception("Invalid subtitle codec: {}".format(codec))

    if "sub" == codec:
        output_scodec = ["subrip"]
    else:
        output_scodec = [codec]

    return output_scodec


# Returns the encoder string for a codec name of a stream type
def stream_codec_to_encoder(stream_type, codec, args):
    if 'v' == stream_type:
        cmdline = video_codec_to_encoder(codec, args)
    elif 'a' == stream_type:
        cmdline = audio_codec_to_encoder(codec, args)
    elif 's' == stream_type:
        cmdline = subtitle_codec_to_encoder(codec, args)
    else:
        cmdline = []

    # Stream bitrate
    if stream_type_bool(args.bitrate, stream_type):
        cmdline += ["-b:{}".format(stream_type),
                    stream_type_arg(args.bitrate, stream_type)]

    return cmdline


# Checks for mutually exclusive arguments
def check_exclusive_args(args, arg_names):
    set_args = [name for name in arg_names if bool(getattr(args, name))]
    if len(set_args) > 1:
        raise Exception(
            "Mutually exclusive arguments: {}".format(' '.join(set_args)))


# Check for arguments dependent on one of another set of argument
def check_dependent_args(args, arg_names, requires_names):
    for arg_name in arg_names:
        if getattr(args, arg_name) and not sum(bool(getattr(args, name)) for name in requires_names):
            raise Exception("Argument '{}' requires one of: '{}'".format(
                arg_name, ', '.join(requires_names)))


# Returns either an empty list, the input list, or the first item in a tuple list
def arg_stream_type_enumeration(arg):
    if not arg:
        return []
    if type(arg[0]) == tuple or type(arg[0] == list):
        return [val[0] for val in arg]
    return arg


# Checks for stream arguments dependant on other stream arguments of the same stream type
def check_arg_stream_dependencies(args, value_names, depend_arg_names):
    for value_name in value_names:
        for key in arg_stream_type_enumeration(getattr(args, value_name)):
            if not any(key in arg_stream_type_enumeration(getattr(args, depend_name)) for depend_name in depend_arg_names):
                raise Exception("Use of stream option '{}:{}' requires use of one of '{}' with same stream type".format(
                    value_name, key, ', '.join(depend_arg_names)))


# Check for mutually exclusive stream arguments of the same stream type
def check_arg_stream_exclusive(args, value_names):
    for value_name in value_names:
        for key in arg_stream_type_enumeration(getattr(args, value_name)):
            key_names = [val_name for val_name in value_names if key in arg_stream_type_enumeration(
                getattr(args, val_name))]
            if len(key_names) > 1:
                raise Exception("Mutually exclusive args for stream type '{}': {}".format(
                    key, ', '.join(key_names)))


# Checks for named arguments dependant on stream arguments of a type
def check_arg_stream_names_dependencies(args, value_names, depend_arg_names, stream_spec):
    for value_name in [name for name in value_names if getattr(args, name)]:
        for depend_arg_name in depend_arg_names:
            if not any(stream_spec in arg_stream_type_enumeration(getattr(args, depend_name)) for depend_name in depend_arg_names):
                raise Exception("Use of argument '{}' requires use of one of '{}' with stream type '{}'".format(
                    value_name, ', '.join(depend_arg_names), stream_spec))


# Checks for named arguments mutually exclusive with stream argument of a type
def check_arg_stream_names_exclusive(args, value_names, stream_arg_names, stream_spec):
    for value_name in [name for name in value_names if getattr(args, name)]:
        for stream_arg_name in stream_arg_names:
            if stream_spec in arg_stream_type_enumeration(getattr(args, stream_arg_name)):
                raise Exception("Mutually exclusive arg '{}' for stream type '{}:{}'".format(
                    value_name, stream_arg_name, stream_spec))


# Checks for invalid argument combinations
def check_valid_arguments(args):

    # Arguments that require another argument
    check_arg_stream_dependencies(args, ["bitrate"], ["codec", "addcodec"])
    check_arg_stream_dependencies(
        args, ["addcodec", "addstream", "addfirst", "adddefault", "addlang", "addlooped"], ["addfile"])
    check_arg_stream_dependencies(args, ["addlooped"], ["addcodec"])

    check_arg_stream_names_dependencies(args, ["tune", "crf", "crop"] +
                                        DEPTH_ARGS + COLOR_ARGS + RES_ARGS, ["codec", "addcodec"], 'v')
    check_arg_stream_names_dependencies(
        args, CHANNELS_ARGS, ["codec", "addcodec"], 'a')

    check_dependent_args(args, ["crop"], RES_ARGS + ["width", "height"])
    check_dependent_args(args, ["addlooped"], LENGTH_ARGS)

    # Arguments that are mutually exclusive
    check_arg_stream_exclusive(args, ["nocopy", "codec"])

    check_arg_stream_names_exclusive(args, RES_ARGS, ["nocopy"], 'v')

    check_exclusive_args(args, RES_ARGS + ["width"])
    check_exclusive_args(args, RES_ARGS + ["height"])


# Returns extension of output container based on args or input filename
def get_container_extension(args, input_file):
    # Output container
    if args.container:
        output_extension = "." + args.container
    else:
        # Use input extension
        output_extension = input_file[input_file.rfind('.'):]
    return output_extension


# Returns output width, height based on input arguments
def get_dimensions_from_args(args):
    output_width = 0
    output_height = 0

    # Change resolution
    if args.hd:
        output_width = 1280
        output_height = 720
    elif args.fhd:
        output_width = 1920
        output_height = 1080
    elif args.qhd:
        output_width = 2560
        output_height = 1440
    elif args.uhd:
        output_width = 3840
        output_height = 2160
    else:
        if args.width:
            output_width = args.width
        if args.height:
            output_height = args.height
    return output_width, output_height


# Returns the number of streams of each type for a file in a dictionary
# { 'v':1, 'a':2, 's':3, 'a':0, 'd':0 }
def get_stream_count_map_from_input(args, input_file):
    if args.verbose:
        print("Probing stream counts from {}".format(input_file))

    probe_str = probe_stream_count(
        args.verbose, args.ffprobebin, input_file, None)

    return_dict = {}

    for stream_type in STREAM_TYPES_SHORT:
        return_dict[stream_type] = probe_str.count(
            "codec_type=" + STREAM_TYPES_DICT[stream_type])

    return return_dict


# Returns the value for specific stream type in argparse 2 arg list, returns None if not found
def stream_type_arg(args, stream_type):
    if not args:
        return None
    for t, v in args:
        if t == stream_type:
            return v
    return None


# Returns True if stream_type is in args
def stream_type_bool(args, stream_type):
    if not args:
        return False
    return stream_type in args


# Returns mapping array and optional codec info for a specific type and index of stream
def stream_map_args(spec, file_index, none, stream_num, first_stream, stream_count, codec_args, metadata=None, default_stream=None):
    # Stream map
    cmdline = ["-map"]
    if none:
        cmdline += ["-{}:{}?".format(file_index, spec)]
    else:
        if stream_num is not None:
            cmdline += ["{}:{}:{}".format(file_index, spec, stream_num)]
        else:
            cmdline += ["{}:{}?".format(file_index, spec)]

        if codec_args:
            # Codec info
            for n in range(stream_count):
                cmdline += ["-c:{}:{}".format(spec,
                                              first_stream + n)] + codec_args[n]
                if default_stream is not None:
                    cmdline += ["-disposition:{}:{}".format(
                        spec, first_stream + n)]
                    if default_stream:
                        cmdline += ["default"]
                        # Only mark the first stream as default
                        default_stream = False
                    else:
                        cmdline += ["none"]
                if metadata:
                    cmdline += ["-metadata:s:{}:{}".format(
                        spec, first_stream + n), metadata]
    return cmdline


# Returns command line elements for a primary map
def stream_map(primary, args, stream_type, input_streams, first_stream, input_file):
    if primary:
        encoder = stream_type_arg(args.codec, stream_type)
    else:
        encoder = stream_type_arg(args.addcodec, stream_type)
    encoder_args = stream_codec_to_encoder(stream_type, encoder, args)

    # Either a per stream codec list or they are all the same
    encoder_list = []
    if primary and encoder_args and stream_type_bool(args.convertonly, stream_type):
        for n in range(first_stream, first_stream + input_streams):
            if args.verbose:
                print("Probing codec stream {}:{} from {}".format(
                    stream_type, n, input_file))
            input_audio_codec = probe_media_property(
                args.verbose, args.ffprobebin, input_file, "codec_name", n, stream_type)
            if input_audio_codec == encoder:
                print("Copying stream {}:{} because codec is already codec '{}'".format(
                    stream_type, n, encoder_args))
                encoder_list.append(["copy"])
            else:
                encoder_list.append(encoder_args)
    else:
        encoder_list = [encoder_args] * input_streams

    # Default stream (disposition)
    if primary:
        default_stream = False if stream_type_bool(
            args.adddefault, stream_type) else None
    else:
        default_stream = True if stream_type_bool(
            args.adddefault, stream_type) else None

    # Language metadata
    language = stream_type_arg(args.lang, stream_type) if primary else stream_type_arg(
        args.addlang, stream_type)
    metadata = None if not language else "language=" + language

    return stream_map_args(stream_type, 0, stream_type_bool(args.nocopy, stream_type),
                           stream_type_arg(args.singlestream, stream_type), first_stream, input_streams, encoder_list, metadata, default_stream)


# Builds video filter list from input arguments
def video_filters_from_args(args, output_width, output_height, input_file):
    output_vfilters = []
    # Color space
    if args.sdr:
        output_vfilters.append(
            "scale=in_color_matrix=auto:in_range=auto:out_color_matrix=bt709:out_range=tv")

    # Scale or crop
    if output_width or output_height:
        if args.sequence:
            input_path = printf_filename(input_file)
        else:
            input_path = input_file

        if args.verbose:
            print("Probing input dimensions: {}".format(input_path))

        try:
            input_height = int(probe_media_property(
                args.verbose, args.ffprobebin, input_path, "height"))
            input_width = int(probe_media_property(
                args.verbose, args.ffprobebin, input_path, "width"))
        except Exception as e:
            raise Exception(
                "Failed to query resolution from input: {}".format(e))

        try:
            input_ar_str = probe_media_property(
                args.verbose, args.ffprobebin, input_path, "display_aspect_ratio")

            colon_pos = input_ar_str.find(':')
            if -1 == colon_pos:
                raise Exception(
                    "Bad input aspect ratio string, no colon: '{}'".format(input_ar_str))

            input_ar_num = float(input_ar_str[:colon_pos])
            input_ar_den = float(input_ar_str[colon_pos + 1:])
            input_ar = input_ar_num / input_ar_den
        except Exception as e:
            print("Failed to query input aspect ratio: {}".format(e))
            input_ar = input_width / input_height

        if args.verbose:
            print("Input dimensions: {}x{}  Input aspect ratio: {}".format(
                input_width, input_height, input_ar))

        if not input_width or not input_height:
            raise Exception(
                "Failed to probe input dementions ({}x{}".format(input_width, input_height))

        if input_width == output_width or input_height == output_height:
            print("Input dimensions already {}x{}, not resizing".format(
                input_width, input_height))
            output_width = 0
            output_height = 0
        else:
            # If only one dimention is supplied, calculate the other from the input aspect ratio
            if not output_width:
                output_width = pad_resolution(
                    int(output_height * input_ar), args.padding)
            if not output_height:
                output_height = pad_resolution(
                    int(output_width / input_ar), args.padding)

            if args.crop:
                # Crop filter
                # Constrain output dimensions
                if output_width > input_width:
                    output_width = input_width
                if output_height > input_height:
                    output_height = input_height

                if args.crop == "center":
                    xpos = int((input_width - output_width) / 2)
                    ypos = int((input_height - output_height) / 2)
                elif args.crop == "topleft":
                    xpos = 0
                    ypos = 0
                elif args.crop == "left":
                    xpos = 0
                    ypos = int((input_height - output_height) / 2)
                elif args.crop == "bottomleft":
                    xpos = 0
                    ypos = input_height - output_height
                elif args.crop == "bottom":
                    xpos = int((input_width - output_width) / 2)
                    ypos = input_height - output_height
                elif args.crop == "bottomright":
                    xpos = input_width - output_width
                    ypos = input_height - output_height
                elif args.crop == "right":
                    xpos = input_width - output_width
                    ypos = int((input_height - output_height) / 2)
                elif args.crop == "topright":
                    xpos = input_width - output_width
                    ypos = 0
                elif args.crop == "top":
                    xpos = int((input_width - output_width) / 2)
                    ypos = 0
                filter_string = "crop={}:{}:{}:{}".format(
                    output_width, output_height, xpos, ypos)
            else:
                # Scale filter
                output_ar = output_width / output_height
                if args.verbose:
                    print("Input aspect ratio: {}  Output aspect ratio: {}".format(
                        input_ar, output_ar))

                # Make sure aspect ratio is correct
                if input_ar < output_ar:
                    output_width = pad_resolution(
                        int(output_height * input_ar), args.padding)
                elif input_ar > output_ar:
                    output_height = pad_resolution(
                        int(output_width / input_ar), args.padding)

                filter_string = "scale={}:{},setsar=1:1".format(
                    output_width, output_height)

            if args.verbose:
                print("Final output dimensions: {}x{}".format(
                    output_width, output_height))

            # Video filter string
            output_vfilters.append(filter_string)

    # Deshake filter
    if args.deshake:
        output_vfilters.append("deshake")

    return output_vfilters, output_width, output_height


# Builds audio filter list from input arguments
def audio_filters_from_args(args, input_file):
    output_afilters = []

    # Placeholder
    return output_afilters


# Process through list of input_files
def process_input(input_files, args):
    job_num = 0
    err_count = 0

    # Loop through input
    for input_file in input_files:
        input_file_index = 0
        job_num += 1
        timer_job_start = time.time()

        # Make sure input filename contains a period
        _, input_filename = os.path.split(input_file)

        if not '.' in input_filename:
            print("Input file name contains no extension: {}".format(input_file))
            err_count += 1
            continue

        # Container file extension
        output_extension = get_container_extension(args, input_file)

        # Output dimensions, may be overridden later
        output_width, output_height = get_dimensions_from_args(args)

        # Input stream counts
        input_streams_counts = get_stream_count_map_from_input(
            args, input_file)

        # Create ffmpeg command line

        try:
            # Starting binary
            cmdline = [args.ffmpegbin]

            # Overwrite flag
            if args.overwrite:
                cmdline += ["-y"]

            # Image sequence input
            if args.sequence:
                cmdline += ["-r", args.framerate, "-f", "image2"]

            # Set primary input
            cmdline += ["-i", input_file]

            # Secondary inputs
            for stream_type in STREAM_TYPES_SHORT:
                if stream_type_bool(args.addfile, stream_type):
                    if stream_type_bool(args.addlooped, stream_type):
                        cmdline += ["-stream_loop", "-1"]
                    cmdline += ["-i",
                                stream_type_arg(args.addfile, stream_type)]

            # Copy other stuff
            if not args.nounknown:
                cmdline += ["-copy_unknown"]

            # Begin stream mapping
            for stream_type in STREAM_TYPES_SHORT:
                add_file = stream_type_arg(args.addfile, stream_type)
                input_add_streams = 0
                if add_file:
                    if args.verbose:
                        print("Probing {} stream count from {}".format(
                            STREAM_TYPES_DICT[stream_type], add_file))
                    input_add_streams = probe_stream_count(
                        args.verbose, args.ffprobebin, add_file, STREAM_TYPES_DICT[stream_type])

                    if stream_type_arg(args.addstream, stream_type) is not None:
                        single_add_stream = int(
                            stream_type_arg(args.addstream, stream_type))
                        if single_add_stream + 1 > input_add_streams:
                            raise Exception("Requested stream index {} exceeds stream count {} for added input stream '{}'".format(
                                single_add_stream, input_add_streams, stream_type))
                        input_add_streams = 1

                input_streams = 0
                if not stream_type_bool(args.nocopy, stream_type):
                    input_streams = input_streams_counts[stream_type]
                    if stream_type_arg(args.singlestream, stream_type) is not None:
                        single_stream = int(stream_type_arg(
                            args.singlestream, stream_type))
                        if single_stream + 1 > input_streams_counts[stream_type]:
                            raise Exception("Requested stream index {} exceeds stream count {} for primary input stream '{}'".format(
                                single_stream, input_streams, stream_type))
                        input_streams = 1

                if not stream_type_bool(args.addfirst, stream_type):
                    # Add primary streams first
                    if input_streams:
                        cmdline += stream_map(True, args, stream_type,
                                              input_streams, 0, input_file)
                    if input_add_streams:
                        cmdline += stream_map(False, args, stream_type, input_add_streams,
                                              input_streams, add_file)
                else:
                    # Add secondary streams first
                    if input_add_streams:
                        cmdline += stream_map(False, args, stream_type,
                                              input_add_streams, 0, add_file)
                    if input_streams_counts[stream_type]:
                        cmdline += stream_map(True, args, stream_type,
                                              input_streams, input_add_streams, input_file)

            # Video filters
            if not stream_type_bool(args.nocopy, 'v'):
                try:
                    output_vfilters, output_width, output_height = video_filters_from_args(
                        args, output_width, output_height, input_file)
                    if output_vfilters:
                        cmdline += ["-vf", ','.join(output_vfilters)]
                except Exception as e:
                    print("Error retrieving video filters: {}".format(e))
                    err_count += 1
                    continue

            # Audio filters
            if not stream_type_bool(args.nocopy, 'a'):
                try:
                    output_afilters = audio_filters_from_args(
                        args, input_file)
                    if output_afilters:
                        cmdline += ["-af", ','.join(output_afilters)]
                except Exception as e:
                    print("Error retrieving audio filters: {}".format(e))
                    err_count += 1
                    continue

            # When to stop encoding
            if args.shortest:
                # Stop encoding when shortest stream finishes
                # cmdline += ["-shortest", "-movflags", "+faststart"]
                cmdline += ["-shortest"]
            elif args.duration:
                # Encode for specific duration
                cmdline += ["-t", args.duration]

            # Strip chapters
            if not stream_type_bool(args.nocopy, 'c'):
                cmdline += ["-map_chapters", "-1"]

            # Strip metadata
            if not stream_type_bool(args.nocopy, 'm'):
                cmdline += ["-map_metadata", "-1"]

            # Add metadata attachment
            if args.addattach:
                cmdline += ["-attach", args.addattach, "-metadata:s:t",
                            "mimetype={}".format(args.attachtype)]

            # Determine output filename
            if os.path.isdir(args.output):
                # Strip extension
                filename = input_filename[:input_filename.rfind('.')]
                output_file = os.path.join(
                    args.output, filename + output_extension)
            else:
                output_file = args.output

            # Add suffix
            if args.suffix:
                suffix = suffix_str(args, output_width, output_height)
                if args.verbose:
                    print("Adding suffix to filename: '{}'".format(suffix))
                dotpos = output_file.rfind('.')
                output_file = output_file[:dotpos] + \
                    suffix + output_file[dotpos:]

            # Set Output path
            cmdline += [output_file]

        except Exception as e:
            msg = "Unable to build command line: {}".format(e)
            log_to_file(msg, args)
            err_count += 1
            continue

        if args.verbose or args.dryrun:
            print(cmdline_str(cmdline))

        if args.dryrun:
            msg = "Dry run: {} -> {}".format(input_file, output_file)
            log_to_file(msg, args)
            log_to_file(cmdline_str(cmdline), args, True)
        else:
            msg = "Starting: {} -> {}".format(input_file, output_file)
            log_to_file(msg, args)
            log_to_file(cmdline_str(cmdline), args, True)
            returncode = run_with_afinity(
                cmdline, args.setaffinity, args.verbose)
            print("Finished: {} -> {}".format(input_file, output_file))
            timer_job_end = time.time()
            msg = "Job {}/{} finished: {}".format(job_num, len(
                input_files), timer_string(timer_job_end - timer_job_start))
            log_to_file(msg, args)

            if returncode:
                print("{} returned error value {}".format(
                    args.ffmpegbin, returncode))
                err_count += 1
                if args.stoponerror:
                    exit(returncode)

    return err_count


# Main function entry point
def main(argv):

    # Create argparse.ArgumentParser
    parser = create_argparser()

    # Parse dem args
    args = parser.parse_args(args=argv[1:])

    # Measure time
    timer_start = time.time()

    # Actual input is a file or wildcard list of files
    # or printf style numbered image sequence
    if os.path.isfile(args.input):
        input_files = [args.input]
    elif args.sequence and '%' in args.input:
        input_files = [args.input]
    else:
        input_files = [f for f in glob.glob(args.input)]

    # Validate inputs
    if not input_files:
        print("Missing input: {}".format(args.input))
        return errno.ENOENT
    if len(input_files) > 1 and not os.path.isdir(args.output):
        print("Multiple input files but output not a directory")
        return errno.EFAULT
    if args.addattach and not os.path.isfile(args.addattach):
        print("Missing attachment input: {}".format(args.addattach))
        return errno.ENOENT

    # CPU Affinity only availabe for "some Unix" as of 3.9
    if args.setaffinity and platform.system() not in ['Linux', 'Unix']:
        print("CPU affinity currently only supported on some Unix platforms.")
        args.setaffinity = False

    # Checks for invalid argument combinations
    try:
        check_valid_arguments(args)
    except Exception as e:
        print("Argument check: {}".format(e))
        return errno.EINVAL

    # Log command line
    log_to_file(cmdline_str(sys.argv), args, True)

    # Process the input
    err_count = process_input(input_files, args)

    # Final logs
    timer_end = time.time()
    if not args.dryrun:
        print("Conversion began: {}".format(
            time.asctime(time.localtime(timer_start))))
        print("Conversion ended: {}".format(
            time.asctime(time.localtime(timer_end))))
    msg = "{}{} files processed, {} problems encountered.  Total time: {}".format(
        "(Dry run) " if args.dryrun else "", len(input_files), err_count, timer_string(timer_end - timer_start))
    log_to_file(msg, args)

    return 0


# Entry point
if __name__ == "__main__":
    exit(main(sys.argv))
