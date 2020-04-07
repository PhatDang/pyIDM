#!/usr/bin/env python
"""
    pyIDM

    multi-connections internet download manager, based on "pyCuRL/curl", "youtube_dl", and "PySimpleGUI"

    :copyright: (c) 2019-2020 by Mahmoud Elshahat.
    :license: GNU LGPLv3, see LICENSE for more details.
"""

# The purpose of this module is checking and auto installing dependencies
import os
import sys
import subprocess
import importlib.util

from . import config

# import config

# read dependencies from requirement.txt, 
current_directory = config.current_directory
parent_directory = os.path.dirname(config.current_directory)

# first look for the file in current directory, if not look for it in parent directory
if 'requirements.txt' in os.listdir(current_directory):
    file_name = os.path.join(current_directory, 'requirements.txt')
elif 'requirements.txt' in os.listdir(parent_directory):
    file_name = os.path.join(parent_directory, 'requirements.txt')
else:
    file_name = None

# print('requirements.txt located at: ', file_name)


try:
    with open(file_name) as fh:
        ext_pkgs = [x.strip() for x in fh.readlines()]
except Exception as e:
    # no need to print output as an error since requirements.txt won't be available in pypi release and requirements 
    # will be installed automatically 
    # print('error loading requirements.txt', e)
    # print('falling back to requirements listed in dependency.py')
    ext_pkgs = ['PySimpleGUI', 'pyperclip', 'plyer', 'certifi', 'youtube_dl', 'pycurl']

# list of dependency
missing_pkgs = [pkg for pkg in ext_pkgs if importlib.util.find_spec(pkg) is None]


def install_pkg(pkg_name):
    """install individual package"""
    print('start installing', pkg_name)
    r = subprocess.run([sys.executable, "-m", "pip", "install", pkg_name, '--user', '--upgrade'],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = r.stdout.decode('utf-8')
    if r.returncode != 0:
        print(pkg_name, 'failed to get installed')
        return False, log
    else:
        print(pkg_name, 'installed successfully')
        return True, log


def install_pkgs(pkgs):
    """install pkgs to current python environment
    :param pkgs: (list) list of packages to be installed"""

    status = False

    # handle the gui lib first
    if 'PySimpleGUI' in pkgs:
        print('PySimpleGUI is missing, will try to install it')
        done, _ = install_pkg('PySimpleGUI')
        if done:
            pkgs.remove('PySimpleGUI')
        else:
            return False  # will exit no need to continue

    if not pkgs:
        return True

    # import gui lib and choose default theme
    import PySimpleGUI as sg
    sg.change_look_and_feel('Reds')

    msg = (f"Looks like you have missing modules / packages:\n"
           f"\n"
           f"{pkgs},\n"
           f"\n"
           f"will try to install them automatically,\n"
           f"proceed: go ahead and install missing modules,\n"
           f"cancel- terminate application")
    layout = [
        [sg.T(msg)],
        [sg.T('Installation status:')],
        [sg.Multiline('', key='missing_pkg_status', size=(50, 8), autoscroll=True)],
        [sg.Multiline('', key='log', size=(50, 8), autoscroll=True, )],
        [sg.B('Proceed', key='Proceed'), sg.Cancel()]
    ]

    window = sg.Window(title=f'pyIDM ... Missing packages installation', layout=layout)
    while True:
        event, values = window()

        if event in (None, 'Cancel'):
            break

        if event == 'Proceed':
            if not pkgs:
                break

            failed_packages = []
            for pkg in pkgs[:]:
                done, log = install_pkg(pkg)
                window['missing_pkg_status'](f"{pkg} .......... {'Done' if done else 'Failed'} \n", append=True)
                window['log'](log, append=True)
                window.Refresh()

                if done:
                    pkgs.remove(pkg)
                else:
                    failed_packages.append(pkg)

            if failed_packages:
                window['missing_pkg_status'](
                    f"\nFailed to install some packages, {failed_packages} 'press cancel to terminate application",
                    append=True, text_color_for_value='red')
                status = False
            else:
                window['missing_pkg_status']('\nAll - ok, click proceed to continue', append=True, text_color='green')
                status = True

        window.Close()
        return status


def install_missing_pkgs():
    if missing_pkgs:
        print('require pkgs: ', ext_pkgs)
        print('missing pkgs: ', missing_pkgs)
        done = install_pkgs(missing_pkgs)
        if not done:
            return False

    return True

    # install_missing_pkgs()
