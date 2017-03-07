#!/usr/bin/env python

import provenance as p

p.load_config({'blobstores':
               {'disk': {'type': 'disk',
                         'cachedir': 'artifacts',
                         'read': True,
                         'write': True,
                         'read_through_write': False,
                         'delete': True}},
               'artifact_repos':
               {'local': {'type': 'postgres',
                          'db': 'postgresql://localhost/provenance-basic-example',
                          'store': 'disk',
                          'read': True,
                          'write': True,
                          'read_through_write': False,
                          'delete': True}},
               'default_repo': 'local'})

@p.provenance()
def my_add(x, y):
    print("Executed")
    return x + y

print(my_add(1, 4))
