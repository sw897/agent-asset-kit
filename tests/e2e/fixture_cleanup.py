"""Clean up synthetic Git repositories, including read-only Windows objects."""

import os
from pathlib import Path
import shutil
import stat
import tempfile


def cleanup_workdir(work, prefix):
    target = work.resolve()
    if target.parent != Path(tempfile.gettempdir()).resolve() or not target.name.startswith(prefix):
        raise RuntimeError('refuse cleanup outside the synthetic workflow directory')

    def remove_readonly(function, path, exc_info):
        item = Path(path)
        error = exc_info[1]
        info = item.lstat()
        attributes = getattr(info, 'st_file_attributes', 0)
        if (os.name != 'nt' or function not in (os.unlink, os.remove)
                or not isinstance(error, PermissionError) or not stat.S_ISREG(info.st_mode)
                or attributes & 0x400 or not attributes & 0x1
                or not item.resolve().is_relative_to(target)):
            raise error
        item.chmod(info.st_mode | stat.S_IWRITE)
        function(path)

    shutil.rmtree(target, onerror=remove_readonly)
