"""SD tree operations through the Worker (feature file_tree, ops 36-39).

Each step is verified with a listing; deleted files are copied to the host journal first."""
import json
import time

from . import paths
from .common import OpError
from .mtp import display, split_path

FILE_TREE = 256
ENOENT = 2


class WorkerFiles:
    def __init__(self, daemon):
        self.d = daemon

    def entry(self, parts):
        if not parts:
            return {'name': '', 'directory': True}

        try:
            entries = self.d.ls(display(parts[:-1]))
        except OpError as error:
            if error.kind == 'device_error' and error.details.get('code') == ENOENT:
                return None

            raise

        return next((e for e in entries if e['name'] == parts[-1]), None)

    def step(self, name, op, call, check):
        def verify():
            return {'outcome': 'applied' if check() else 'unknown'}

        self.d.run('worker', name, (op,), call, verify=verify)

        if not check():
            raise OpError('uncertain', f'{name}: console replied but the listing does not confirm it')

    def mv(self, source, dest):
        src, dst = split_path(source), split_path(dest)

        if not src or not dst:
            raise OpError('invalid', 'Cannot move the SD root')

        if self.entry(src) is None:
            raise OpError('invalid', f'{source} does not exist')

        target = self.entry(dst)

        if target is not None and target['directory']:
            dst = dst + [src[-1]]
            target = self.entry(dst)

        if target is not None:
            raise OpError('invalid', f'{display(dst)} already exists')

        if dst[:len(src)] == src:
            raise OpError('invalid', 'Cannot move a directory into itself')

        parent = self.entry(dst[:-1])

        if parent is None or not parent['directory']:
            raise OpError('invalid', f'{display(dst[:-1])} is not an existing directory')

        journal = self.journal('mv', src, dest=display(dst))
        self.step('rename', 36, lambda c: c.sidecar.rename(display(src), display(dst)),
                  lambda: self.entry(dst) is not None and self.entry(src) is None)

        return {'source': display(src), 'dest': display(dst), 'journal': str(journal), 'via': 'worker'}

    def rm(self, remote, recursive=False):
        parts = split_path(remote)

        if not parts:
            raise OpError('invalid', 'Refusing to delete the SD root')

        node = self.entry(parts)

        if node is None:
            raise OpError('invalid', f'{remote} does not exist')

        if node['directory'] and self.d.ls(display(parts)) and not recursive:
            raise OpError('invalid', f'{remote} is a non-empty directory; pass recursive')

        journal = self.journal('rm', parts)

        if node['directory']:
            self.remove_tree(parts, journal / 'tree')
        else:
            self.remove_file(parts, journal / 'original')

        return {'remote': display(parts), 'deleted': True, 'directory': node['directory'], 'journal': str(journal),
                'via': 'worker'}

    def remove_file(self, parts, backup):
        self.d.get(display(parts), backup)
        self.step('unlink', 37, lambda c: c.sidecar.unlink(display(parts)), lambda: self.entry(parts) is None)

    def remove_tree(self, parts, backup):
        for child in self.d.ls(display(parts)):
            if child['directory']:
                self.remove_tree(parts + [child['name']], backup / child['name'])
            else:
                self.remove_file(parts + [child['name']], backup / child['name'])

        self.step('rmdir', 38, lambda c: c.sidecar.rmdir(display(parts)), lambda: self.entry(parts) is None)

    def mkdir(self, remote, parents=False):
        parts = split_path(remote)

        if not parts:
            raise OpError('invalid', 'The SD root already exists')

        created = []

        for index in range(1, len(parts) + 1):
            prefix = parts[:index]
            node = self.entry(prefix)

            if node is not None and not node['directory']:
                raise OpError('invalid', f'{display(prefix)} is a file')

            if node is not None:
                continue

            if index < len(parts) and not parents:
                raise OpError('invalid', f'{display(prefix)} does not exist; pass parents')

            self.step('mkdir', 39, lambda c, p=prefix: c.sidecar.mkdir(display(p)),
                      lambda p=prefix: (self.entry(p) or {}).get('directory') is True)
            created.append(display(prefix))

        return {'remote': display(parts), 'created': created, 'via': 'worker'}

    def journal(self, op, parts, **extra):
        run = paths.ensure(paths.journal_dir() / f'worker-change-{time.time_ns()}')
        (run / 'manifest.json').write_text(json.dumps({'op': op, 'target': display(parts), **extra}, indent=2))

        return run
