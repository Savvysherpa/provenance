# provenance-sftp-example

## Step 0: Understand the basic example

## Step 1: Run some stuff

First you run `conda env create`, `source activate provenance-sftp-example`, and
Now open up `sftp_example.py`. In the config fill in the `cachedir`, `basepath`,
and `ssh_config` for the sftp blobstore. You'll find some directions acting as
placeholders or you can see [here](#the-config). Now you can run
`./sftp_example.py`.

Then you can explore how the artifacts and blobs were saved in your specified
`cachedir`,`basepath`, and in `psql provenance-sftp-example`.

## Step 2: Learn some stuff

### The gist
This is pretty much the same as the basic example, only the blobstore is on a
remote machine. The artifacts in the postgres db are referring to the blobs in
the remote blobstore.

### The Config
`cachedir` here has the same meaning as it did in the basic example. This leads
to the question "do I then have an implicit local blobstore?". Yes and no, yes
because all the blobs will be in the `cachedir`. No because Provenance will not
look for them there but will go immediately to the remote host and look in
`basepath`. (You could use the local blobstore via a chained blobstore which is
not covered in this example. Also this is a feature that should probably be
added so it's automatic.) `basepath` is the path to the blobstore on the remote
machine. While `cachedir` will be created for you if it doesn't exist,
`basepath` won't be so make sure you create it. `ssh_config` is relatively
straight forward as it is the standard things you need to ssh onto a machine.
