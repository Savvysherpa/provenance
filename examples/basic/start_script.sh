#!/usr/bin/env python
import os
import sys
import sqlalchemy as sa
import sqlalchemy_utils.functions as sql_utils
from provenance.models import Base

if not os.path.exists('./artifacts/'):
    os.mkdir('./artifacts/')

db_conn_str = 'postgresql://localhost/provenance-basic-example'

if not sql_utils.database_exists(db_conn_str):
    print("creating database")
    sql_utils.create_database(db_conn_str)

engine = sa.create_engine(db_conn_str)
print("creating tables")
Base.metadata.create_all(engine)
print("complete")
