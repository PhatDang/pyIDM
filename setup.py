"""
    pyIDM

    multi-connections internet download manager, based on "pyCuRL/curl", "youtube_dl", and "PySimpleGUI"

    :copyright: (c) 2019-2020 by Mahmoud Elshahat.
    :license: GNU LGPLv3, see LICENSE for more details.
"""
import setuptools, os, sys

# get version
version = {}
with open("pyidm/version.py") as f:
    exec (f.read(), version)  # later on we use: version['__version__']

# get long description from readme
with open("README.md", "r") as fh:
    long_description = fh.read()

# get requirements
with open("requirements.txt", "r") as fh:
    requirements = fh.readlines()

setuptools.setup(
    name="pyIDM",
    version=version['__version__'],
    scripts=[],  # ['pyIDM.py'], no need since added an entry_points
    author="Mahmoud Elshahat",
    author_email="mahmoud_elshahhat@yahoo.com",
    description="download manager",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/pyIDM/pyIDM ",
    packages=setuptools.find_packages(),
    keywords="internet download manager youtube downloader pycurl youtube-dl PySimpleGUI",
    project_urls={
        'Source': 'https://github.com/pyIDM/pyIDM',
        'Tracker': 'https://github.com/pyIDM/pyIDM/issues',
        'Releases': 'https://github.com/pyIDM/pyIDM/releases',
        'Screenshots': 'https://github.com/pyIDM/pyIDM/issues/13'
    },
    install_requires=requirements,  # ['PySimpleGUI', 'pyperclip', 'plyer', 'certifi', 'youtube_dl', 'pycurl'],
    # py_modules=[],
    # data_files=[(os.path.join(sys.prefix, 'Lib/site-packages/pyidm'),['requirements.txt'])],
    # package_data={
    #     # can include only files inside the package:
    #     "": ["*.txt"]
    # },
    # include_package_data=True,  # if plan to use MANIFEST.in
    entry_points={
        # our executable "exe file on windows for example"
        'console_scripts': [
            'pyidm = pyidm.pyIDM:main',
        ]},
    classifiers=[
        "Programming Language :: Python :: 3",
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
