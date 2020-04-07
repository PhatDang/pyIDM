"""
    pyIDM

    multi-connections internet download manager, based on "pyCuRL/curl", "youtube_dl", and "PySimpleGUI"

    :copyright: (c) 2019-2020 by Mahmoud Elshahat.
    :license: GNU LGPLv3, see LICENSE for more details.
"""
import sys

import PySimpleGUI as sg
import os
import re
import time
import copy
import subprocess
from threading import Thread, Barrier, Timer, Lock
from collections import deque

from .utils import *
from . import config
from .config import Status
from . import update
from .brain import brain
from . import video
from .video import Video, check_ffmpeg, download_ffmpeg, unzip_ffmpeg, get_ytdl_options
from .about import about_notes
from .downloaditem import DownloadItem

# todo: this module needs some clean up

# gui setting
config.all_themes = natural_sort(sg.ListOfLookAndFeelValues())
sg.SetOptions(icon=config.APP_ICON, font='Helvetica 11', auto_size_buttons=True, progress_meter_border_depth=0,
              border_width=1)


# todo: change buttons to images, pysimplegui can set transparent buttons see below link
# https://github.com/PySimpleGUI/PySimpleGUI/issues/142#issuecomment-590126165


class MainWindow:
    def __init__(self, d_list):
        """This is the main application user interface window"""

        # current download_item
        self.d = DownloadItem()

        # main window
        self.window = None

        # download windows
        self.download_windows = {}  # dict that holds Download_Window() objects --> {d.id: Download_Window()}

        # url
        self.url_timer = None  # usage: Timer(0.5, self.refresh_headers, args=[self.d.url])
        self.bad_headers = [0, range(400, 404), range(405, 418), range(500, 506)]  # response codes

        # youtube specific
        self.video = None
        self.yt_id = 0  # unique id for each youtube thread
        self.playlist = []
        self.pl_title = ''
        self.pl_quality = None
        self._pl_menu = []
        self._stream_menu = []
        self.s_bar_lock = Lock()  # a lock to access a video quality progress bar from threads
        self._s_bar = 0  # side progress bar for video quality loading
        self._m_bar = 0  # main playlist progress par
        self.stream_menu_selection = ''

        # download
        self.pending = deque()
        self.disabled = True  # for download button

        # download list
        self.d_headers = ['i', 'num', 'name', 'progress', 'speed', 'time_left', 'downloaded', 'total_size', 'status', ]
        # 'resumable', 'folder', 'max_connections', 'live_connections', 'remaining_parts']
        self.d_list = d_list  # list of DownloadItem() objects
        self.selected_row_num = None
        self._selected_d = None

        # update
        self.new_version_available = False
        self.new_version_description = None

        # initial setup
        self.setup()

    def setup(self):
        """initial setup"""
        # theme
        sg.ChangeLookAndFeel(config.current_theme)

        # download folder
        if not self.d.folder:
            self.d.folder = config.download_folder

        # main window
        self.start_window()

        self.reset()
        self.reset_video_controls()

    def read_q(self):
        # read incoming messages from queue
        for _ in range(config.main_window_q.qsize()):
            k, v = config.main_window_q.get()
            if k == 'log':
                try:
                    if len(config.log_text) > 1000_000:
                        config.log_text = config.log_text[1000:]  # remove oldest 1000 characters
                    config.log_text += v
                    self.window['log'](config.log_text)
                except:
                    pass

                # show youtube_dl activity in status text
                if '[youtube]' in v:
                    self.set_status(v.strip('\n'))

            elif k == 'url':
                self.window.Element('url').Update(v)
                self.url_text_change()

            elif k == 'monitor':
                self.window.Element('monitor').Update(v)

            elif k == 'visibility' and v == 'show':
                self.window.BringToFront()
                sg.popup_ok('application is already running', title=config.APP_NAME)

            elif k == 'download':  # receive download requests
                self.start_download(*v)

            elif k == 'popup':
                type_ = v['type_']
                if type_ == 'popup_no_buttons':
                    sg.popup_no_buttons(v['msg'], title=v['title'])
                else:
                    sg.popup(v['msg'], title=v['title'])

            elif k == 'show_update_gui':  # show update gui
                self.show_update_gui()

    # region gui design
    def create_window(self):
        # main tab
        # column for playlist menu
        col1 = [[sg.Combo(values=self.pl_menu, size=(34, 1), key='pl_menu', enable_events=True)],
                [sg.ProgressBar(max_value=100, size=(20, 5), key='m_bar')]]

        # column for stream menu
        col2 = [[sg.Combo(values=self.stream_menu, size=(34, 1), key='stream_menu', enable_events=True)],
                [sg.ProgressBar(max_value=100, size=(20, 5), key='s_bar')]]

        main_layout = [
            [sg.Text(f'{config.APP_NAME}', font='any 20', justification='center', key='app_title')],

            # url
            [sg.T('', size=(50, 1), justification='center', key='update_note', enable_events=True)],
            [sg.Text('URL:'), sg.Input(self.d.url, enable_events=True, key='url', size=(66, 1)),
             sg.Button('Retry', key='Retry', tooltip=' retry ')],
            [sg.Text('Status:', size=(70, 1), key='status')],

            # spacer
            [sg.T('', font='any 1')],

            # youtube playlist ⚡
            [sg.Frame('Playlist / videos:', key='youtube_frame', pad=(5, 5), layout=[
                [sg.Column(col1, size=(300, 40), pad=(10, 5)),
                 sg.Button('⚡', pad=(0, 0), size=(2, 1), tooltip='download this playlist', key='pl_download'),
                 sg.Column(col2, size=(300, 40), pad=(2, 5))]]
                      )
             ],

            # file info
            [sg.Text('File name:'), sg.Input('', size=(65, 1), key='name', enable_events=True)],
            [sg.T('-' * 300, key='file_properties')],
            [sg.Text('Save To:  '), sg.Input(config.download_folder, size=(55, 1), key='folder', enable_events=True),
             sg.FolderBrowse(key='browse')],

            # download button

            [sg.Column([[sg.B('Download', font='Helvetica 14', border_width=0, pad=(0, 0), size=(10, 1),
                              tooltip='Main download Engine'),
                         sg.B('▼', font='Helvetica 14', pad=(5, 0), border_width=0, key='ytdl_dl_btn', size=(2, 1),
                              tooltip='Alternative Download with youtube-dl')]],
                       size=(150, 40), justification='center')],

        ]

        # downloads tab
        table_right_click_menu = ['Table', ['!Options for selected file:', '---', 'Open File', 'Open File Location',
                                            '▶ Watch while downloading', 'copy webpage url', 'copy download url',
                                            '⏳ Schedule download', '⏳ Cancel schedule!', 'properties']]
        headings = ['i', 'num', 'name', 'progress', 'speed', 'left', 'done', 'size', 'status']
        spacing = [' ' * 4, ' ' * 3, ' ' * 30, ' ' * 3, ' ' * 6, ' ' * 7, ' ' * 6, ' ' * 6, ' ' * 10]

        downloads_layout = [[sg.Button('Resume'), sg.Button('Cancel'), sg.Button('Refresh'),
                             sg.Button('Folder'), sg.Button('D.Window'),
                             sg.T(' ' * 5), sg.T('Item:'),
                             sg.T('---', key='selected_row_num', text_color='white', background_color='red')],
                            [sg.Table(values=[spacing], headings=headings, size=(70, 13), justification='left',
                                      vertical_scroll_only=False, key='table', enable_events=True, font='any 9',
                                      right_click_menu=table_right_click_menu)],
                            [sg.Button('Resume All'), sg.Button('Stop All'), sg.B('Schedule All'),
                             sg.Button('Delete', button_color=('white', 'red')),
                             sg.Button('Delete All', button_color=('white', 'red'))],
                            ]

        # setting tab
        seg_size = config.segment_size // 1024  # kb
        if seg_size >= 1024:
            seg_size = seg_size // 1024
            seg_size_unit = 'MB'
        else:
            seg_size_unit = 'KB'

        setting_layout = [[sg.T('User Setting:'), sg.T(' ', size=(50, 1)), sg.Button(' about ', key='about')],
                          [sg.Text('Select Theme:'),
                           sg.Combo(values=config.all_themes, default_value=config.current_theme, size=(15, 1),
                                    enable_events=True, key='themes'),
                           sg.Text(f' Total: {len(config.all_themes)} Themes')],
                          [sg.Checkbox('Speed Limit:', default=True if config.speed_limit else False,
                                       key='speed_limit_switch', enable_events=True,
                                       tooltip='examples: 50 k, 10kb, 2m, 3mb, 20, 10MB '),
                           sg.Input(default_text=config.speed_limit if config.speed_limit else '', size=(10, 1),
                                    key='speed_limit',
                                    disabled=False if config.speed_limit else True, enable_events=True),
                           sg.T('0', size=(30, 1), key='current_speed_limit')],
                          [sg.Checkbox('Monitor copied urls in clipboard', default=config.monitor_clipboard,
                                       key='monitor', enable_events=True)],
                          [sg.Checkbox("Show download window", key='show_download_window',
                                       default=config.show_download_window, enable_events=True)],
                          [sg.Text('Max concurrent downloads:'),
                           sg.Combo(values=[x for x in range(1, 101)], size=(5, 1), enable_events=True,
                                    key='max_concurrent_downloads', default_value=config.max_concurrent_downloads),
                           sg.Text('  ...    Max connections per download:'),
                           sg.Combo(values=[x for x in range(1, 101)], size=(5, 1), enable_events=True,
                                    key='max_connections', default_value=config.max_connections)],
                          [sg.T('Proxy: '), sg.I(default_text=config.proxy, size=(30, 1),
                                                 tooltip='proxy server ip:port, ex: 157.245.224.29:3128',
                                                 key='proxy', enable_events=True)],
                          [sg.Text('Segment size:'), sg.Input(default_text=seg_size, size=(6, 1),
                                                              enable_events=True, key='segment_size'),
                           sg.Combo(values=['KB', 'MB'], default_value=seg_size_unit, size=(4, 1), key='seg_size_unit',
                                    enable_events=True),
                           sg.Text(f'current value: {size_format(config.segment_size)}', size=(30, 1),
                                   key='seg_current_value')],
                          [sg.T('Setting Folder:'), sg.Combo(values=['Local', 'Global'],
                                                             default_value='Local' if config.sett_folder == config.current_directory else 'Global',
                                                             key='sett_folder', enable_events=True),
                           sg.T(config.sett_folder, key='sett_folder_text', size=(50, 1))],
                          [sg.T('')],
                          [sg.T('Check for update every:'),
                           sg.Combo([1, 7, 30], default_value=config.update_frequency, size=(4, 1),
                                    key='update_frequency', enable_events=True), sg.T('day(s).')],
                          [sg.T('    '),
                           sg.T('Youtube-dl version = 00.00.00', size=(50, 1), key='youtube_dl_update_note'),
                           sg.Button('Check for update', key='update_youtube_dl')],
                          [sg.T('    '),
                           sg.T(f'pyIDM version = {config.APP_VERSION}', size=(50, 1), key='pyIDM_version_note'),
                           sg.Button('Check for update', key='update_pyIDM')],
                          [sg.T('')],

                          ]
        # put setting layout in a scrollable column, to add more options
        setting_layout = [[sg.Column(setting_layout, scrollable=True, vertical_scroll_only=True, size=(650, 370),
                                     key='col')]]

        log_layout = [[sg.T('Details events:')], [sg.Multiline(default_text='', size=(70, 21), key='log', font='any 8',
                                                               autoscroll=True)],
                      [sg.T('Log Level:'), sg.Combo([1, 2, 3], default_value=config.log_level, enable_events=True,
                                                    size=(3, 1), key='log_level',
                                                    tooltip='*(1=Standard, 2=Verbose, 3=Debugging)'),
                       sg.T(f'*This log will be auto-saved at {config.sett_folder}', font='any 8', size=(70, 1),
                            tooltip=config.current_directory),
                       sg.Button('Clear Log')]]

        layout = [[sg.TabGroup(
            [[sg.Tab('Main', main_layout), sg.Tab('Downloads', downloads_layout), sg.Tab('Setting', setting_layout),
              sg.Tab('Log', log_layout)]],
            key='tab_group')],
            [sg.StatusBar('', size=(81, 1), font='Helvetica 11', key='status_bar')]
        ]

        # window
        window = sg.Window(title=config.APP_TITLE, layout=layout, size=(700, 450), margins=(2, 2))
        return window

    def start_window(self):
        self.window = self.create_window()
        self.window.Finalize()

        # expand elements to fit
        elements = ['url', 'name', 'folder', 'youtube_frame', 'm_bar', 's_bar', 'pl_menu', 'update_note', 'app_title',
                    'stream_menu', 'log', 'status_bar', 'file_properties']  # elements to be expanded
        for e in elements:
            self.window[e].expand(expand_x=True)

        # bind keys events for table, it is tkinter specific
        self.window['table'].Widget.bind("<Button-3>", self.table_right_click)  # right click
        self.window['table'].bind('<Double-Button-1>', '_double_clicked')  # double click
        self.window['table'].bind('<Return>', '_enter_key')  # Enter key

        # log text, disable word wrap
        self.window['log'].Widget.config(wrap='none')

    def restart_window(self):
        try:
            self.window.Close()
        except:
            pass

        self.start_window()

        if self.video:
            self.update_pl_menu()
            self.update_stream_menu()
        else:
            self.pl_menu = ['Playlist']
            self.stream_menu = ['Video quality']

    def table_right_click(self, event):
        try:
            # select row under mouse
            id_ = self.window['table'].Widget.identify_row(event.y)  # first row = 1 not 0
            if id_:
                # mouse pointer over item
                self.window['table'].Widget.selection_set(id_)
                self.select_row(int(id_) - 1)  # get count start from zero
                self.window['table']._RightClickMenuCallback(event)
        except:
            pass

    def select_row(self, row_num):
        try:
            self.selected_row_num = int(row_num)
            # self.selected_d = self.d_list[self.selected_row_num]

            # update text widget that display selected row number
            self.window['selected_row_num']('---' if row_num is None else row_num + 1)

        except Exception as e:
            log('MainWindow.select_row(): ', e)

    def select_tab(self, tab_name):
        try:
            self.window[tab_name].Select()
        except Exception as e:
            print(e)

    def update_gui(self):

        # update Elements
        try:
            if self.window['name'].get() != self.d.name:  # it will prevent cursor jump to end when modifying name
                self.window['name'](self.d.name)

            file_properties = f'Size: {size_format(self.d.total_size)} - Type: {self.d.type} ' \
                              f'{"fragments" if self.d.fragments else ""} - ' \
                              f'Protocol: {self.d.protocol} - Resumable: {"Yes" if self.d.resumable else "No"} ...'
            self.window['file_properties'](file_properties)

            # download list / table
            table_values = [[self.format_cell_data(key, getattr(d, key, '')) for key in self.d_headers] for d in
                            self.d_list]
            self.window.Element('table').Update(values=table_values[:])

            # re-select the previously selected row in the table
            if self.selected_row_num is not None:
                self.window.Element('table').Update(select_rows=(self.selected_row_num,))
            else:
                # update selected item number
                self.window.Element('selected_row_num').Update('---')

            # update status bar
            self.window.Element('status_bar').Update(
                f'Active downloads: {len(self.active_downloads)}, pending: {len(self.pending)}')

            # setting
            speed_limit = size_format(config.speed_limit * 1024) if config.speed_limit > 0 else "_no limit_"
            self.window['current_speed_limit'](f'current speed limit: {speed_limit}')

            self.window['youtube_dl_update_note'](
                f'Youtube-dl version = {config.ytdl_VERSION}, Latest version = {config.ytdl_LATEST_VERSION}')
            self.window['pyIDM_version_note'](
                f'pyIDM version = {config.APP_VERSION}, Latest version = {config.APP_LATEST_VERSION}')

        except Exception as e:
            log('MainWindow.update_gui() error:', e)

    def enable(self):
        self.disabled = False

    def disable(self):
        self.disabled = True

    def set_status(self, text):
        """update text under url input widget"""
        try:
            self.window.Element('status').Update(text)
        except:
            pass

    # endregion

    def run(self):
        """main loop"""
        timer1 = 0
        timer2 = 0
        one_time = True
        while True:
            event, values = self.window.Read(timeout=50)
            # self.event, self.values = event, values
            # if event != '__TIMEOUT__': print(event, values)

            if event is None:
                self.main_frameOnClose()
                break

            elif event == 'update_note':
                # if clicked on update notification text
                if self.new_version_available:
                    self.update_app(remote=False)

            elif event == 'url':
                self.url_text_change()

            elif event == 'Download':
                self.download_btn()

            elif event == 'ytdl_dl_btn':
                self.ytdl_downloader()

            elif event == 'folder':
                if values['folder']:
                    config.download_folder = os.path.abspath(values['folder'])
                else:  # in case of empty entries
                    self.window.Element('folder').Update(config.download_folder)

            elif event == 'name':
                self.d.name = validate_file_name(values['name'])

            elif event == 'Retry':
                self.retry()

            # downloads tab events -----------------------------------------------------------------------------------
            elif event == 'table':
                try:
                    row_num = values['table'][0]
                    self.select_row(row_num)
                except Exception as e:
                    # log("MainWindow.run:if event == 'table': ", e)
                    pass

            elif event in ('table_double_clicked', 'table_enter_key', 'Open File', '▶ Watch while downloading') and \
                    self.selected_d:
                if self.selected_d.status == Status.completed:
                    open_file(self.selected_d.target_file)
                else:
                    open_file(self.selected_d.temp_file)

            # table right click menu event
            elif event == 'Open File Location':
                self.open_file_location()

            elif event == 'copy webpage url':
                clipboard_write(self.selected_d.url)

            elif event == 'copy download url':
                clipboard_write(self.selected_d.eff_url)

            elif event == 'properties':
                # right click properties
                try:
                    d = self.selected_d

                    if d:
                        text = f'Name: {d.name} \n' \
                               f'Folder: {d.folder} \n' \
                               f'Progress: {d.progress}% \n' \
                               f'Downloaded: {size_format(d.downloaded)} \n' \
                               f'Total size: {size_format(d.total_size)} \n' \
                               f'Status: {d.status} \n' \
                               f'Resumable: {d.resumable} \n' \
                               f'Type: {d.type} \n' \
                               f'Protocol: {d.protocol} \n' \
                               f'Webpage url: {d.url}'

                        sg.popup_scrolled(text, title='File properties')
                except Exception as e:
                    log('gui> properties>', e)

            elif event == '⏳ Schedule download':
                response = self.ask_for_sched_time(msg=self.selected_d.name)
                if response:
                    self.selected_d.sched = response

            elif event == '⏳ Cancel schedule!':
                self.selected_d.sched = None

            elif event == 'Resume':
                self.resume_btn()

            elif event == 'Cancel':
                self.cancel_btn()

            elif event == 'Refresh':
                self.refresh_link_btn()

            elif event == 'Folder':
                self.open_file_location()

            elif event == 'D.Window':
                # create download window
                if self.selected_d and self.selected_d.status == Status.downloading:
                    d = self.selected_d
                    if d.id not in self.download_windows:
                        self.download_windows[d.id] = DownloadWindow(d=d)
                    else:
                        self.download_windows[d.id].focus()

            elif event == 'Resume All':
                self.resume_all_downloads()

            elif event == 'Stop All':
                self.stop_all_downloads()

            elif event == 'Schedule All':
                response = self.ask_for_sched_time(msg='Schedule all non completed files')
                if response:
                    for d in self.d_list:
                        if d.status in (Status.pending, Status.cancelled):
                            d.sched = response

            elif event == 'Delete':
                self.delete_btn()

            elif event == 'Delete All':
                self.delete_all_downloads()

            # video events
            elif event == 'pl_download':
                self.download_playlist()

            elif event == 'pl_menu':
                self.playlist_OnChoice(values['pl_menu'])

            elif event == 'stream_menu':
                self.stream_OnChoice(values['stream_menu'])

            # setting tab -------------------------------------------------------------------------------------------
            elif event == 'themes':
                config.current_theme = values['themes']
                sg.ChangeLookAndFeel(config.current_theme)

                # close all download windows if existed
                for win in self.download_windows.values():
                    win.window.Close()
                self.download_windows = {}

                self.restart_window()
                self.select_tab('Setting')

            elif event == 'speed_limit_switch':
                switch = values['speed_limit_switch']

                if switch:
                    self.window['speed_limit'](disabled=False)
                else:
                    config.speed_limit = 0
                    self.window['speed_limit']('', disabled=True)  # clear and disable

            elif event == 'speed_limit':
                sl = values['speed_limit'].replace(' ', '')  # if values['speed_limit'] else 0

                # validate speed limit,  expecting formats: number + (k, kb, m, mb) final value should be in kb
                # pattern \d*[mk]b?

                match = re.fullmatch(r'\d+([mk]b?)?', sl, re.I)
                if match:
                    # print(match.group())

                    digits = re.match(r"[0-9]+", sl, re.I).group()
                    digits = int(digits)

                    letters = re.search(r"[a-z]+", sl, re.I)
                    letters = letters.group().lower() if letters else None

                    # print(digits, letters)

                    if letters in ('k', 'kb', None):
                        sl = digits
                    elif letters in ('m', 'mb'):
                        sl = digits * 1024
                else:
                    sl = 0

                config.speed_limit = sl
                # print('speed limit:', config.speed_limit)

            elif event == 'max_concurrent_downloads':
                config.max_concurrent_downloads = int(values['max_concurrent_downloads'])

            elif event == 'max_connections':
                mc = int(values['max_connections'])
                if mc > 0:
                    # self.max_connections = mc
                    config.max_connections = mc

            elif event == 'monitor':
                config.monitor_clipboard = values['monitor']

            elif event == 'show_download_window':
                config.show_download_window = values['show_download_window']

            elif event == 'proxy':
                if isinstance(values['proxy'], str):
                    config.proxy = values.get('proxy', '')
                print('config.proxy = ', config.proxy)

            elif event in ('segment_size', 'seg_size_unit'):
                try:
                    seg_size_unit = values['seg_size_unit']
                    if seg_size_unit == 'KB':
                        seg_size = int(values['segment_size']) * 1024  # convert from kb to bytes
                    else:
                        seg_size = int(values['segment_size']) * 1024 * 1024  # convert from mb to bytes

                    config.segment_size = seg_size
                    self.window['seg_current_value'](f'current value: {size_format(config.segment_size)}')
                    self.d.segment_size = seg_size

                except:
                    pass

            elif event == 'sett_folder':
                selected = values['sett_folder']
                if selected == 'Local':
                    # choose local folder as a setting folder
                    config.sett_folder = config.current_directory

                    # remove setting.cfg from global folder
                    delete_file(os.path.join(config.global_sett_folder, 'setting.cfg'))
                else:
                    # choose global folder as a setting folder
                    config.sett_folder = config.global_sett_folder

                    # remove setting.cfg from local folder
                    delete_file(os.path.join(config.current_directory, 'setting.cfg'))

                # update display test
                try:
                    self.window['sett_folder_text'](config.sett_folder)
                except:
                    pass

            elif event == 'update_frequency':
                selected = values['update_frequency']
                config.update_frequency = selected  # config.update_frequency_map[selected]

            elif event == 'update_youtube_dl':
                self.update_ytdl()

            elif event in ['update_pyIDM']:
                Thread(target=self.update_app, daemon=True).start()

            # log ---------------------------------------------------------------------------------------------------
            elif event == 'log_level':
                config.log_level = int(values['log_level'])
                log('Log Level changed to:', config.log_level)

            elif event == 'Clear Log':
                try:
                    self.window['log']('')
                    config.log_text = ''
                except:
                    pass

            # elif event == 'Save Log':
            #     save_log()

            # about window
            elif event == 'about':
                self.window['about'](disabled=True)
                sg.PopupNoButtons(about_notes, title=f'About {config.APP_NAME}', keep_on_top=True)
                self.window['about'](disabled=False)

            # Run every n seconds
            if time.time() - timer1 >= 0.5:
                timer1 = time.time()

                # gui update
                self.update_gui()

                # read incoming requests and messages from queue
                self.read_q()

                # scheduled downloads
                self.check_scheduled()

                # process pending jobs
                if self.pending and len(self.active_downloads) < config.max_concurrent_downloads:
                    self.start_download(self.pending.popleft(), silent=True)

            # run download windows if existed
            keys = list(self.download_windows.keys())
            for i in keys:
                win = self.download_windows[i]
                win.run()
                if win.event is None:
                    self.download_windows.pop(i, None)

            # run one time, reason this is here not in setup, is to minimize gui loading time
            if one_time:
                one_time = False
                # check availability of ffmpeg in the system or in same folder with this script
                self.ffmpeg_check()

                # check_for_update
                t = time.localtime()
                today = t.tm_yday  # today number in the year range (1 to 366)

                try:
                    days_since_last_update = today - config.last_update_check
                    log('days since last check for update:', days_since_last_update, 'day(s).')

                    if days_since_last_update >= config.update_frequency:
                        Thread(target=self.check_for_update, daemon=True).start()
                        Thread(target=self.check_for_ytdl_update, daemon=True).start()
                        config.last_update_check = today
                except Exception as e:
                    log('MainWindow.run()>', e)

            if time.time() - timer2 >= 1:
                timer2 = time.time()
                # update notification
                if self.new_version_available:
                    self.animate_update_note()
                else:
                    self.window['update_note']('')

    # region headers
    def refresh_headers(self, url):
        if self.d.url != '':
            self.change_cursor('busy')
            Thread(target=self.get_header, args=[url], daemon=True).start()

    def get_header(self, url):
        # curl_headers = get_headers(url)
        self.d.update(url)

        # update headers only if no other curl thread created with different url
        if url == self.d.url:

            self.set_status(self.d.status_code_description)

            # enable download button
            if self.d.status_code not in self.bad_headers and self.d.type != 'text/html':
                self.enable()

            # check if the link contains stream videos by youtube-dl
            Thread(target=self.youtube_func, daemon=True).start()

            self.change_cursor('default')

    # endregion

    # region download
    @property
    def active_downloads(self):
        # update active downloads
        _active_downloads = set(d.id for d in self.d_list if d.status == config.Status.downloading)
        config.active_downloads = _active_downloads

        return _active_downloads

    def start_download(self, d, silent=False, downloader=None):
        """
        Receive a DownloadItem and pass it to brain
        :param bool silent: True or False, show a warninig dialogues
        :param DownloadItem d: DownloadItem() object
        :param downloader: name of alternative  downloader
        """

        if d is None:
            return

        # check for ffmpeg availability in case this is a dash video
        if d.type == 'dash' or 'm3u8' in d.protocol:
            # log('Dash video detected')
            if not self.ffmpeg_check():
                log('Download cancelled, FFMPEG is missing')
                return 'cancelled'

        # validate destination folder for existence and permissions
        # in case of missing download folder value will fallback to current download folder
        folder = d.folder or config.download_folder
        try:
            with open(os.path.join(folder, 'test'), 'w') as test_file:
                test_file.write('0')
            os.unlink(os.path.join(folder, 'test'))

            # update download item
            d.folder = folder
        except FileNotFoundError:
            sg.Popup(f'destination folder {folder} does not exist', title='folder error')
            return 'error'
        except PermissionError:
            sg.Popup(f"you don't have enough permission for destination folder {folder}", title='folder error')
            return 'error'
        except Exception as e:
            sg.Popup(f'problem in destination folder {repr(e)}', title='folder error')
            return 'error'

        # validate file name
        if d.name == '':
            sg.popup("File name can't be empty!!", title='invalid file name!!')
            return 'error'

        # check if file with the same name exist in destination
        if os.path.isfile(d.target_file):
            #  show dialogue
            msg = 'File with the same name already exist in ' + d.folder + '\n Do you want to overwrite file?'
            response = sg.PopupYesNo(msg)

            if response != 'Yes':
                log('Download cancelled by user')
                return 'cancelled'
            else:
                delete_file(d.target_file)

        # ------------------------------------------------------------------
        # search current list for previous item with same name, folder
        found_index = self.file_in_d_list(d.target_file)
        if found_index is not None:  # might be zero, file already exist in d_list
            log('download item', d.num, 'already in list, check resume availability')
            # get download item from the list
            d_from_list = self.d_list[found_index]
            d.id = d_from_list.id

            # default
            response = 'Resume'

            if not silent:
                #  show dialogue
                msg = f'File with the same name: \n{self.d.name},\n already exist in download list\n' \
                      'Do you want to resume this file?\n' \
                      'Resume ==> continue if it has been partially downloaded ... \n' \
                      'Overwrite ==> delete old downloads and overwrite existing item... \n' \
                      'note: "if you need fresh download, you have to change file name \n' \
                      'or target folder or delete same entry from download list'
                # response = sg.PopupYesNo(msg)
                window = sg.Window(title='', layout=[[sg.T(msg)], [sg.B('Resume'), sg.B('Overwrite'), sg.B('Cancel')]])
                response, _ = window()
                window.close()

            #
            if response == 'Resume':
                print('resuming')

                # to resume, size must match, otherwise it will just overwrite
                if d.size == d_from_list.size:
                    log('resume is possible')
                    # get the same segment size
                    d.segment_size = d_from_list.segment_size
                else:
                    log('file: ', d.name, 'has different size and will be downloaded from beginning')
                    d.delete_tempfiles()

            elif response == 'Overwrite':
                print('overwrite')
                d.delete_tempfiles()

            else:
                log('Download cancelled by user')
                d.status = Status.cancelled
                return

        # ------------------------------------------------------------------

        else:  # new file
            print('new file')
            # generate unique id number for each download
            d.id = len(self.d_list)

            # add to download list
            self.d_list.append(d)

        # if max concurrent downloads exceeded, this download job will be added to pending queue
        if len(self.active_downloads) >= config.max_concurrent_downloads:
            d.status = Status.pending
            self.pending.append(d)
            return

        # start downloading
        if config.show_download_window and not silent:
            # create download window
            self.download_windows[d.id] = DownloadWindow(d)

        # create and start brain in a separate thread
        Thread(target=brain, daemon=True, args=(d, downloader)).start()

    def stop_all_downloads(self):
        # change status of pending items to cancelled
        for d in self.d_list:
            d.status = Status.cancelled

        self.pending.clear()

    def resume_all_downloads(self):
        # change status of all non completed items to pending
        for d in self.d_list:
            if d.status == Status.cancelled:
                self.start_download(d, silent=True)

    def file_in_d_list(self, target_file):
        for i, d in enumerate(self.d_list):
            if d.target_file == target_file:
                return i
        return None

    def download_btn(self, downloader=None):

        if self.disabled:
            sg.popup_ok('Nothing to download', 'it might be a web page or invalid url link',
                        'check your link or click "Retry"')
            return

        d = copy.copy(self.d)
        d.folder = config.download_folder

        r = self.start_download(d, downloader=downloader)

        if r not in ('error', 'cancelled', False):
            self.select_tab('Downloads')

    def ytdl_downloader(self):
        """launch youtube-dl in terminal with proper command args.
        This method is very limited, basically mimic running youtube-dl from command line"""
        d = self.d
        ytdl_executable = 'youtube-dl.exe' if config.FROZEN else f'"{sys.executable}" -m youtube_dl'

        verbose = '-v' if config.log_level >= 3 else ''

        if not self.video:
            cmd = f'{ytdl_executable} {self.d.url} {verbose} --ffmpeg-location {config.ffmpeg_actual_path}'
        else:
            name = d.target_file.replace("\\", "/")
            if d.type == 'dash':
                # default format: bestvideo+bestaudio/best
                requested_format = f'"{d.format_id}"+"{d.audio_format_id}"/"{d.format_id}"+bestaudio/best'
            else:
                requested_format = f'"{d.format_id}"/best'

            # creating command
            cmd = f'{ytdl_executable} -f {requested_format} {d.url} -o "{name}" {verbose} --hls-use-mpegts --ffmpeg-location {config.ffmpeg_actual_path}'
            log('cmd:', cmd)

        # executing command
        if config.operating_system == 'Windows':
            # write a batch file to start anew cmd terminal
            batch_file = os.path.join(config.current_directory, 'ytdl_cmd.bat')
            with open(batch_file, 'w') as f:
                f.write(cmd + '\npause')

            # execute batch file
            os.startfile(batch_file)
        else:
            # not tested yet
            subprocess.Popen([os.getenv('SHELL'), '-i', '-c', cmd])

        # self.download_btn(downloader='ytdl')

    # endregion

    # region downloads tab
    @property
    def selected_d(self):
        self._selected_d = self.d_list[self.selected_row_num] if self.selected_row_num is not None else None
        return self._selected_d

    @selected_d.setter
    def selected_d(self, value):
        self._selected_d = value

    @staticmethod
    def format_cell_data(k, v):
        """take key, value and prepare it for display in cell"""
        if k in ['size', 'total_size', 'downloaded']:
            v = size_format(v)
        elif k == 'speed':
            v = size_format(v, '/s')
        elif k in ('percent', 'progress'):
            v = f'{v}%' if v else '---'
        elif k == 'time_left':
            v = time_format(v)
        elif k == 'resumable':
            v = 'yes' if v else 'no'
        elif k == 'name':
            v = validate_file_name(v)

        return v

    def resume_btn(self):
        # todo: fix resume parameters
        if self.selected_row_num is None:
            return

        # print_object(self.selected_d)

        self.start_download(self.selected_d, silent=True)

    def cancel_btn(self):
        if self.selected_row_num is None:
            return

        d = self.selected_d
        if d.status == Status.pending:
            self.d_list[d.id].status = Status.cancelled
            self.pending.pop(d.id)

        elif d.status == Status.downloading:
            d.status = Status.cancelled

    def delete_btn(self):
        if self.selected_row_num is None:
            return

        # todo: should be able to delete items anytime by making download item id unique and number changeable
        # abort if there is items in progress or paused
        if self.active_downloads:
            msg = "Can't delete items while downloading.\nStop or cancel all downloads first!"
            sg.Popup(msg)
            return

        # confirm to delete
        msg = "Warninig!!!\nAre you sure you want to delete!\n%s?" % self.selected_d.name
        r = sg.PopupYesNo(msg, title='Delete file?', keep_on_top=True)
        if r != 'Yes': return

        try:
            # pop item
            d = self.d_list.pop(self.selected_row_num)

            # update count numbers for remaining items
            n = len(self.d_list)
            for i in range(n):
                self.d_list[i].id = i

            # fix a selected item number if it no longer exist
            if not self.d_list:
                self.selected_row_num = None
            else:
                last_num = len(self.d_list) - 1
                if self.selected_row_num > last_num:
                    self.selected_row_num = last_num

            # delete temp folder on disk
            d.delete_tempfiles()

        except:
            pass

    def delete_all_downloads(self):
        # abort if there is items in progress or paused
        if self.active_downloads:
            msg = "Can't delete items while downloading.\nStop or cancel all downloads first!"
            sg.Popup(msg)
            return

        # warning / confirmation dialog, user has to write ok to proceed
        msg = 'Delete all items and their progress temp files\n' \
              'Type the word "delete" and hit ok\n'
        response = sg.PopupGetText(msg, title='Warning!!', keep_on_top=True)
        if response == 'delete':
            log('start deleting all download items')
        else:
            return

        self.stop_all_downloads()

        # selected item number
        self.selected_row_num = None

        # pop item
        n = len(self.d_list)

        # delete temp files
        for i in range(n):
            d = self.d_list[i]
            d.delete_tempfiles()

        self.d_list.clear()

    def open_file_location(self):
        if self.selected_row_num is None:
            return

        d = self.selected_d

        try:
            folder = os.path.abspath(d.folder)
            file = d.target_file

            if config.operating_system == 'Windows':
                if not os.path.isfile(file):
                    os.startfile(folder)
                else:
                    cmd = f'explorer /select, "{file}"'
                    run_command(cmd)
            else:
                # linux
                cmd = f'xdg-open "folder"'
                # os.system(cmd)
                run_command(cmd)
        except Exception as e:
            handle_exceptions(e)

    def refresh_link_btn(self):
        if self.selected_row_num is None:
            return

        d = self.selected_d
        config.download_folder = d.folder

        self.window['url'](d.url)
        self.url_text_change()

        self.window['folder'](config.download_folder)
        self.select_tab('Main')

    # endregion

    # region video

    @property
    def m_bar(self):
        """playlist progress bar"""
        return self._m_bar

    @m_bar.setter
    def m_bar(self, value):
        """playlist progress bar"""
        self._m_bar = value if value <= 100 else 100
        try:
            self.window['m_bar'].UpdateBar(value)
        except:
            pass

    @property
    def s_bar(self):
        """streams progress bar"""
        return self._s_bar

    @s_bar.setter
    def s_bar(self, value):
        """streams progress bar"""
        self._s_bar = value if value <= 100 else 100
        try:
            self.window['s_bar'].UpdateBar(value)
        except:
            pass

    @property
    def pl_menu(self):
        """video playlist menu"""
        return self._pl_menu

    @pl_menu.setter
    def pl_menu(self, rows):
        """video playlist menu"""
        self._pl_menu = rows
        try:
            self.window['pl_menu'](values=rows)
        except:
            pass

    @property
    def stream_menu(self):
        """video streams menu"""
        return self._stream_menu

    @stream_menu.setter
    def stream_menu(self, rows):
        """video streams menu"""
        self._stream_menu = rows
        try:
            self.window['stream_menu'](values=rows)
        except:
            pass

    def reset_video_controls(self):
        try:
            self.reset_progress_bar()
            self.pl_menu = ['Playlist']
            self.stream_menu = ['Video quality']
        except:
            pass

    def reset_progress_bar(self):
        self.m_bar = 0
        self.s_bar = 0

    def youtube_func(self):
        """fetch metadata from youtube and other stream websites"""

        # getting videos from youtube is time consuming, if another thread starts, it should cancel the previous one
        # create unique identification for this thread
        self.yt_id += 1 if self.yt_id < 1000 else 0
        yt_id = self.yt_id
        url = self.d.url

        msg = f'looking for video streams ... Please wait'
        log(msg)
        log('youtube_func()> processing:', self.d.url)

        # reset video controls
        self.reset_video_controls()
        self.change_cursor('busy')

        # main progress bar
        self.m_bar = 10

        # reset playlist
        self.playlist = []

        # quit if main window terminated
        if config.terminate: return

        try:
            # we import youtube-dl in separate thread to minimize startup time, will wait in loop until it gets imported
            if video.ytdl is None:
                log('youtube-dl module still not loaded completely, please wait')
                while not video.ytdl:
                    time.sleep(0.1)  # wait until module gets imported

            # youtube-dl process
            log(get_ytdl_options())
            with video.ytdl.YoutubeDL(get_ytdl_options()) as ydl:
                info = ydl.extract_info(self.d.url, download=False, process=True)
                log('Media info:', info, log_level=3)

                # set playlist / video title
                self.pl_title = info.get('title', '')

                # main progress bar
                self.m_bar = 30
                # check results if it's a playlist
                if info.get('_type') == 'playlist' or 'entries' in info:
                    pl_info = list(info.get('entries'))

                    self.d.playlist_url = self.d.url

                    # progress bars
                    self.m_bar = 50  # decide increment value in side bar based on number of threads

                    self.window['s_bar'].update_bar(0, max=len(pl_info))  # change maximum value
                    s_bar_incr = 1  # 100 / len(pl_info) # 100 // len(pl_info) + 1 #

                    self.playlist = [None for _ in range(len(pl_info))]  # fill list so we can store videos in order
                    v_threads = []
                    for num, item in enumerate(pl_info):
                        video_url = item.get('url', None) or item.get('webpage_url', None) or item.get('id', None)
                        t = Thread(target=self.get_video, daemon=True, args=[num, video_url, yt_id, s_bar_incr])
                        v_threads.append(t)
                        t.start()

                    for t in v_threads:
                        t.join()

                    # clean playlist in case a slot left with 'None' value
                    self.playlist = [v for v in self.playlist if v]

                else:  # in case of single video
                    self.playlist = [Video(self.d.url, vid_info=info)]
                    self.s_bar = 100

            # quit if main window terminated
            if config.terminate: return

            # quit if we couldn't extract any videos info (playlist or single video)
            if not self.playlist:
                self.reset_video_controls()
                self.disable()
                self.set_status('')
                self.change_cursor('default')
                self.reset()
                log('youtube func: quitting, can not extract videos')
                return

            # quit if url changed by user
            if url != self.d.url:
                self.reset_video_controls()
                self.change_cursor('default')
                log('youtube func: quitting, url changed by user')
                return

            # quit if new youtube func thread started
            if yt_id != self.yt_id:
                log('youtube func: quitting, new instance has started')
                return

            # update playlist menu
            self.update_pl_menu()
            self.update_stream_menu()  # uses the current self.video
            self.update_video_param()  # take stream number as an argument, default 0

            # self.enable_video_controls()
            self.enable()

            self.m_bar = 100

        except Exception as e:
            log('youtube_func()> error:', e)
            self.reset_video_controls()

        finally:
            self.change_cursor('default')

    def get_video(self, num, vid_url, yt_id, s_bar_incr):
        log('Main_window.get_video()> url:', vid_url)
        if not vid_url:
            return None
        try:
            video = Video(vid_url)

            # make sure no other youtube func thread started
            if yt_id != self.yt_id:
                log('get_video:> operation cancelled')
                return

            self.playlist[num] = video

        except Exception as e:
            log('MainWindow.get_video:> ', e)
        finally:
            with self.s_bar_lock:
                self.s_bar += s_bar_incr

    def update_pl_menu(self):
        try:
            # set playlist label
            self.set_status(f'{len(self.playlist)} videos in Playlist: {self.pl_title}')

            # update playlist menu items
            self.pl_menu = [str(i + 1) + '- ' + video.title for i, video in enumerate(self.playlist)]

            # choose current item
            self.video = self.playlist[0]
        except:
            pass

    def update_video_param(self):
        self.d = self.video

    def update_stream_menu(self):
        try:
            self.stream_menu = self.video.stream_menu

            # select first stream
            selected_text = self.video.stream_names[0]
            self.window['stream_menu'](selected_text)
            self.stream_OnChoice(selected_text)
        except:
            pass

    def playlist_OnChoice(self, selected_text):
        if selected_text not in self.pl_menu:
            return

        index = self.pl_menu.index(selected_text)
        self.video = self.playlist[index]

        self.update_stream_menu()
        self.update_video_param()

    def stream_OnChoice(self, selected_text):
        if selected_text not in self.stream_menu:
            return
        if selected_text not in self.video.stream_names:
            selected_text = self.stream_menu_selection or self.video.stream_names[0]
            self.window['stream_menu'](selected_text)

        self.stream_menu_selection = selected_text
        self.video.selected_stream = self.video.streams[selected_text]
        self.update_video_param()

    def download_playlist(self):

        # check if there is a video file or quit
        if not self.video:
            sg.popup_ok('Playlist is empty, nothing to download :)', title='Playlist download')
            return

        # prepare a list for master stream menu
        mp4_videos = {}
        other_videos = {}
        audio_streams = {}

        # will use raw stream names which doesn't include size
        for video in self.playlist:
            mp4_videos.update({stream.raw_name: stream for stream in video.mp4_videos.values()})
            other_videos.update({stream.raw_name: stream for stream in video.other_videos.values()})
            audio_streams.update({stream.raw_name: stream for stream in video.audio_streams.values()})

        # sort streams based on quality
        mp4_videos = {k: v for k, v in sorted(mp4_videos.items(), key=lambda item: item[1].quality, reverse=True)}
        other_videos = {k: v for k, v in sorted(other_videos.items(), key=lambda item: item[1].quality, reverse=True)}
        audio_streams = {k: v for k, v in sorted(audio_streams.items(), key=lambda item: item[1].quality, reverse=True)}

        raw_streams = {**mp4_videos, **other_videos, **audio_streams}
        master_stream_menu = ['● Video streams:                     '] + list(mp4_videos.keys()) + list(
            other_videos.keys()) + \
                             ['', '● Audio streams:                 '] + list(audio_streams.keys())
        master_stream_combo_selection = ''

        video_checkboxes = []
        stream_combos = []

        general_options_layout = [sg.Checkbox('Select All', enable_events=True, key='Select All'),
                                  sg.T('', size=(15, 1)),
                                  sg.T('Choose quality for all videos:'),
                                  sg.Combo(values=master_stream_menu, default_value=master_stream_menu[0], size=(28, 1),
                                           key='master_stream_combo', enable_events=True)]

        video_layout = []

        for num, video in enumerate(self.playlist):
            # set selected stream
            video.selected_stream = video.stream_list[0]

            video_checkbox = sg.Checkbox(truncate(video.title, 40), size=(40, 1), tooltip=video.title,
                                         key=f'video {num}')
            video_checkboxes.append(video_checkbox)

            stream_combo = sg.Combo(values=video.raw_stream_menu, default_value=video.raw_stream_menu[1], font='any 8',
                                    size=(26, 1), key=f'stream {num}', enable_events=True)
            stream_combos.append(stream_combo)

            row = [video_checkbox, stream_combo,
                   sg.T(size_format(video.total_size), size=(10, 1), font='any 8', key=f'size_text {num}')]
            video_layout.append(row)

        video_layout = [sg.Column(video_layout, scrollable=True, vertical_scroll_only=True, size=(650, 250), key='col')]

        layout = [[sg.T(f'Total Videos: {len(self.playlist)}')]]
        layout.append(general_options_layout)
        layout.append([sg.T('')])
        layout.append([sg.Frame(title='select videos to download:', layout=[video_layout])])
        layout.append([sg.Col([[sg.OK(), sg.Cancel()]], justification='right')])

        window = sg.Window(title='Playlist download window', layout=layout, finalize=True, margins=(2, 2))

        chosen_videos = []

        while True:
            event, values = window()
            if event in (None, 'Cancel'):
                window.close()
                return

            if event == 'OK':
                chosen_videos.clear()
                for num, video in enumerate(self.playlist):
                    selected_text = values[f'stream {num}']
                    video.selected_stream = video.raw_streams[selected_text]

                    if values[f'video {num}'] is True:
                        chosen_videos.append(video)
                        # print('video.selected_stream:', video.selected_stream)

                window.close()
                break

            elif event == 'Select All':
                checked = window['Select All'].get()
                for checkbox in video_checkboxes:
                    checkbox(checked)

            elif event == 'master_stream_combo':
                selected_text = values['master_stream_combo']
                if selected_text in raw_streams:
                    # update all videos stream menus from master stream menu
                    for num, stream_combo in enumerate(stream_combos):
                        video = self.playlist[num]

                        if selected_text in video.raw_streams:
                            stream_combo(selected_text)
                            video.selected_stream = video.raw_streams[selected_text]
                            window[f'size_text {num}'](size_format(video.size))

            elif event.startswith('stream'):
                num = int(event.split()[-1])

                video = self.playlist[num]
                selected_text = window[event].get()
                # print(f'"{selected_text}", {video.raw_streams}')
                if selected_text in video.raw_streams:
                    video.selected_stream = video.raw_streams[selected_text]

                else:
                    window[event](video.selected_stream.raw_name)

                window[f'size_text {num}'](size_format(video.size))
                # log('download playlist fn>', 'stream', repr(video.selected_stream))

        self.select_tab('Downloads')

        for video in chosen_videos:
            # resume_support = True if video.size else False

            log(f'download playlist fn> {repr(video.selected_stream)}, title: {video.name}')

            video.folder = config.download_folder

            self.start_download(video, silent=True)

    def ffmpeg_check(self):
        if not check_ffmpeg():
            if config.operating_system == 'Windows':
                layout = [[sg.T('"ffmpeg" is missing!! and need to be downloaded:\n')],
                          [sg.T('destination:')],
                          [sg.Radio(f'recommended: {config.sett_folder}', group_id=0, key='radio1', default=True)],
                          [sg.Radio(f'Local folder: {config.current_directory}', group_id=0, key='radio2')],
                          [sg.B('Download'), sg.Cancel()]]

                window = sg.Window('ffmpeg is missing', layout)

                event, values = window()
                window.close()
                selected_folder = config.sett_folder if values['radio1'] else config.current_directory
                if event == 'Download':
                    download_ffmpeg(destination=selected_folder)
            else:
                sg.popup_error(
                    '"ffmpeg" is required to merge an audio stream with your video',
                    'executable must be copied into pyIDM folder or add ffmpeg path to system PATH',
                    '',
                    'you can download it manually from https://www.ffmpeg.org/download.html',
                    title='ffmpeg is missing')

            return False
        else:
            return True

    # endregion

    # region General
    def url_text_change(self):
        url = self.window.Element('url').Get().strip()
        if url == self.d.url:
            return

        # Focus and select main app page in case text changed from script
        self.window.BringToFront()
        self.select_tab('Main')

        self.reset()
        try:
            self.d.eff_url = self.d.url = url

            # schedule refresh header func
            if isinstance(self.url_timer, Timer):
                self.url_timer.cancel()  # cancel previous timer

            self.url_timer = Timer(0.5, self.refresh_headers, args=[url])
            self.url_timer.start()  # start new timer

        except:
            pass

    def retry(self):
        self.d.url = ''
        self.url_text_change()

    def reset(self):
        # reset some values
        # self.d.reset()
        self.d = DownloadItem()
        self.set_status('')
        self.playlist = []
        self.video = None

        # widgets
        self.disable()
        self.reset_video_controls()

    def change_cursor(self, cursor='default'):
        # todo: check if we can set cursor  for window not individual tabs
        if cursor == 'busy':
            cursor_name = 'watch'
        else:  # default
            cursor_name = 'arrow'

        self.window['Main'].set_cursor(cursor_name)
        self.window['Setting'].set_cursor(cursor_name)

    def main_frameOnClose(self):
        # config.terminate = True

        log('main frame closing')
        self.window.Close()

        # Terminate all downloads before quitting if any is a live
        try:
            for i in self.active_downloads:
                d = self.d_list[i]
                d.status = Status.cancelled
        except:
            pass

        # config.clipboard_q.put(('status', Status.cancelled))

    def check_scheduled(self):
        t = time.localtime()
        c_t = (t.tm_hour, t.tm_min)
        for d in self.d_list:
            if d.sched and d.sched[0] <= c_t[0] and d.sched[1] <= c_t[1]:
                self.start_download(d)  # send for download
                d.sched = None  # cancel schedule time

    def ask_for_sched_time(self, msg=''):
        """Show a gui dialog to ask user for schedule time for download items, it take one or more of download items"""
        response = None

        layout = [
            [sg.T('schedule download item:')],
            [sg.T(msg)],
            [sg.Combo(values=list(range(1, 13)), default_value=1, size=(5, 1), key='hours'), sg.T('H  '),
             sg.Combo(values=list(range(0, 60)), default_value=0, size=(5, 1), key='minutes'), sg.T('m  '),
             sg.Combo(values=['AM', 'PM'], default_value='AM', size=(5, 1), key='am pm')],
            [sg.Ok(), sg.Cancel()]
        ]

        window = sg.Window('Scheduling download item', layout, finalize=True)

        e, v = window()

        if e == 'Ok':
            h = int(v['hours'])
            if v['am pm'] == 'AM' and h == 12:
                h = 0
            elif v['am pm'] == 'PM' and h != 12:
                h += 12

            m = int(v['minutes'])

            # # assign to download item
            # d.sched = (h, m)

            response = h, m

        window.close()
        return response

    # endregion

    # region update
    def check_for_update(self):
        self.change_cursor('busy')

        # check for update
        current_version = config.APP_VERSION
        info = update.get_changelog()

        if info:
            latest_version, version_description = info

            # compare with current application version
            newer_version = compare_versions(current_version, latest_version)  # return None if both equal
            # print(newer_version, current_version, latest_version)

            if not newer_version or newer_version == current_version:
                self.new_version_available = False
                log("check_for_update() --> App. is up-to-date, server version=", latest_version)
            else:  # newer_version == latest_version
                self.new_version_available = True

            # updaet global values
            config.APP_LATEST_VERSION = latest_version
            self.new_version_description = version_description
        else:
            self.new_version_description = None
            self.new_version_available = False

        self.change_cursor('default')

    def update_app(self, remote=True):
        """show changelog with latest version and ask user for update
        :param remote: bool, check remote server for update"""
        if remote:
            self.check_for_update()

        if self.new_version_available:
            config.main_window_q.put(('show_update_gui', ''))
            # self.show_update_gui()
        else:
            popup(f"      App. is up-to-date \n\n"
                  f"Current version: {config.APP_VERSION} \n"
                  f"Server version:  {config.APP_LATEST_VERSION} \n",
                  title='App update',
                  type_='popup_no_buttons'
                  )
            if self.new_version_description:
                pass
            else:
                popup("couldn't check for update")

    def show_update_gui(self):
        layout = [
            [sg.T('New version available:')],
            [sg.Multiline(self.new_version_description, size=(50, 10))],
            [sg.B('Update'), sg.Cancel()]
        ]
        window = sg.Window('Update Application', layout, finalize=True, keep_on_top=True)
        event, _ = window()
        if event == 'Update':
            update.update()

        window.close()

    def animate_update_note(self):
        # display word by word
        # values = 'new version available, click me for more info !'.split()
        # values = [' '.join(values[:i + 1]) for i in range(len(values))]

        # display character by character
        # values = [c for c in 'new version available, click me for more info !']
        # values = [''.join(values[:i + 1]) for i in range(len(values))]

        # normal on off display
        values = ['', 'new version available, click me for more info !']
        note = self.window['update_note']

        # add animation text property to note object
        if not hasattr(note, 'animation_index'):
            note.animation_index = 0

        if note.animation_index < len(values) - 1:
            note.animation_index += 1
        else:
            note.animation_index = 0

        new_text = values[note.animation_index]
        note(new_text)

    def check_for_ytdl_update(self):
        config.ytdl_LATEST_VERSION = update.check_for_ytdl_update()

    def update_ytdl(self):
        current_version = config.ytdl_VERSION
        latest_version = config.ytdl_LATEST_VERSION or update.check_for_ytdl_update()
        if latest_version:
            config.ytdl_LATEST_VERSION = latest_version
            log('youtube-dl update, latest version = ', latest_version, ' - current version = ', current_version)

            if latest_version != current_version:
                # select log tab
                self.select_tab('Log')

                response = sg.popup_ok_cancel(
                    f'Found new version of youtube-dl on github {latest_version}\n'
                    f'current version =  {current_version} \n'
                    'Install new version?',
                    title='youtube-dl module update')

                if response == 'OK':
                    try:
                        Thread(target=update.update_youtube_dl).start()
                    except Exception as e:
                        log('failed to update youtube-dl module:', e)
            else:
                sg.popup_ok(f'youtube_dl is up-to-date, current version = {current_version}')
    # endregion


class DownloadWindow:

    def __init__(self, d=None):
        self.d = d
        self.q = d.q
        self.window = None
        self.event = None
        self.values = None
        self.timeout = 10
        self.timer = 0
        self._progress_mode = 'determinate'

        self.create_window()

    @property
    def progress_mode(self):
        return self._progress_mode

    @progress_mode.setter
    def progress_mode(self, mode):
        """change progressbar mode (determinate / undeterminate)"""
        if self._progress_mode != mode:
            try:
                self.window['progress_bar'].Widget.config(mode=mode)
                self._progress_mode = mode
            except:
                pass

    def create_window(self):
        layout = [
            [sg.T('', size=(55, 4), key='out')],

            [sg.T(' ' * 120, key='percent')],

            [sg.ProgressBar(max_value=100, key='progress_bar', size=(42, 15), border_width=3)],

            # [sg.Column([[sg.Button('Hide', key='hide'), sg.Button('Cancel', key='cancel')]], justification='right')],
            [sg.T(' ', key='status', size=(35, 1)), sg.Button('Hide', key='hide'), sg.Button('Cancel', key='cancel')],
            [sg.T('', size=(100, 2), font='any 8', key='log2')],
        ]

        self.window = sg.Window(title=self.d.name, layout=layout, finalize=True, margins=(2, 2), size=(460, 220))
        self.window['progress_bar'].expand()
        self.window['percent'].expand()

        # log text, disable word wrap
        # self.window['log2'].Widget.config(wrap='none')

    def update_gui(self):
        # trim name and folder length
        name = truncate(self.d.name, 50)
        # folder = truncate(self.d.folder, 50)

        out = f"File: {name}\n" \
              f"downloaded: {size_format(self.d.downloaded)} out of {size_format(self.d.total_size)}\n" \
              f"speed: {size_format(self.d.speed, '/s')}  {time_format(self.d.time_left)} left \n" \
              f"live connections: {self.d.live_connections} - remaining parts: {self.d.remaining_parts}\n" \
 \
        try:
            self.window.Element('out').Update(value=out)

            # progress bar mode depend on available downloaditem progress property
            if self.d.progress:
                self.progress_mode = 'determinate'
                self.window['progress_bar'].update_bar(self.d.progress)
            else:  # size is zero, will make random animation
                self.progress_mode = 'indeterminate'
                self.window['progress_bar'].Widget['value'] += 5

            if self.d.status in (Status.completed, Status.cancelled, Status.error):
                self.close()

            # log
            self.window['log2']('Activity:\n' + config.log_entry)

            # percentage value to move with progress bar
            position = int(self.d.progress) - 5 if self.d.progress > 5 else 0
            self.window['percent'](f"{' ' * position} {self.d.progress}%")

            # status update
            self.window['status'](
                f"{self.d.status}  {self.d.i} {'Please wait' if self.d.status == Status.merging_audio else ''}")
        except:
            pass

    def run(self):
        self.event, self.values = self.window.Read(timeout=self.timeout)
        if self.event in ('cancel', None):
            if self.d.status != Status.error:
                self.d.status = Status.cancelled
            self.close()

        elif self.event == 'hide':
            self.close()

        # update gui
        if time.time() - self.timer >= 0.5:
            self.timer = time.time()
            self.update_gui()

    def focus(self):
        self.window.BringToFront()

    def close(self):
        self.event = None
        self.window.Close()
