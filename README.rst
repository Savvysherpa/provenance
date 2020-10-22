==========
provenance
==========

|version status| |conda-version status| |build status| |docs|


.. |version status| image:: https://img.shields.io/pypi/v/provenance.svg
   :target: https://pypi.python.org/pypi/provenance
   :alt: Version Status
.. |conda-version status| image:: https://img.shields.io/conda/vn/conda-forge/provenance
   :target: https://anaconda.org/conda-forge/provenance
   :alt: Conda version Status
.. |build status| image:: https://travis-ci.org/bmabey/provenance.png?branch=trunk
   :target: https://travis-ci.org/bmabey/provenance
   :alt: Build Status
.. |docs| image:: https://readthedocs.org/projects/provenance/badge/?version=latest
   :target: https://provenance.readthedocs.org
   :alt: Documentation Status

``provenance`` is a Python library for function-level caching and provenance that aids in
creating Parsimonious Pythonic |Pipelines|. By wrapping functions in the ``provenance``
decorator computed results are cached across various tiered stores (disk, S3, SFTP) and
`provenance <https://en.wikipedia.org/wiki/Provenance>`_ (i.e. lineage) information is tracked
and stored in an artifact repository. A central artifact repository can be used to enable
production pipelines, team collaboration, and reproducible results. The library is general
purpose but was built with machine learning pipelines in mind. By leveraging the fantastic
`joblib`_ library object serialization is optimized for ``numpy`` and other PyData libraries.

What that means in practice is that you can easily keep track of how artifacts (models,
features, or any object or file) are created, where they are used, and have a central place
to store and share these artifacts. This basic plumbing is required (or at least desired!)
in any machine learning pipeline and project. ``provenance`` can be used standalone along with
a build server to run pipelines or in conjunction with more advanced workflow systems
(e.g. `Airflow`_, `Luigi`_).

.. |Pipelines| unicode:: Pipelines U+2122
.. _joblib: https://pythonhosted.org/joblib/
.. _Airflow: http://airbnb.io/projects/airflow/
.. _Luigi: https://github.com/spotify/luigi

Example
=======

For an explanation of this example please see the `Introductory Guide`_.

.. code-block:: python

    import provenance as p

    p.load_config(...)

    import time

    @p.provenance()
    def expensive_add(a, b):
        time.sleep(2)
        return a + b


    @p.provenance()
    def expensive_mult(a, b):
        time.sleep(2)
        return a * b


    a1 = expensive_add(4, 3)
    a2 = expensive_add(1, 1)

    result = expensive_mult(a1, a2)

    vis.visualize_lineage(result)


.. image:: https://raw.githubusercontent.com/bmabey/provenance/trunk/docs/source/images/lineage_example.png


.. _Introductory Guide: http://provenance.readthedocs.io/en/latest/intro-guide.html

Installation
============

For the base functionality:

.. code:: bash

    pip install provenance


For the visualization module (which requires ``graphviz`` to be installed):

.. code:: bash

    pip install provenance[vis]

For the SFTP store:

.. code:: bash

    pip install provenance[sftp]

For everything all at once:


.. code:: bash

    pip install provenance[all]

provenance is also available from conda-forge for conda installations:

.. code:: bash

    conda install -c conda-forge provenance



Compatibility
=============

``provenance`` is currently only compatible with Python 3.5 and higher. Updating it to work with Python 2.7x
should be easy, follow this `ticket`_ if you are interested in that.


.. _ticket: https://github.com/bmabey/provenance/issues/32
