#!/usr/bin/env python

import provenance as p
from joblib.disk import mkdirp

mkdirp('./remote-machine/sftp-artifacts')

p.load_config({'blobstores':
               {'sftp': {'type': 'sftp',
                         'cachedir': '<This is the path on your local machine where you want the blobs to be cached, ex. /Users/me/provenance/examples/sftp/artifacts>',
                         'basepath': '<            ""           remote machine                ""                       , ex. /home/me/artifacts>, you need to make sure that path directory exists.',
                         'read': True,
                         'write': True,
                         'read_through_write': False,
                         'delete': True,
                         'ssh_config': {'hostname': '<your host here>',
                                        'port': '<your port here as an int, defaults to 22 if excluded>',
                                        'username': '<your user here>',
                                        'password': '<your password here>'}}},
               'artifact_repos':
               {'local': {'type': 'postgres',
                          'db': 'postgresql://localhost/provenance-sftp-example',
                          'store': 'sftp',
                          'read': True,
                          'write': True,
                          'create_db': True,
                          'read_through_write': False,
                          'delete': True}},
               'default_repo': 'local'})

@p.provenance()
def my_add(x, y):
    print("Executed")
    return x + y

print(my_add(1, 4))
