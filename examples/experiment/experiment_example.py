#!/usr/bin/env python

import csv
import json
import os
import provenance as p
import random
import shutil


p.load_yaml_config('config.yaml')

# Suppose you are conducting an experiment to determine the correlation between
# geographic location and favorite 3x3 matrix. To do this you have them sit at a
# computer and enter in the information. To store the data you create a
# directory structure such that each entry gets its own numbered directory in
# which there are two files, info.json which has the demographic info and
# data.csv which contains their favorite 3x3 matrix. Now, is this the best way
# to store the data for this experiment? No. But we'll ignore that for the sake
# of instruction.

################################################################################
## Generate random data, you wouldn't actually have this code in your experiment.

first_names = ['Eric', 'Belinda', 'Jane', 'Scott', 'Joe', 'Mike', 'Wilhelmina']
last_names = ['Thompson', 'Erikson', 'Gandalfo', 'Wesson', 'Black', 'Stephens']
def gen_name():
    return random.choice(first_names) + ' ' + random.choice(last_names)

def gen_age():
    return random.randint(18, 100)

street_names = ['Maple St', 'Corner Ave', 'West Helm Lp', '4th St', 'Main St', 'Center St']
def gen_address():
    return str(random.randint(1000, 10000)) + ' ' + random.choice(street_names)

def gen_matrix():
    return [[random.randint(0, 100) for x in range(3)] for y in range(3)]


################################################################################
## Here's the crux. You WOULD have this code in your experiment. This function
## actually writes the data files that you want to keep track of and share with
## others. Here we introduce the provenance_set, which is basically a named set
## of artifacts. It makes sense if each entry (which includes two files) becomes
## a set. We can name the set, then use that name to retreive the latest
## version.

def save_entry(id, name, age, address, matrix):
    directory = os.path.join('./experiment_data', id)
    os.mkdir(directory)
    demographics = {'name': name, 'age': age, 'address': address}

    @p.provenance_set(set_name=id)
    def write_entry():
        with open(os.path.join(directory, 'demographic.json'), 'w') as demof:
            json.dump(demographics, demof)

        with open(os.path.join(directory, 'matrix.csv'), 'w') as matrixf:
            writer = csv.writer(matrixf)
            writer.writerows(matrix)
        p.archive_file(os.path.join(directory, 'demographic.json'), name=id+'/demographic', delete_original=True)
        p.archive_file(os.path.join(directory, 'matrix.csv'), name=id+'/matrix', delete_original=True)

    write_entry()

################################################################################
## Simulate some number of participants, you wouldn't actually have this code in
## your experiment.

def simulate_entry(id):
    name = gen_name()
    age = gen_age()
    address = gen_address()
    matrix = gen_matrix()
    save_entry(id, name, age, address, matrix)

def simulate_experiment(num_participants):
    # I use the experiment_data as a temporary location to write the data to.
    # Provenance will store the files in the blobstore so...
    if not os.path.exists('./experiment_data'):
        os.mkdir('./experiment_data')

    for i in range(num_participants):
        simulate_entry(str(i).zfill(4))

    # ... then I erase the folder at the end.
    shutil.rmtree('./experiment_data')

simulate_experiment(10)
