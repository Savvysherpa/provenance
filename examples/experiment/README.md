# provenance-experiment-example

## Step 0: Understand sftp-example

## Step 1: Run some stuff

First you run `conda env create`, `source activate
provenance-experiment-example`. Now open up `config.yaml`. In the config fill in
the `cachedir`, `basepath`, and `ssh_config` for the sftp blobstore. You'll find
some directions acting as placeholders or you can see [here](#the-config). Now
you can run `./experiment_example.py`.

Then you can explore how the artifacts and blobs were saved in your specified
`cachedir`,`basepath`, and in `psql provenance-experiment-example`.

## Step 2: Learn some stuff

### The gist
Here we learn about archiving files, provenance\_sets and chaining blobstores.
For chaining blobstores see [here](#the-config). Archiving files is really
straight forward if you've understood the previous examples, you can see it in
action [here](sftp\_example.py#62). Two blobs are created for each file, one is
the actual file and the other is the inputs to the call to archive_file. See the
comments in [sftp\_example.py](sftp\_example.py) for additional detail.

At the top of the function that contains the calls to `archive_file`
(see [here](sftp\_example.py#54)) you'll see we have `p.provenance_set` instead
of the `p.provenance` that we've seen before. A provenance_set is simply a named
set containing the id's of the artifacts in the set. In this example each entry
(demographic.json and matrix.csv) are put in a set named after the entry id
(0000 or 0001 etc.). Details on how to get the set back and the artifacts out
have not yet been written but are coming soon.

### The Config
We changed the config to be a yaml file and loaded it in. You'll notice that we
define two blobstores (the same two from basic-example and sftp-example). Then
there is a third. This third, called `experiment` is a chained blobstore. It
chains `disk` to `sftp`. Remember in `sftp-example` a local blobstore was
created but Provenance didn't know that it could look there when asked to
retrieve an artifact. By chaining we say, first look/write in `disk`, then to
`sftp`. Here's where `read_through_write` comes into play. We've set it to
`True`. This means that if Provenance is trying to look up an artifact and it
doesn't find it in `disk` but it does find it in `sftp` it will write it in
`disk` "on the way back". Notice that we set the store for the artifact_repo to
`experiment`.
