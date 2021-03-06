# -*- coding: utf-8 -*-

import xbmcplugin
import sys
from urllib.parse import urlencode, urlparse, parse_qsl
import os
import platform
import subprocess
from subprocess import Popen
import re
import xbmc
import xbmcaddon
import xbmcgui
import json
import requests
import xbmcvfs
from zipfile import ZipFile
import shlex
import time

ADDON = xbmcaddon.Addon(id="plugin.video.telerising-cloudcontrol")
addon_name = ADDON.getAddonInfo('name')
addon_version = ADDON.getAddonInfo('version')
loc = ADDON.getLocalizedString
datapath = xbmc.translatePath(ADDON.getAddonInfo('profile'))
addonpath = xbmc.translatePath(ADDON.getAddonInfo('path'))
temppath = os.path.join(datapath, "temp")
mute_notify = ADDON.getSetting('hide-osd-messages')
status = os.path.join(temppath, "status.json")

# Read Cloud Settings
enable_cloud = True if ADDON.getSetting('enable_cloud').upper() == 'TRUE' else False
recording_address = ADDON.getSetting('recording_address')
recording_port = ADDON.getSetting('recording_port')
connection_type_cloud = True if ADDON.getSetting('connection_type_cloud').upper() == 'TRUE' else False
enable_protection_pin_cloud = True if ADDON.getSetting('enable_protection_pin_cloud').upper() == 'TRUE' else False
protection_pin_cloud = ADDON.getSetting('protection_pin_cloud')

# Read Vod Settings
enable_vod = True if ADDON.getSetting('enable_vod').upper() == 'TRUE' else False
vod_address = ADDON.getSetting('vod_address')
vod_port = ADDON.getSetting('vod_port')
connection_type_vod = True if ADDON.getSetting('connection_type_vod').upper() == 'TRUE' else False
enable_protection_pin_vod = True if ADDON.getSetting('enable_protection_pin_vod').upper() == 'TRUE' else False
protection_pin_vod = ADDON.getSetting('protection_pin_vod')


# Read Global Settings
storage_path = ADDON.getSetting('storage_path')
quality = ADDON.getSetting('quality')
audio_profile = ADDON.getSetting('audio_profile')
showtime_in_title = True if ADDON.getSetting('showtime_in_title').upper() == 'TRUE' else False
enable_moviedetails = True if ADDON.getSetting('enable_moviedetails').upper() == 'TRUE' else False

# Items per Page, maybe later a setup option

ipp = 15

machine = platform.machine()

# return connection type


def setServer(server, port, secure=True):
    if secure:
        return 'https://{}'.format(server)
    return 'http://{}:{}'.format(server, port)


# Translate Video Settings to Bandwidth

bandwidth = dict({'432p25': '1500',
                  '576p50': '2999',
                  '720p25': '3000',
                  '720p50': '5000',
                  '1080p25': '4999',
                  '1080p50': '8000'})


# Make OSD Notify Messages

OSD = xbmcgui.Dialog()


def notify(title, message, icon=xbmcgui.NOTIFICATION_INFO):
    OSD.notification(title, message, icon)

# Make a debug logger


def log(message, loglevel=xbmc.LOGDEBUG):
    xbmc.log('[{} {}] {}'.format(addon_name, addon_version, message), loglevel)


def calculate_dltime(vmax, vcurrent, start):
    """
    :param start: timestamp of start of process
    :type start: float
    :param vmax: max value of process, e.g. filesize or playtime
    :type vmax: float
    :param vcurrent: current value of process, e.g. current filesize or playtime
    :return: None if calculation impossible (div by zero), else 'mm:ss'
    """
    if vcurrent > 0:
        remain = (time.time() - start) / (vcurrent / vmax) - (time.time() - start)
        return '{}:{:02}'.format(int(remain / 60), int(remain % 60))
    return None


def HRV(ibytes):
    """
    forms human readable values of bytesizes
    :param ibytes: bytes to convert
    :type ibytes: int
    :return: formatted bytes e.g. '3.44 MB'
    """
    hrv = list(['Bytes', 'kBytes', 'MBytes', 'GBytes', 'TBytes'])
    index = 0
    while ibytes > 1024:
        ibytes /= 1024
        index += 1
    if index > 0: return '{:1.2f} {}'.format(ibytes, hrv[index])
    return '{} {}'.format(ibytes, hrv[index])


class IncompleteOrMissingJsonFileError(Exception):
    pass


class SystemEnvironment(object):
    def __init__(self):
        self.base_git_url = 'https://github.com/DeBaschdi/packages/raw/master'

        # structure of dict: machine: [OS, URL ffprobe packed, URL ffmpeg packed, ffprobe unpacked, ffmpeg unpacked,
        # kill command]

        self.mtypes = dict({'x86_64': ['Linux', 'ffprobe_x86_64.zip', 'ffmpeg_x86_64.zip', 'ffprobe', 'ffmpeg',
                                       'ps ax | grep "ffmpeg" | cut -c1-6 | sed "s/^/kill -9 /" | bash'],
                            'AMD64': ['Windows', 'ffprobe_amd64.zip', 'ffmpeg_amd64.zip', 'ffprobe.exe', 'ffmpeg.exe',
                                      'taskkill /IM ffmpeg.exe /F'],
                            'OSX64': ['OSX', 'ffprobe_osx64.zip', 'ffmpeg_osx64.zip', 'ffprobe', 'ffmpeg', None],
                            'armv7l': ['Linux', 'ffprobe_arm32.zip', 'ffmpeg_arm32.zip', 'ffprobe', 'ffmpeg',
                                       'ps ax | grep "ffmpeg" | cut -c1-6 | sed "s/^/kill -9 /" | bash'],
                            'armv8l': ['Linux', 'ffprobe_arm64.zip', 'ffmpeg_arm64.zip' 'ffprobe', 'ffmpeg',
                                       'ps ax | grep "ffmpeg" | cut -c1-6 | sed "s/^/kill -9 /" | bash'],
                            'aarch64': ['Android', None, None, None, None, None]})

        self.machine = None
        self.isSupported = False
        self.isInstalled = False

        self.run = None
        self.temp = None

    def prepare(self):
        if machine != '' and machine in self.mtypes.keys() and self.mtypes[machine][1] is not None and \
                self.mtypes[machine][2] is not None:

            self.isSupported = True
            self.machine = machine

            self.run = os.path.join(datapath, 'bin')
            self.temp = os.path.join(datapath, 'temp')

            if not os.path.exists(self.run): os.makedirs(self.run, mode=509)
            if not os.path.exists(self.temp): os.makedirs(self.temp)

            self.ffprobe_url = '{}/{}'.format(self.base_git_url, self.mtypes[machine][1])
            self.ffmpeg_url = '{}/{}'.format(self.base_git_url, self.mtypes[machine][2])
            self.ffprobe_executable = os.path.join(self.run, self.mtypes[machine][3])
            self.ffmpeg_executable = os.path.join(self.run, self.mtypes[machine][4])
            self.kill_ffmpeg = self.mtypes[machine][5]
            log('Machine is {}'.format(self.machine), xbmc.LOGNOTICE)

        else:
            if self.machine == '' or None:
                log('Machine ' + machine + 'is currently not supported', xbmc.LOGERROR)
                notify(addon_name, loc(32181).format(machine), icon=xbmcgui.NOTIFICATION_ERROR)

    def check(self):
        if self.isSupported:
            if os.path.exists(self.run) and os.path.isfile(self.ffprobe_executable) and \
                    os.path.isfile(self.ffmpeg_executable):
                self.isInstalled = True
                log('{} and {} are installed'.format(os.path.basename(self.ffprobe_executable),
                                                     os.path.basename(self.ffmpeg_executable)), xbmc.LOGNOTICE)

                # Make Binarys Executable (Octal Premission Python 2 +3 Compatible)
                os.chmod(self.ffprobe_executable, 509)
                os.chmod(self.ffmpeg_executable, 509)
        else:
            log('Machine is ' + machine + ' and currently only support Playback / Delete', xbmc.LOGNOTICE)

    def download(self, response, message):
        with open(os.path.join(self.run, 'download.zip'), 'wb') as f:
            total = response.headers.get('content-length')
            if total is None:
                log('Could not determine download size', xbmc.LOGNOTICE)
                f.write(response.content)
            else:
                bgDialog = xbmcgui.DialogProgress()
                bgDialog.create(addon_name, message)

                partial = 0
                total = int(total)
                completed = True
                for data in response.iter_content(chunk_size=4096):
                    partial += len(data)
                    f.write(data)
                    bgDialog.update(int(100 * partial / total), addon_name, message)
                    if bgDialog.iscanceled():
                        completed = False
                        break
                bgDialog.close()
        f.close()

        if completed:
            with ZipFile(os.path.join(self.run, 'download.zip'), 'r') as _zip:
                _zip.extractall(self.run)
        os.remove(os.path.join(self.run, 'download.zip'))
        return completed

    def install_tools(self):
        if self.isInstalled: return

        if self.isSupported:
            yn = OSD.yesno(addon_name, loc(32200))
            if yn:
                try:
                    log('Download and install FFProbe', xbmc.LOGNOTICE)
                    req = requests.get(self.ffprobe_url, stream=True)
                    req.raise_for_status()

                    if self.download(req, 'Download FFProbe'):
                        log('Download and install FFMpeg', xbmc.LOGNOTICE)
                        req = requests.get(self.ffmpeg_url, stream=True)
                        req.raise_for_status()

                        if self.download(req, 'Download FFMpeg'):
                            OSD.ok('{} - Addon Environment'.format(addon_name), loc(32201))
                            self.isInstalled = True

                    if not self.isInstalled:
                        OSD.ok('{} - Addon Environment'.format(addon_name), 'Setup aborted by User.')

                except requests.exceptions.RequestException as e:
                    log('Could not download/install ffmpeg/ffprobe: {}'.format(e), xbmc.LOGERROR)
        else:
            notify(addon_name, loc(32180).format(machine), icon=xbmcgui.NOTIFICATION_ERROR)


def request_m3u(list_type, address, port, secure, params):
    try:
        req = requests.get(setServer(address, port, secure), params=params, timeout=30)
        req.raise_for_status()
        encoding = 'utf-8' if req.encoding is None else req.encoding
        response = req.text.encode(encoding=encoding)
        m3u = response.splitlines()
        m3u.pop(0)
        return m3u
    except requests.exceptions.RequestException as e:
        notify(addon_name, loc(32190).format(list_type), icon=xbmcgui.NOTIFICATION_ERROR)
        log('Could not download {} m3u: {}'.format(list_type, e), xbmc.LOGERROR)
    except AttributeError as e:
        log('Error while processing items in {} list: {}'.format(list_type, e), xbmc.LOGERROR)
    return []


def parse_m3u_items(line_0, line_1, list_type):
    m3u_items = line_0.decode().split(', ')
    try:
        (extinf, tvgid, grouptitle, tvglogo) = shlex.split(m3u_items[0])
    except ValueError as e:
        log('Not all parameters provided', xbmc.LOGERROR)
        log('{}'.format(m3u_items[0]))
        log('Error parsing parameters in line #0 of m3u item: {}'.format(e), xbmc.LOGERROR)
        log('{}'.format(line_0), xbmc.LOGERROR)
        log('discard entry', xbmc.LOGERROR)
        return False

    showtime = ''
    channel = ''

    try:
        if list_type.lower() == 'cloud':
            (showtime, title, channel) = m3u_items[1].split(' | ')

        elif list_type.lower() == 'vod':
            title = m3u_items[1]

        videourl = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', line_1.decode())

        stream_params = urlparse(videourl[0]).query
        ffmpeg_params = line_1.decode().split(videourl[0] + '"')[1].split('pipe:1')[0]
        IsPlayable = 'true'

        if title[0:9] == '[PLANNED]':
            collection = 'Timer'
            title = title[10:]
            IsPlayable = 'false'
        else:
            collection = grouptitle.split('=')[1]
    except ValueError as e:
        log('Error parsing parameters in line #1 of m3u item: {}'.format(e), xbmc.LOGERROR)
        log('{}'.format(line_1))
        log('{}'.format(line_1), xbmc.LOGERROR)
        log('discard entry', xbmc.LOGERROR)
        return False

    return (collection,
            tvgid.split('=')[1],
            title.replace(' _', ':'),
            tvglogo.split('=')[1],
            videourl[0],
            grouptitle.split('=')[1],
            showtime,
            channel,
            ffmpeg_params,
            dict(parse_qsl(stream_params)),
            IsPlayable)


def create_videodict(list_types):

    videodict = dict()

    for list_type in list_types:
        log('Getting M3U from {}'.format(list_type))
        if list_type.lower() == 'cloud':
            m3u = request_m3u(list_type,
                              recording_address,
                              recording_port,
                              connection_type_cloud,
                              params={'file': 'recordings.m3u', 'bw': bandwidth[quality], 'platform': 'hls5',
                                      'ffmpeg': 'true', 'profile': audio_profile, 'code': protection_pin_cloud})

        elif list_type.lower() == 'vod':
            m3u = request_m3u(list_type,
                              vod_address,
                              vod_port,
                              connection_type_vod,
                              params={'file': 'ondemand.m3u', 'bw': bandwidth[quality], 'platform': 'hls5',
                                      'ffmpeg': 'true', 'profile': audio_profile, 'code': protection_pin_vod})

        else:
            m3u = []

        log('parse {} m3u listitems of {}'.format(len(m3u) // 2, list_type))
        for i in range(0, len(m3u), 2):
            m3u_items = parse_m3u_items(m3u[i], m3u[i + 1], list_type)

            if not m3u_items:
                continue

            (collection, tvgid, name, thumb, video_url, group, showtime,
             channel, ffmpeg_params, streamparams, IsPlayable) = m3u_items
            try:
                if collection not in videodict.keys(): videodict.update({collection: list()})
                videodict[collection].append(dict({'name': name,
                                                   'tvgid': tvgid,
                                                   'thumb': thumb,
                                                   'group': group,
                                                   'video': video_url,
                                                   'showtime': showtime,
                                                   'channel': channel,
                                                   'list_type': list_type,
                                                   'ffmpeg_params': ffmpeg_params,
                                                   'streamparams': streamparams,
                                                   'isplayable': IsPlayable}))

            except (TypeError, AttributeError) as e:
                log('Error while processing items in recording list: {}'.format(e), xbmc.LOGERROR)
                return False

    return videodict


def get_url(params):
    """
    Create a URL for calling the plugin recursively from the given set of keyword arguments.
    :param params: "argument=value" pairs
    :type params: dict
    :return: plugin call URL
    :rtype: str
    """
    return '{0}?{1}'.format(_url, urlencode(params))


def get_categories():
    """
    Get the list of video categories.
    Here you can insert some parsing code that retrieves
    the list of video categories (e.g. 'Movies', 'TV-shows', 'Documentaries' etc.)
    from some site or server.
    .. note:: Consider using `generator functions <https://wiki.python.org/moin/Generators>`_
        instead of returning lists.
    :return: The list of video categories
    :rtype: types.GeneratorType
    """
    return tr_videos.keys()


def get_videos(category):
    """
    Get the list of videofiles/streams.
    Here you can insert some parsing code that retrieves
    the list of video streams in the given category from some site or server.
    .. note:: Consider using `generators functions <https://wiki.python.org/moin/Generators>`_
        instead of returning lists.
    :param category: Category name
    :type category: str
    :return: the list of videos in the category
    :rtype: list
    """
    try:
        return tr_videos[category]

    except KeyError as e:
        log('Key Error: {}, fall back to overview'.format(e), xbmc.LOGERROR)
        list_categories()
        exit(0)


def create_context_url(params):
    """
    Create a context menu URL for downloading and deleting video from server
    :param params: dict of video params
    :return: URL
    """
    return 'RunPlugin({})'.format(get_url(params))


def list_categories():
    """
    Create the list of video categories in the Kodi interface.
    """
    xbmcplugin.setPluginCategory(_handle, loc(32221))
    xbmcplugin.setContent(_handle, 'videos')

    categories = get_categories()

    for category in categories:
        liz = xbmcgui.ListItem(label=category)
        liz.setArt({'thumb': tr_videos[category][0]['thumb'],
                    'icon': tr_videos[category][0]['thumb'],
                    'fanart': tr_videos[category][0]['thumb']})

        liz.setInfo('video', {'title': category,
                              'genre': category,
                              'mediatype': 'video'})

        # Create a URL for a plugin recursive call.
        # Example: plugin://plugin.video.example/?action=listing&category=Animals
        url = get_url({'action': 'listing', 'category': category, 'page': 0})
        is_folder = True
        xbmcplugin.addDirectoryItem(_handle, url, liz, is_folder)

    # Add a sort method for the virtual folder items (alphabetically, ignore articles)
    xbmcplugin.addSortMethod(_handle, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE)
    xbmcplugin.endOfDirectory(_handle, succeeded=True, updateListing=True, cacheToDisc=False)


def list_videos(category, page=None):
    """
    Create the list of playable videos in the Kodi interface.
    :param category: Category name
    :param page: Page number for paginator
    :type category: str
    """
    xbmcplugin.setPluginCategory(_handle, category)
    xbmcplugin.setContent(_handle, 'videos')

    videos = get_videos(category)
    items = len(videos)
    log('{} videos in category {} found'.format(items, category))

    req_pars = dict(
        {
            'cloud': {'va': recording_address, 'vp': recording_port, 'secure': connection_type_cloud, 'params': 'info'},
            'vod_movie': {'va': vod_address, 'vp': vod_port, 'secure': connection_type_vod, 'params': 'vod_movie_info'},
            'vod': {'va': vod_address, 'vp': vod_port, 'secure': connection_type_vod, 'params': 'vod_info'}
         })

    # Paginator

    if page is None:
        first = 0
    else:
        first = int(page) * ipp

    last = first + ipp if items > first + ipp else items

    # get paginated listitems

    for item in range(first, last):
        video = videos[item]
        description = ''
        genre = ''
        year = ''

        if enable_moviedetails:
            req_par = None
            if video['list_type'].lower() == 'cloud': req_par = req_pars['cloud']
            elif video['list_type'].lower() == 'vod':
                params = dict(parse_qsl(urlparse(video['video']).query))
                if 'vod_movie' in params:
                    req_par = req_pars['vod_movie']
                elif 'vod' in params:
                    req_par = req_pars['vod']
            try:
                json_url = requests.get(setServer(req_par['va'],
                                                  req_par['vp'],
                                                  secure=req_par['secure']),
                                        params={req_par['params']: video['tvgid'], 'code': protection_pin_cloud}
                                        ).json()

                if video['list_type'].lower() == 'cloud':
                    description = json_url['programs'][0]['d']
                    genre = ', '.join(json_url['programs'][0]['g'])
                    year = json_url['programs'][0]['year']
                else:
                    description = json_url["description"]
                    genre = ', '.join(json_url['genres'])
                    year = json_url['year']

            except (AttributeError, ValueError) as e:
                log('An error ocurred: {}'.format(e), xbmc.LOGERROR)

        liz = xbmcgui.ListItem(label=video['name'])
        liz.setArt({'thumb': video['thumb'],
                    'icon': video['thumb'],
                    'fanart': video['thumb']})

        liz.setInfo('video', {'plot': description,
                              'genre': genre,
                              'year': year,
                              'mediatype': 'video'})
        if showtime_in_title:
            liz.setInfo('video', {'title': video['showtime'] + ' ' + video['name']})
        else:
            liz.setInfo('video', {'title': video['name']})

        # Set 'IsPlayable' property to 'true'.
        # This is mandatory for playable items!
        liz.setProperty('IsPlayable', video['isplayable'])

        # Context Menu
        context_items = list()

        if video['isplayable'] == 'true':
            if SysEnv.isSupported:
                context_items.append(('Download',
                                      create_context_url({'action': loc(32222), 'video': video['video'],
                                                          'title': video['name'],
                                                          'ffmpeg_params': video['ffmpeg_params'],
                                                          'list_type': video['list_type']})
                                      ))

        # Create a URL for a plugin call from within context menu
        # Example: plugin://script.telerising-cloudcontrol/?action=download&recording=12345678

        if video['list_type'] == 'Cloud':
            context_items.append(('Delete',
                                  create_context_url({'action': loc(32223), 'video': video['video']})
                                  ))

        liz.addContextMenuItems(context_items)

        # Create a URL for a plugin recursive call.
        # Example: plugin://plugin.video.example/?action=play&video=http://www.vidsplay.com/wp-content/uploads/2017/04/crab.mp4

        url = get_url({'action': 'play', 'video': video['video']})
        is_folder = False
        xbmcplugin.addDirectoryItem(_handle, url, liz, is_folder)

    # Add a sort method for the virtual folder items (alphabetically, ignore articles)
    xbmcplugin.addSortMethod(_handle, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE)

    # Add a paginator link if there are more items
    if last < items:
        page = int(last / ipp)
        url = get_url({'action': 'listing', 'category': category, 'page': page})
        liz = xbmcgui.ListItem(label=loc(32220))
        liz.setProperty('IsPlayable', 'false')
        is_folder = True
        xbmcplugin.addDirectoryItem(_handle, url, liz, is_folder)

    xbmcplugin.endOfDirectory(_handle, succeeded=True, updateListing=True, cacheToDisc=False)


def play_video(path):
    """
    Play a video by the provided path.
    :param path: Fully-qualified video URL
    :type path: str
    """
    play_item = xbmcgui.ListItem(path=path)
    xbmcplugin.setResolvedUrl(_handle, True, listitem=play_item)


def delete_video(video):
    """
    Remove a file aka Recording from the server.
    :param video: unique Video URL
    :return: True, if deleting was success else False
    """
    params = dict(parse_qsl(urlparse(video).query))
    try:
        req = requests.get(setServer(recording_address, recording_port,
                                     secure=connection_type_cloud) + '/index.m3u',
                           params={'recording': params['recording'], 'remove': 'true', 'code': protection_pin_cloud})
        req.raise_for_status()

        if 'SUCCESS' in req.text:
            return True

    except requests.exceptions.RequestException as e:
        log('Could not delete video with id {}: {}'.format(params['recording'], e), xbmc.LOGERROR)
        return False

    log('Unexpected response from server while deleting a file: {}'.format(req.text), xbmc.LOGERROR)
    return False


def download_video(url, title, ffmpeg_params, list_type):
    data = dict({'is_downloading': True})
    with open(status, 'w') as f: json.dump(data, f, indent=4)

    title = ' '.join(re.sub('[^\w_.)( -]', '', title).split())
    params = dict(parse_qsl(urlparse(url).query))
    if list_type.lower() == 'vod':
        if "vod_movie" in params:
            download_id = params['vod_movie']
        elif "vod" in params:
            download_id = params['vod']
    elif list_type.lower() == 'cloud':
        download_id = params['recording']

    src_json = xbmc.makeLegalFilename(os.path.join(SysEnv.temp, download_id + '_src.json'))
    dest_json = xbmc.makeLegalFilename(os.path.join(SysEnv.temp, download_id + '_dest.json'))
    src_movie = xbmc.makeLegalFilename(os.path.join(SysEnv.temp, download_id + '.ts'))
    dest_movie = os.path.join(storage_path, title + '.ts')

    log('Selected ID for Download: {} {}'.format(list_type.lower(), download_id), xbmc.LOGNOTICE)
    pDialog = xbmcgui.DialogProgressBG()
    pDialog.create(loc(32210).format(title, quality), loc(32214))

    retries = 10
    while retries > 0:
        xbmc.sleep(1000)
        try:
            # Probe source
            subprocess.Popen('"{}" -v quiet -print_format json '
                             '-show_format "{}" > "{}"'.format(SysEnv.ffprobe_executable, url, src_json), shell=True)
            with open(src_json, 'r') as f_src:
                src_status = json.load(f_src)
                src_duration = src_status["format"].get("duration")
                break
        except (IOError, ValueError, KeyError, AttributeError) as e:
            retries -= 1

    if retries == 0:
        pDialog.close()
        notify(addon_name, loc(32182), icon=xbmcgui.NOTIFICATION_ERROR)
        log("Could not open Json SRC File", xbmc.LOGERROR)
        raise IncompleteOrMissingJsonFileError()

    start_dl = time.time()
    log('Starting Download of {}'.format(download_id), xbmc.LOGNOTICE)
    ffmpeg_task = [Popen('"{}" -y -i "{}" {} "{}"'.format(SysEnv.ffmpeg_executable, url,
                                                          ffmpeg_params, src_movie), shell=True)]
    xbmc.sleep(3000)

    while ffmpeg_task:
        for proc in ffmpeg_task:
            if proc.poll() is None:

                # still running
                retries = 10
                while retries > 0:
                    try:
                        xbmc.sleep(3000)
                        # probe destination
                        subprocess.Popen('"{}" -v quiet -print_format json '
                                         '-show_format "{}" > "{}"'.format(SysEnv.ffprobe_executable,
                                                                           src_movie, dest_json), shell=True)
                        with open(dest_json, 'r') as f_dest:
                            dest_status = json.load(f_dest)
                            dest_duration = dest_status["format"].get("duration")
                        break
                    except (IOError, ValueError, KeyError, AttributeError) as e:
                        retries -= 1
                if retries == 0:
                    notify(addon_name, loc(32189), icon=xbmcgui.NOTIFICATION_ERROR)
                    log("Could not open Json Dest File", xbmc.LOGERROR)
                    pDialog.close()
                    raise IncompleteOrMissingJsonFileError()

                percent = int((float(dest_duration) * 100) / float(src_duration))
                remain = calculate_dltime(float(src_duration), float(dest_duration), start_dl)
                if remain is not None:
                    pDialog.update(percent, loc(32210).format(title, quality), loc(32211).format(remain))
                else:
                    pDialog.update(percent, loc(32210).format(title, quality), loc(32214))
                continue

            else:
                # Download process has finished or has aborted
                pDialog.close()
                ffmpeg_task.remove(proc)

                # check if download process is canceled
                if os.path.isfile(status):
                    with open(status, 'r') as s:
                        data = json.load(s)

                    if not data["is_downloading"]:
                        # aborting
                        notify(addon_name, loc(32188).format(title), icon=xbmcgui.NOTIFICATION_INFO)
                        log("Download aborted by User for '{}'".format(download_id), xbmc.LOGNOTICE)
                    else:
                        # completed
                        log('Download of {} complete'.format(download_id), xbmc.LOGNOTICE)

                        if xbmcvfs.exists(src_movie):
                            s = xbmcvfs.File(src_movie)
                            d = xbmcvfs.File(dest_movie, 'w')
                            chunksize = int(s.size() / 100)
                            if chunksize > 10000:
                                # >10k, progress copy
                                cDialog = xbmcgui.DialogProgressBG()
                                cDialog.create(loc(32212).format(title))
                                i = 0
                                cp = True

                                while cp:
                                    chunk = s.readBytes(chunksize)
                                    if not chunk: break     # EOF
                                    if not d.write(chunk):  # could not write for several reasons
                                        log('An error occurred during copying, check free space and permissions',
                                            xbmc.LOGERROR)
                                        notify(addon_name, loc(32186).format(download_id),
                                               icon=xbmcgui.NOTIFICATION_ERROR)
                                        cp = False
                                        break
                                    i += 1
                                    cDialog.update(i, loc(32212).format(title), loc(32213).format(HRV(i * chunksize)))
                                cDialog.close()

                                if cp: notify(addon_name, loc(32183).format(title), icon=xbmcgui.NOTIFICATION_INFO)
                            else:
                                # direct copy
                                if xbmcvfs.copy(src_movie, dest_movie):
                                    notify(addon_name, loc(32183).format(title), icon=xbmcgui.NOTIFICATION_INFO)
                                else:
                                    log('An error occurred during copying, check free space and permissions',
                                        xbmc.LOGERROR)
                                    notify(addon_name, loc(32186).format(download_id), icon=xbmcgui.NOTIFICATION_ERROR)
                            s.close()
                            d.close()
                        else:
                            notify(addon_name, loc(32187).format(src_movie), icon=xbmcgui.NOTIFICATION_ERROR)
                            log('missing {}'.format(src_movie), xbmc.LOGERROR)

                    xbmcvfs.delete(status)
                clean_tempfolder([download_id + '_src.json', download_id + '_dest.json', download_id + '.ts'],
                                 'deleting temp files for {}'.format(download_id), xbmc.LOGNOTICE)


def clean_tempfolder(files=None, msg=None, msg_status=xbmc.LOGERROR):
    """
    Function for deleting all files in temp folder, or (if files not none) specific files
    :param files: files to delete, none for complete folder
    :type files: list
    :param msg: log message
    :param msg_status: status of log message (xbmc.LOGNOTICE, XBMC.LOGERROR), default xbmc.LOGERROR
    :return: None
    """
    if msg is not None:
        log(msg, msg_status)
    if files is not None:
        for file in files: xbmcvfs.delete(os.path.join(SysEnv.temp, file))
    else:
        for file in os.listdir(SysEnv.temp): xbmcvfs.delete(os.path.join(SysEnv.temp, file))


def kill_ffmpeg():
    data = dict({'is_downloading': False})
    with open(status, 'w') as f:
        json.dump(data, f, indent=4)
    subprocess.Popen(SysEnv.kill_ffmpeg, shell=True)


def router(paramstring):
    """
    Router function that calls other functions
    depending on the provided paramstring
    :param paramstring: URL encoded plugin paramstring
    :type paramstring: str
    """
    log('Provided params: {}'.format(paramstring), xbmc.LOGDEBUG)
    # Parse a URL-encoded paramstring to the dictionary of
    # {<parameter>: <value>} elements
    params = dict(parse_qsl(paramstring))
    # Check the parameters passed to the plugin
    if params:
        if params['action'] == 'check':
            SysEnv.prepare()
            SysEnv.check()
            if not SysEnv.isInstalled:
                SysEnv.install_tools()
            else:
                notify(addon_name, 'Environment Tools already installed')
            if not SysEnv.isInstalled:
                log('Could not install System Environment', xbmc.LOGERROR)
                notify(addon_name, 'Could not install System Environment', icon=xbmcgui.NOTIFICATION_ERROR)
            quit()

        elif params['action'] == 'clean':
            # Cleanup Tempfolder
            clean_tempfolder()
            log('deleting Tempfiles' , xbmc.LOGNOTICE)
            notify(addon_name, loc(32193), icon=xbmcgui.NOTIFICATION_INFO)

        elif params['action'] == 'kill_ffmpeg':
            # Kill all FFMpeg Processes
            kill_ffmpeg()
            log('Stop All FFMpeg Processes' , xbmc.LOGNOTICE)

        elif params['action'] == 'listing':
            # Display the list of videos in a provided category.
            list_videos(params['category'], params['page'])

        elif params['action'] == 'play':
            # Play a video from a provided URL.
            play_video(params['video'])

        elif params['action'] == 'delete':
            # Delete a video from server
            if delete_video(params['video']):
                xbmc.executebuiltin('Container.Update({})'.format(get_url()))

        elif params['action'] == 'download':
            # Download a video from server to a defined destination
            download_video(params['video'], params['title'], params['ffmpeg_params'], params['list_type'])

        else:
            # If the provided paramstring does not contain a supported action
            # we raise an exception. This helps to catch coding errors,
            # e.g. typos in action names.
            raise ValueError('Invalid paramstring: {0}!'.format(paramstring))
    else:
        # If the plugin is called from Kodi UI without any parameters,
        # display the list of video categories
        list_categories()


# ::: START MAIN PROGRAM :::

SysEnv = SystemEnvironment()

if enable_cloud and recording_address == '0.0.0.0':
    log('You need to setup Cloud Server first, check IP/Port', xbmc.LOGERROR)
    notify(addon_name, loc(32191), icon=xbmcgui.NOTIFICATION_ERROR)
    quit()

if enable_vod and vod_address == '0.0.0.0' :
    log('You need to setup VOD Server first, check IP/Port', xbmc.LOGERROR)
    notify(addon_name, loc(32192), icon=xbmcgui.NOTIFICATION_ERROR)
    quit()

_url = sys.argv[0]
_handle = int(sys.argv[1])

if __name__ == '__main__':

    SysEnv.prepare()
    SysEnv.check()

    if SysEnv.isSupported and not SysEnv.isInstalled:
        if sys.argv[2][1:] == 'action=check':
            router(sys.argv[2][1:])
        OSD.ok('{} - Missing Environment'.format(addon_name), loc(32202))
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.telerising-cloudcontrol/?action=check")')
        quit()
    else:
        if sys.argv[2][1:] == 'action=clean' or sys.argv[2][1:] == 'action=kill_ffmpeg':
            router(sys.argv[2][1:])
            quit()
        servers = list()
        if enable_vod:
            servers.append('VOD')
        if enable_cloud:
            servers.append('Cloud')
        tr_videos = create_videodict(servers)
        router(sys.argv[2][1:])
