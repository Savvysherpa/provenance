import os
import paramiko
import shutil
from joblib.disk import mkdirp
from ..serializers import DEFAULT_VALUE_SERIALIZER, DEFAULT_INPUT_SERIALIZER
from .. import _commonstore as cs
from .. import blobstores as bs


def _ssh_client(ssh_config):
    client = paramiko.SSHClient()
    client.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
    # There still seems to be problems with some types keys.
    # See https://github.com/paramiko/paramiko/issues/243
    # So you might try uncommenting if you are using an ecdsa-sha2-nistp256
    # client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(**ssh_config)
    return client


class SFTPStore(bs.BaseBlobStore):
    def __init__(self, cachedir, basepath, ssh_config=None, ssh_client=None,
                 sftp_client=None, read=True, write=True,
                 read_through_write=True, delete=False,
                 on_duplicate_key='skip', cleanup_cachedir=False):
        super(SFTPStore, self).__init__(
            read=read, write=write, read_through_write=read_through_write,
            delete=delete, on_duplicate_key=on_duplicate_key)

        if ssh_config is not None:
            self.ssh_client = _ssh_client(ssh_config)
        if self.ssh_client is not None:
            sftp_client = paramiko.SFTPClient.from_transport(self.ssh_client._transport)
        if sftp_client is not None:
            self.sftp_client = sftp_client
        else:
            raise ValueError('You must specify a SFTP client by passing in one of: sftp_client, ssh_config, ssh_client')

        self.cachedir = bs._abspath(cachedir)
        self.basepath = basepath
        self.cleanup_cachedir = cleanup_cachedir
        mkdirp(self.cachedir)

    def __del__(self):
        if self.cleanup_cachedir:
            shutil.rmtree(self.cachedir)

    def _filename(self, id):
        return os.path.join(self.cachedir, id)

    def _path(self, id):
        return os.path.join(self.basepath, id)

    def __contains__(self, id):
        cs.ensure_contains(self)
        try:
            self.sftp_client.stat(self._path(id))
            return True
        except FileNotFoundError:
            return False

    def _put_overwrite(self, id, value, serializer, read_through):
        cs.ensure_put(self, id, read_through, check_contains=False)
        filename = self._filename(id)
        # not already saved by DiskStore?
        if not os.path.isfile(filename):
            with bs._atomic_write(filename) as temp:
                serializer.dump(value, temp)
        self.sftp_client.put(filename, self._path(id))

    def get(self, id, serializer=DEFAULT_VALUE_SERIALIZER, **_kargs):
        cs.ensure_read(self)
        cs.ensure_present(self, id)
        filename = self._filename(id)
        if not os.path.exists(filename):
            with bs._atomic_write(filename) as temp:
                self.sftp_client.get(self._path(id), temp)
        return serializer.load(filename)

    def delete(self, id):
        cs.ensure_delete(self, id)
        self.sftp_client.remove(self._path(id))
