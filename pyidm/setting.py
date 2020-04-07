"""
    pyIDM

    multi-connections internet download manager, based on "pyCuRL/curl", "youtube_dl", and "PySimpleGUI"

    :copyright: (c) 2019-2020 by Mahmoud Elshahat.
    :license: GNU LGPLv3, see LICENSE for more details.
"""

import os
import json

from . import config
from . import downloaditem
from .utils import log, handle_exceptions, update_object


def get_global_sett_folder():
    """return a proper global setting folder"""
    home_folder = os.path.expanduser('~')

    if config.operating_system == 'Windows':
        roaming = os.getenv('APPDATA')  # return APPDATA\Roaming\ under windows
        _sett_folder = os.path.join(roaming, f'.{config.APP_NAME}')

    elif config.operating_system == 'Linux':
        _sett_folder = f'{home_folder}/.config/{config.APP_NAME}/'

    elif config.operating_system == 'Darwin':
        _sett_folder = f'{home_folder}/Library/Application Support/{config.APP_NAME}/'

    else:
        _sett_folder = config.current_directory

    if not os.path.exists(_sett_folder):
        try:
            os.mkdir(_sett_folder)
        except Exception as e:
            _sett_folder = config.current_directory
            print('setting folder error:', e)

    return _sett_folder


config.global_sett_folder = get_global_sett_folder()


def locate_setting_folder():
    """check local folder and global setting folder for setting.cfg file"""
    if 'setting.cfg' in os.listdir(config.current_directory):
        return config.current_directory
    elif 'setting.cfg' in os.listdir(config.global_sett_folder):
        return config.global_sett_folder

    # no setting file found will check local folder for writing permission, otherwise will return global sett folder
    try:
        folder = config.current_directory
        with open(os.path.join(folder, 'test'), 'w') as test_file:
            test_file.write('0')
        os.unlink(os.path.join(folder, 'test'))
        return config.current_directory

    except PermissionError:
        log("No enough permission to store setting at local folder:", folder)
        log('Global setting folder will be selected:', config.global_sett_folder)
        return config.global_sett_folder


config.sett_folder = locate_setting_folder()


def load_d_list():
    """create and return a list of 'DownloadItem objects' based on data extracted from 'downloads.cfg' file"""
    d_list = []
    try:
        log('Load previous download items from', config.sett_folder)
        file = os.path.join(config.sett_folder, 'downloads.cfg')

        with open(file, 'r') as f:
            # expecting a list of dictionaries
            data = json.load(f)

        # converting list of dictionaries to list of DownloadItem() objects
        for dict_ in data:
            d = update_object(downloaditem.DownloadItem(), dict_)
            if d:  # if update_object() returned an updated object not None
                d_list.append(d)

        # clean d_list
        for d in d_list:
            status = config.Status.completed if d.progress >= 100 else config.Status.cancelled
            d.status = status
            d.live_connections = 0

    except FileNotFoundError:
        log('downloads.cfg file not found')
    except Exception as e:
        log(f'load_d_list()>: {e}')
    finally:
        if not isinstance(d_list, list):
            d_list = []
        return d_list


def save_d_list(d_list):
    try:
        data = []
        for d in d_list:
            data.append(d.get_persistent_properties())

        file = os.path.join(config.sett_folder, 'downloads.cfg')

        with open(file, 'w') as f:
            try:
                json.dump(data, f)
            except Exception as e:
                print('error save d_list:', e)
        log('list saved')
    except Exception as e:
        handle_exceptions(e)


def load_setting():
    setting = {}
    try:
        log('Load Application setting from', config.sett_folder)
        file = os.path.join(config.sett_folder, 'setting.cfg')
        with open(file, 'r') as f:
            setting = json.load(f)

    except FileNotFoundError:
        log('setting.cfg not found')
    except Exception as e:
        handle_exceptions(e)
    finally:
        if not isinstance(setting, dict):
            setting = {}

        # download folder
        folder = setting.get('download_folder', None)
        if folder and os.path.isdir(folder):
            config.download_folder = folder
        else:
            config.download_folder = os.path.join(os.path.expanduser("~"), 'Downloads')

        config.current_theme = setting.get('current_theme', config.DEFAULT_THEME)
        config.speed_limit = setting.get('speed_limit', 0)
        config.monitor_clipboard = setting.get('monitor_clipboard', True)
        config.show_download_window = setting.get('show_download_window', True)
        config.max_concurrent_downloads = setting.get('max_concurrent_downloads', config.DEFAULT_CONCURRENT_CONNECTIONS)
        config.max_connections = setting.get('max_connections', config.DEFAULT_CONNECTIONS)
        config.proxy = setting.get('proxy', '')
        config.segment_size = setting.get('segment_size', config.DEFAULT_SEGMENT_SIZE)
        config.last_update_check = setting.get('last_update_check', 0)
        config.update_frequency = setting.get('update_frequency', 1)

        config.log_level = setting.get('log_level', config.DEFAULT_LOG_LEVEL)


def save_setting():
    setting = dict()
    setting['download_folder'] = config.download_folder
    setting['current_theme'] = config.current_theme
    setting['speed_limit'] = config.speed_limit
    setting['monitor_clipboard'] = config.monitor_clipboard
    setting['show_download_window'] = config.show_download_window
    setting['max_concurrent_downloads'] = config.max_concurrent_downloads
    setting['max_connections'] = config.max_connections
    setting['proxy'] = config.proxy
    setting['segment_size'] = config.segment_size
    setting['last_update_check'] = config.last_update_check
    setting['update_frequency'] = config.update_frequency

    setting['log_level'] = config.log_level

    try:
        file = os.path.join(config.sett_folder, 'setting.cfg')
        with open(file, 'w') as f:
            json.dump(setting, f)
            log('setting saved')
    except Exception as e:
        handle_exceptions(e)
