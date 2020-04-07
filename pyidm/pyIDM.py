#!/usr/bin/env python
"""
    pyIDM

    multi-connections internet download manager, based on "pyCuRL/curl", "youtube_dl", and "PySimpleGUI"

    :copyright: (c) 2019-2020 by Mahmoud Elshahat.
    :license: GNU LGPLv3, see LICENSE for more details.
"""

# This is the main application module ........................................


# standard modules
import os, sys
from threading import Thread
import time

# This code should stay on top to handle relative imports in case of direct call of pyIDM.py
if __package__ is None:
    path = os.path.realpath(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(path))
    sys.path.insert(0, os.path.dirname(os.path.dirname(path)))

    __package__ = 'pyidm'

    import pyidm

# check and auto install external modules defined in requirements.txt
from .dependency import install_missing_pkgs

done = install_missing_pkgs()
if not done:
    print('Missing dependencies, quit application')
    sys.exit(1)  # will exit application if failed to install

# local modules
from .utils import *
from . import config
from . import setting
from . import video
from .gui import MainWindow


def clipboard_listener():
    old_data = ''
    # monitor = True

    while True:

        new_data = clipboard_read()

        if new_data == 'any one there?':  # a possible message coming from new instance of this script
            clipboard_write('yes')  # it will be read by singleApp() as an exit signal
            config.main_window_q.put(('visibility', 'show'))  # restore main window if minimized

        if config.monitor_clipboard and new_data != old_data:
            if new_data.startswith('http') and ' ' not in new_data:
                config.main_window_q.put(('url', new_data))

            old_data = new_data

        # if config.clipboard_q.qsize() > 0:
        #     k, v = config.clipboard_q.get()
        #     if k == 'status' and v == config.Status.cancelled: break
        # elif k == 'monitor':
        #     monitor = v

        # monitor global termination flag
        if config.terminate:
            break

        time.sleep(0.2)


def singleApp():
    """send a message thru clipboard to check if an app instance already running"""
    original = clipboard_read()  # get original clipboard value
    clipboard_write('any one there?')
    time.sleep(0.3)
    answer = clipboard_read()
    clipboard_write(original)  # restore clipboard original value

    if answer == 'yes':
        return False
    else:
        return True


def main():
    # echo stdout, stderr to our gui
    # sys.stdout.write = echo_stdout(sys.stdout.write)
    # sys.stderr.write = echo_stderr(sys.stderr.write)

    # start log recorder
    Thread(target=log_recorder, daemon=True).start()

    log('Starting PyIDM version:', config.APP_VERSION, 'Frozen' if config.FROZEN else 'Non-Frozen')
    # log('starting application')
    log('operating system:', config.operating_system_info)

    # quit if there is previous instance of this script already running
    if not singleApp():
        print('previous instance already running')
        return

    log('current working directory:', config.current_directory)
    os.chdir(config.current_directory)

    # import youtube-dl in a separate thread
    Thread(target=video.import_ytdl, daemon=True).start()

    # load stored setting from disk
    setting.load_setting()
    config.d_list = setting.load_d_list()

    # run clipboard monitor thread
    Thread(target=clipboard_listener, daemon=True).start()

    # start gui main loop
    main_window = MainWindow(config.d_list)
    main_window.run()

    # set global termination flag
    config.terminate = True

    # Save setting to disk
    setting.save_setting()
    setting.save_d_list(config.d_list)


if __name__ == '__main__':
    main()
