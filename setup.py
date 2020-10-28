#!/usr/bin/env python

from os.path import exists

from setuptools import setup

import versioneer

subpackages = {
    'sftp': ['paramiko'],
    'google_storage': ['google-cloud'],
    'vis': ['graphviz', 'frozendict'],
}

DESCRIPTION = 'Provenance and caching library for functions, built for creating lightweight machine learning pipelines.'

setup(
    name='provenance',
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    packages=['provenance', 'provenance.sftp', 'provenance.vis'],
    install_requires=[open('requirements.txt').read().strip().split('\n')],
    extras_require=subpackages,
    include_package_data=True,
    description=DESCRIPTION,
    long_description=(open('README.rst').read() if exists('README.rst') else ''),
    author='Ben Mabey',
    author_email='ben@benmabey.com',
    url='http://github.com/bmabey/provenance',
    license='MIT',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: MIT License',
    ],
    python_requires='>=3.5',
)
