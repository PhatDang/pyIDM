"""
    pyIDM

    multi-connections internet download manager, based on "pyCuRL/curl", "youtube_dl", and "PySimpleGUI"

    :copyright: (c) 2019-2020 by Mahmoud Elshahat.
    :license: GNU LGPLv3, see LICENSE for more details.
"""

import os
import re
import shlex
import subprocess
import sys
import zipfile
import time
from threading import Thread
from urllib.parse import urljoin

from . import config
from .downloaditem import DownloadItem, Segment
from .utils import log, validate_file_name, get_headers, size_format, run_command, size_splitter, get_seg_size, \
    delete_file, download

# youtube-dl
ytdl = None  # youtube-dl will be imported in a separate thread to save loading time


class Logger(object):
    """used for capturing youtube-dl stdout/stderr output"""

    def debug(self, msg):
        log(msg)

    def error(self, msg):
        log(msg)

    def warning(self, msg):
        log(msg)

    def __repr__(self):
        return "youtube-dl Logger"


def get_ytdl_options():
    ydl_opts = {'prefer_insecure': True, 'no_warnings': False, 'logger': Logger()}
    if config.proxy:
        ydl_opts['proxy'] = config.proxy

    # if config.log_level >= 3:
    #     ydl_opts['verbose'] = True  # it make problem with Frozen PyIDM, extractor doesn't work
    # elif config.log_level <= 1:
    #     ydl_opts['quiet'] = True  # it doesn't work

    return ydl_opts


class Video(DownloadItem):
    """represent a youtube video object, interface for youtube-dl"""

    def __init__(self, url, vid_info=None, get_size=True):
        super().__init__(folder=config.download_folder)
        self.url = url
        self.resumable = True
        self.vid_info = vid_info  # a youtube-dl dictionary contains video information

        if self.vid_info is None:
            with ytdl.YoutubeDL(get_ytdl_options()) as ydl:
                self.vid_info = ydl.extract_info(self.url, download=False)

        self.webpage_url = url  # self.vid_info.get('webpage_url')
        self.title = validate_file_name(self.vid_info.get('title', f'video{int(time.time())}'))
        self.name = self.title

        # streams
        self.stream_names = []  # names in a list
        self.raw_stream_names = []  # names but without size
        self.stream_list = []  # streams in a list
        self.video_streams = {}
        self.mp4_videos = {}
        self.other_videos = {}
        self.audio_streams = {}
        self._streams = {}
        self.raw_streams = {}

        self.stream_menu = []  # it will be shown in video quality combo box != self.stream.names
        self.raw_stream_menu = []  # same as self.stream_menu but without size
        self._selected_stream = None

        # self.audio_url = None  # None for non dash videos
        # self.audio_size = 0

        self.setup()

    def setup(self):
        self._process_streams()

    def _process_streams(self):
        """ Create Stream object lists"""
        all_streams = [Stream(x) for x in self.vid_info['formats']]

        # prepare some categories
        normal_streams = {stream.raw_name: stream for stream in all_streams if stream.mediatype == 'normal'}
        dash_streams = {stream.raw_name: stream for stream in all_streams if stream.mediatype == 'dash'}

        # normal streams will overwrite same streams names in dash
        video_streams = {**dash_streams, **normal_streams}

        # sort streams based on quality
        video_streams = {k: v for k, v in sorted(video_streams.items(), key=lambda item: item[1].quality, reverse=True)}

        # sort based on mp4 streams first
        mp4_videos = {stream.name: stream for stream in video_streams.values() if stream.extension == 'mp4'}
        other_videos = {stream.name: stream for stream in video_streams.values() if stream.extension != 'mp4'}
        video_streams = {**mp4_videos, **other_videos}

        audio_streams = {stream.name: stream for stream in all_streams if stream.mediatype == 'audio'}

        # collect all in one dictionary of stream.name: stream pairs
        streams = {**video_streams, **audio_streams}

        stream_menu = ['● Video streams:                     '] + list(mp4_videos.keys()) + list(other_videos.keys()) \
                      + ['', '● Audio streams:                 '] + list(audio_streams.keys())

        # assign variables
        self.stream_list = list(streams.values())
        self.stream_names = [stream.name for stream in self.stream_list]
        self.raw_stream_names = [stream.raw_name for stream in self.stream_list]
        self.video_streams = video_streams
        self.mp4_videos = mp4_videos
        self.other_videos = other_videos
        self.audio_streams = audio_streams

        self._streams = streams
        self.raw_streams = {stream.raw_name: stream for stream in streams.values()}
        self.stream_menu = stream_menu
        self.raw_stream_menu = [x.rsplit(' -', 1)[0] for x in stream_menu]

    @property
    def streams(self):
        """ Returns dictionary of all streams sorted  key=stream.name, value=stream object"""
        if not self._streams:
            self._process_streams()

        return self._streams

    @property
    def selected_stream_index(self):
        return self.stream_list.index(self.selected_stream)

    @property
    def selected_stream(self):
        if not self._selected_stream:
            self._selected_stream = self.stream_list[0]  # select first stream

        return self._selected_stream

    @selected_stream.setter
    def selected_stream(self, stream):
        if type(stream) is not Stream:
            raise TypeError

        self._selected_stream = stream

        self.update_param()

    def update_param(self):
        # do some parameter updates
        stream = self.selected_stream
        self.name = self.title + '.' + stream.extension
        self.eff_url = stream.url
        self.type = stream.mediatype
        self.size = stream.size
        self.fragment_base_url = stream.fragment_base_url
        self.fragments = stream.fragments
        self.protocol = stream.protocol
        self.format_id = stream.format_id

        # select an audio to embed if our stream is dash video
        if stream.mediatype == 'dash':
            audio_stream = [audio for audio in self.audio_streams.values() if audio.extension == stream.extension
                            or (audio.extension == 'm4a' and stream.extension == 'mp4')][0]
            self.audio_url = audio_stream.url
            self.audio_size = audio_stream.size
            self.audio_fragment_base_url = audio_stream.fragment_base_url
            self.audio_fragments = audio_stream.fragments
            self.audio_format_id = audio_stream.format_id
        else:
            self.audio_url = None
            self.audio_fragment_base_url = None
            self.audio_fragments = None


class Stream:
    def __init__(self, stream_info):
        # fetch data from youtube-dl stream_info dictionary
        self.format_id = stream_info.get('format_id', None)
        self.url = stream_info.get('url', None)
        self.player_url = stream_info.get('player_url', None)
        self.extension = stream_info.get('ext', None)
        self.width = stream_info.get('width', None)
        self.fps = stream_info.get('fps', None)
        self.height = stream_info.get('height', 0)
        self.format_note = stream_info.get('format_note', None)
        self.acodec = stream_info.get('acodec', None)
        self.abr = stream_info.get('abr', 0)
        self.size = stream_info.get('filesize', None)
        self.tbr = stream_info.get('tbr', None)
        # self.quality = stream_info.get('quality', None)
        self.vcodec = stream_info.get('vcodec', None)
        self.res = stream_info.get('resolution', None)
        self.downloader_options = stream_info.get('downloader_options', None)
        self.format = stream_info.get('format', None)
        self.container = stream_info.get('container', None)

        # protocol
        self.protocol = stream_info.get('protocol', '')

        # calculate some values
        self.rawbitrate = stream_info.get('abr', 0) * 1024
        self._mediatype = None
        self.resolution = f'{self.width}x{self.height}' if (self.width and self.height) else ''

        # fragmented video streams
        self.fragment_base_url = stream_info.get('fragment_base_url', None)
        self.fragments = stream_info.get('fragments', None)

        # get missing size
        if self.fragments or 'm3u8' in self.protocol:
            # ignore fragmented streams, since the size coming from headers is for first fragment not whole file
            self.size = 0
        if not isinstance(self.size, int):
            self.size = self.get_size()

        # print(self.name, self.size, isinstance(self.size, int))

    def get_size(self):
        headers = get_headers(self.url)
        size = int(headers.get('content-length', 0))
        print('stream.get_size()>', self.name)
        return size

    @property
    def name(self):
        return f'      ›  {self.extension} - {self.quality} - {size_format(self.size)}'  # ¤ » ›

    @property
    def raw_name(self):
        return f'      ›  {self.extension} - {self.quality}'

    @property
    def quality(self):
        try:
            if self.mediatype == 'audio':
                return int(self.abr)
            else:
                return int(self.height)
        except:
            return 0

    def __repr__(self, include_size=True):
        return self.name

    @property
    def mediatype(self):
        if not self._mediatype:
            if self.vcodec == 'none':
                self._mediatype = 'audio'
            elif self.acodec == 'none':
                self._mediatype = 'dash'
            else:
                self._mediatype = 'normal'

        return self._mediatype


def download_ffmpeg(destination=config.sett_folder):
    """it should download ffmpeg.exe for windows os"""

    # set download folder
    config.ffmpeg_download_folder = destination

    # first check windows 32 or 64
    import platform
    # ends with 86 for 32 bit and 64 for 64 bit i.e. Win7-64: AMD64 and Vista-32: x86
    if platform.machine().endswith('64'):
        # 64 bit link
        url = 'https://github.com/pyIDM/pyIDM/releases/download/extra/ffmpeg.zip'
    else:
        # 32 bit link
        url = 'https://github.com/pyIDM/pyIDM/releases/download/extra/ffmpeg_32bit.zip'

    log('downloading: ', url)

    # create a download object, will store ffmpeg in setting folder
    # print('config.sett_folder = ', config.sett_folder)
    d = DownloadItem(url=url, folder=config.ffmpeg_download_folder)
    d.update(url)
    d.name = 'ffmpeg.zip'  # must rename it for unzip to find it
    # print('d.folder = ', d.folder)

    # post download
    d.callback = 'unzip_ffmpeg'

    # send download request to main window
    config.main_window_q.put(('download', (d, False)))


def unzip_ffmpeg():
    log('unzip_ffmpeg:', 'unzipping')

    try:
        file_name = os.path.join(config.ffmpeg_download_folder, 'ffmpeg.zip')
        with zipfile.ZipFile(file_name, 'r') as zip_ref:  # extract zip file
            zip_ref.extractall(config.ffmpeg_download_folder)

        log('ffmpeg update:', 'delete zip file')
        delete_file(file_name)
        log('ffmpeg update:', 'ffmpeg .. is ready at: ', config.ffmpeg_download_folder)
    except Exception as e:
        log('unzip_ffmpeg: error ', e)


def check_ffmpeg():
    """check for ffmpeg availability, first: current folder, second config.global_sett_folder,
    and finally: system wide"""

    log('check ffmpeg availability?')
    found = False

    # search in current directory then default setting folder
    for folder in [config.current_directory, config.global_sett_folder]:
        for file in os.listdir(folder):
            # print(file)
            if file == 'ffmpeg.exe':
                found = True
                config.ffmpeg_actual_path = os.path.join(folder, file)
                break
        if found:  # break outer loop
            break

    # Search in the system
    if not found:
        cmd = 'where ffmpeg' if config.operating_system == 'Windows' else 'which ffmpeg'
        error, output = run_command(cmd, verbose=False)
        if not error:
            found = True

            # fix issue 47 where command line return \n\r with path
            # todo: just ignore the output path since ffmpeg in sys path and  we can call ffmpeg directly
            output = output.strip()
            config.ffmpeg_actual_path = os.path.realpath(output)

    if found:
        log('ffmpeg checked ok! - at: ', config.ffmpeg_actual_path)
        return True
    else:
        log(f'can not find ffmpeg!!, install it, or add executable location to PATH, or copy executable to ',
            config.global_sett_folder, 'or', config.current_directory)


def merge_video_audio(video, audio, output):
    log('merging video and audio')
    # ffmpeg
    ffmpeg = config.ffmpeg_actual_path

    # very fast audio just copied, format must match [mp4, m4a] and [webm, webm]
    cmd1 = f'"{ffmpeg}" -y -i "{video}" -i "{audio}" -c copy "{output}"'

    # slow, mix different formats
    cmd2 = f'"{ffmpeg}" -y -i "{video}" -i "{audio}" "{output}"'

    verbose = True if config.log_level > 1 else False

    # run command with shell=False if failed will use shell=True option
    error, output = run_command(cmd1, verbose=verbose, shell=False)

    if error:
        error, output = run_command(cmd1, verbose=verbose, shell=True)

    if error:
        error, output = run_command(cmd2, verbose=verbose, shell=True)

    return error, output


def import_ytdl():
    # import youtube_dl using thread because it takes sometimes 20 seconds to get imported and impact app startup time
    start = time.time()
    global ytdl, ytdl_version
    import youtube_dl as ytdl
    config.ytdl_VERSION = ytdl.version.__version__

    load_time = time.time() - start
    log(f'youtube-dl load_time= {load_time}')


def parse_bytes(bytestr):
    """Parse a string indicating a byte quantity into an integer., example format: 536.71KiB,
    modified from original source at youtube-dl.common"""
    matchobj = re.match(r'(?i)^(\d+(?:\.\d+)?)([kMGTPEZY]\S*)?$', bytestr)
    if matchobj is None:
        return None
    number = float(matchobj.group(1))
    unit = matchobj.group(2).lower()[0:1] if matchobj.group(2) else ''
    multiplier = 1024.0 ** 'bkmgtpezy'.index(unit)
    return int(round(number * multiplier))


def youtube_dl_downloader(d=None, extra_options=None, use_subprocess=True):
    """ ---- NOT IMPLEMENTED ----
    This is non-completed attempt to integrate youtube-dl native downloader into PyIDM,
    main issue is the lack of a proper method to interrupt / terminate youtube-dl subprocess, since process.kill() only
    kill youtube-dl and leave the child ffmpeg subprocess running in background.
    this issue still have no proper solution on windows.

    download with youtube_dl
    :param extra_options: youtube-dl extra options
    :param subprocess: bool, if true will use a subprocess to launch youtube-dl like a command line, if False will use
    an imported youtube-dl module which has a progress hook

    """
    downloaded = {}
    file_name = ''
    name = d.target_file.replace("\\", "/")

    # requested format
    if d.type == 'dash':
        # default format: bestvideo+bestaudio/best
        requested_format = f'"{d.format_id}"+"{d.audio_format_id}"/"{d.format_id}"+bestaudio/best'
    else:
        requested_format = f'"{d.audio_format_id}"/best'

    # Launch youtube-dl in subprocess, has advantage of catching stdout/stderr output, drawback no progress
    if use_subprocess:

        # get executable path,
        if config.FROZEN:  # if app. frozen by cx_freeze will use a compiled exe file
            youtube_dl_executable = '"' + os.path.join(config.current_directory, 'youtube-dl.exe') + '"'
        else:  # will use an installed module
            youtube_dl_executable = f'"{sys.executable}" -m youtube_dl'

        cmd = f'{youtube_dl_executable} -f {requested_format} {d.url} -o "{name}" -v --hls-use-mpegts'

        log('cmd:', cmd)

        cmd = shlex.split(cmd)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8',
                                   errors='replace', shell=False)

        # process stdout and parse youtube-dl progress data
        for line in process.stdout:
            line = line.strip()
            log(line)

            # monitor cancel flag
            if d.status == config.Status.cancelled:
                log('youtube_dl_downloader()> killing subprocess, cancelled by user')
                process.kill()
                return ()

            # parse line looking for progress info example output: [download]   0.0% of 4.63MiB at 32.00KiB/s ETA 02:28
            if line.startswith('[download]'):
                buffer = line.split()

                # get file name
                if 'Destination' in buffer[1] and len(buffer) >= 3:
                    file_name = buffer[2]
                    log('file name .................................', file_name)

                # get downloaded size
                if '%' in buffer[1]:
                    log(buffer)
                    percent = buffer[1].strip()
                    percent = percent.replace('%', '')
                    try:
                        # speed = buffer[5]
                        # eta = buffer[7]
                        percent = int(float(percent))
                        size = parse_bytes(buffer[3])
                        # log('percent:', percent, '- size:', size)

                        if size and file_name:
                            done = size * percent // 100
                            downloaded[file_name] = done
                            # log('done:', done, ' - downloaded:', downloaded)
                            d.downloaded = sum(downloaded.values())

                            # update video size
                            if d.format_id.replace(' ', '_') in file_name:
                                log()
                                d.size = size

                            # update audio size
                            elif d.audio_format_id.replace(' ', '_') in file_name:
                                d.audio_size = size

                    except Exception as e:
                        log(e)

        # wait for subprocess to finish, process.wait() is not recommended
        process.communicate()

        # get return code
        process.poll()
        error = process.returncode != 0  # True or False

        log(f'youtube_dl_downloader {d.status}')
        if not error:
            return True
        else:
            return False

    # using imported youtube-dl library -----------------------------------------------------------------------

    # update options
    options = {}
    options.update(**get_ytdl_options())

    if isinstance(extra_options, dict):
        options.update(**extra_options)

    options['quiet'] = False
    options['verbose'] = True
    # options['hls-prefer-native'] = True
    options['format'] = requested_format
    options['outtmpl'] = name

    def progress_hook(progress_dict):
        log(progress_dict)

        # when downloading 2 streams "i.e. dash video and audio" downloaded_bytes will be reset to zero and report
        # wrong total downloaded bytes, fix >> use  a dictionary to store every stream progress
        downloaded_bytes = progress_dict.get('downloaded_bytes', 0)
        file_name = progress_dict.get('filename', 'unknown')
        downloaded[file_name] = downloaded_bytes
        d.downloaded = sum(downloaded.values())

        total_bytes = progress_dict.get('total_bytes', 0)

        if file_name == d.temp_file:
            d.size = total_bytes

        elif file_name == d.audio_file:
            d.audio_size = total_bytes

        # monitor cancel flag
        if d.status == config.Status.cancelled:
            log('youtube-dl cancelled download')
            raise KeyboardInterrupt

    options['progress_hooks'] = [progress_hook]

    # start downloading
    try:
        with ytdl.YoutubeDL(options) as ydl:
            ydl.download([d.url])
    except Exception as e:
        log('youtube_dl_downloader()> ', e)
        return False

    log('youtube_dl_downloader()> done downloading', d.name)
    return True


def hls_downloader(d):
    """using ffmpeg to download hls streams ---- NOT IMPLEMENTED ----"""

    cmd = f'"ffmpeg" -y -i "{d.eff_url}" -c copy -f mp4 "file:{d.temp_file}"'
    subprocess.Popen(cmd)
    # error, output = run_command(cmd)
    # if error:
    #     return False
    # else:
    #     return True


def pre_process_hls(d):
    """handle m3u8 list and build a url list of file segments"""

    log('pre_process_hls()> start processing', d.name)

    def get_url_list(url):
        # url_list
        url_list = []

        # download the manifest from m3u8 file descriptor located at url
        buffer = download(url)  # get BytesIO object

        if buffer:
            # convert to string
            buffer = buffer.getvalue().decode()
            for line in buffer.splitlines():
                line.strip()
                if line and not line.startswith('#'):
                    url_list.append(line)

            # get absolute path from url_list relative path
            url_list = [urljoin(url, seg_url) for seg_url in url_list]

        # log('process hls> url list:', url_list)
        return buffer, url_list

    # first lets handle video stream
    video_m3u8, video_url_list = get_url_list(d.eff_url)

    # validate video m3u8 file
    if '#EXT' not in repr(video_m3u8):
        log('pre_process_hls()> received invalid m3u8 file from server')
        log('buffer:', video_m3u8)
        return False

    # create temp_folder if doesn't exist
    if not os.path.isdir(d.temp_folder):
        os.makedirs(d.temp_folder)

    # save m3u8 file to disk
    with open(os.path.join(d.temp_folder, 'remote_video.m3u8'), 'w') as f:
        f.write(video_m3u8)

    # build video segments
    d.segments = [Segment(name=os.path.join(d.temp_folder, str(i) + '.ts'), num=i, range=None, size=0,
                          url=seg_url, tempfile=d.temp_file)
                  for i, seg_url in enumerate(video_url_list)]

    # handle audio stream in case of dash videos
    if d.type == 'dash':
        audio_m3u8, audio_url_list = get_url_list(d.audio_url)

        # validate audio m3u8 file
        if '#EXT' not in repr(audio_m3u8):
            log('pre_process_hls()> received invalid m3u8 file from server')
            return False

        # save m3u8 file to disk
        with open(os.path.join(d.temp_folder, 'remote_audio.m3u8'), 'w') as f:
            f.write(audio_m3u8)

        # build audio segments
        audio_segments = [Segment(name=os.path.join(d.temp_folder, str(i) + '_audio.ts'), num=i, range=None, size=0,
                                  url=seg_url, tempfile=d.audio_file)
                          for i, seg_url in enumerate(audio_url_list)]

        # add to video segments
        d.segments += audio_segments

    # load previous segment information from disk - resume download -
    d.load_progress_info()

    log('pre_process_hls()> done processing', d.name)

    return True


def post_process_hls(d):
    """ffmpeg will process m3u8 files"""

    log('post_process_hls()> start processing', d.name)

    def create_local_m3u8(remote_file, local_file, local_names):

        with open(remote_file, 'r') as f:
            lines = f.readlines()

        names = [f'{name}\n' for name in local_names]
        names.reverse()

        # log(len([a for a in lines if not a.startswith('#')]))

        for i, line in enumerate(lines):
            if line and not line.startswith('#'):
                try:
                    lines[i] = names.pop()
                    # log(lines[i])
                except:
                    pass

        with open(local_file, 'w') as f:
            f.writelines(lines)
            # print(lines)

    # create local m3u8 version - video
    remote_video_m3u8_file = os.path.join(d.temp_folder, 'remote_video.m3u8')
    local_video_m3u8_file = os.path.join(d.temp_folder, 'local_video.m3u8')

    try:
        names = [seg.name for seg in d.segments if seg.tempfile == d.temp_file]
        # print('names = ', names)
        create_local_m3u8(remote_video_m3u8_file, local_video_m3u8_file, names)
    except Exception as e:
        log('post_process_hls()> error', e)
        return False

    if d.type == 'dash':
        # create local m3u8 version - audio
        remote_audio_m3u8_file = os.path.join(d.temp_folder, 'remote_audio.m3u8')
        local_audio_m3u8_file = os.path.join(d.temp_folder, 'local_audio.m3u8')

        try:
            create_local_m3u8(remote_audio_m3u8_file, local_audio_m3u8_file,
                              [seg.name for seg in d.segments if seg.tempfile == d.audio_file])
        except Exception as e:
            log('post_process_hls()> error', e)
            return False

    # now processing with ffmpeg
    cmd = f'"{config.ffmpeg_actual_path}" -y -protocol_whitelist "file,http,https,tcp,tls,crypto"  -i ' \
          f'"{local_video_m3u8_file}" -c copy -f mp4 "file:{d.temp_file}"'

    error, output = run_command(cmd)
    if error:
        log('post_process_hls()> ffmpeg failed:', output)
        return False

    if d.type == 'dash':
        cmd = f'"{config.ffmpeg_actual_path}" -y -protocol_whitelist "file,http,https,tcp,tls,crypto"  -i ' \
              f'"{local_audio_m3u8_file}" -c copy -f mp4 "file:{d.audio_file}"'

        error, output = run_command(cmd)
        if error:
            log('post_process_hls()> ffmpeg failed:', output)
            return False

    log('post_process_hls()> done processing', d.name)

    return True
