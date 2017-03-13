# provenance-basic-example
## Step 1: Run some stuff

All you do is `conda env create`, `source activate provenance-basic-example`,
and `./basic_example.py`.

Then you can explore how the artifacts and blobs were saved in `./artifacts` and
in `psql provenance-basic-example`.

## Step 2: Learn some stuff

### The gist
In `basic_example.py` you'll see the decorator `@p.provenance()` above the
function `my_add`. Because of this, Provenance will keep track of inputs and
outputs to this function. Then if you call the function again, it won't compute
the sum, rather it will say "I've already seen these inputs!" and simply look up
the answer based on the inputs. It's safe to say that this is a gross
simplification but it lays the ground work for going forward.

### Terminology
#### Artifact
An artifact is the mechanism by which Provenance stores the inputs and outputs
to our function `my_add`. It actually stores more than that but we'll get there.
An artifact exists as an entry in a database table. It's probably best described
by looking at the columns in the artifact table. There are 21 columns but we'll
start by only looking at 2 of them: `id` and `value_id`. The `id` is just that,
the id of the artifact. But it's actually more then that, it's also a hash of
the inputs (as well as other things like the function name). In the blobstore
(see [below](#blobs-and-blobstore)) there is a blob
(see [below](#blobs-and-blobstore)), the name of that blob is this same as `id`
and the blob contains a pickled version of the inputs. Next is `value_id`, this
is a hash of the output and similarly shares a name with a blob which contains a
pickled version of the output. We won't go over the other columns in the
artifact table now.

#### Blobs and Blobstore
A blob is a Binary Large OBject. Although in this case we don't require them to
be large. A blob is simply a file, what type of file? Doesn't really matter. The
blobstore is simply the place where the blobs are kept. In this example it is
the `artifacts` directory. To be a bit more technical, we can see the blobstore
defined [here](basic_example.py#L5). The `cachedir` part of the blobstore is the
`artifacts` directory but since that's really the heart of the blobstore, we'll
just think of them as synonymous for now until we go into more details about the
config [below](#the-config).

#### Repo (or artifact repo)
A repo is the place where the artifacts are stored. You can see it
defined [here](basic_example.py#L12). In this case it's just a postgres database
as you can see in the `db` part of the repos definition. Again there is more to
a repo but the db is the heart so for now they are synonymous.

### Recap
The first time we run `basic_example.py` we print the result of calling `my_add`
with 1 and 4. We see 5 printed, along with the string 'Executed' that lets us
know that the function was actually executed. The blobstore (artifacts
directory) now contains two blobs (just files). An artifact (entry in a db
table) is created in the repo (postgres db). The artifact has an `id` which is
the hash of the inputs and some other things. One of the blobs (files) has this
as it's name. In that blob (file) is the pickled inputs 1 and 4. The other blob
shares it's name with the `value_id` of the artifact and the blob contains a
pickled 5. Now if we run the same call to my_add with 1 and 4, we won't see
'Executed' printed, but 5 will still be returned. This is evidence that the
function was not ran, rather the answer was looked up. If we call my_add with
different inputs the function is executed and more artifacts and blobs are
created.

### The Config
The config is a map (see [here](basic_example.py#L5)). At the top level we have
`blobstores`, `artifact_repos`, and `default_repo`. We won't get into the reason
for first two are plural here. It will be addressed in a more advanced example.
So for now we have to define a blobstore, and an artifact_repo. (In the case of
the plural artifact\_repos we also have a default_repo, which for us is just our
only repo.) Our blobstore is called 'disk', this name is totally up to you. It
is of type 'disk', meaning on your drive. The possible types are disk, memory,
s3, and chained (chained gets into the plural thing so we'll hold of on that
explaination). The cachedir is defined as discussed earlier. We'll come back to
read, write, read\_through\_right, and delete. We then define Our artifact\_repo
is called 'local', this name is again up to you. It is of type 'postgres'. The
possible types are postgres, memory, and chained. Again our db is defined as
discussed earlier. The read, write, read\_through\_write, and delete fields in
the config of both the blobstore and artifact\_repo are boolean permissions. Am I
allowed to read, write, or delete from this blobstore or artifact\_repo? The
read\_through\_write is concerned with chained blobstores and artifact_repos and
we'll continue to hold off discussing that.
