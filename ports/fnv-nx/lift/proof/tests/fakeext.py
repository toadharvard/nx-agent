"""Test helper: local bare git repos as fake externals (a stand-in for fnv-nx-build, selected by ARTIFACTS_URL, no network) and the
trunk's verify/ of this tree."""
import os
import subprocess
import sys

VERIFY = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'verify'))    # ports/fnv-nx/verify of this tree
sys.path.insert(0, os.path.join(VERIFY, '..', 'contract'))
sys.path.insert(0, VERIFY)
import cas       # noqa: E402
import verdict   # noqa: E402


def git(d, *a):
    return subprocess.run(['git', '-C', d] + list(a), check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')).stdout


class Artifacts:
    """a bare repo BASE/artifacts.git; publish() makes one branch per external"""
    def __init__(self, base):
        self.base = base
        self.url = os.path.join(base, 'artifacts.git')
        subprocess.run(['git', 'init', '-q', '--bare', self.url], check=True)

    def publish(self, name, files, at=None):
        """files {path: text} -> the external entry {name, repo, ref, commit, tree_sha256, at} naming the one commit of branch NAME"""
        d = os.path.join(self.base, 'pub-' + name)
        os.makedirs(d)
        for p, t in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
            with open(os.path.join(d, p), 'w') as fh:
                fh.write(t)
        git(d, 'init', '-q')
        git(d, 'add', '-A')
        git(d, 'commit', '-q', '-m', name)
        git(d, 'push', '-q', self.url, 'HEAD:refs/heads/' + name)
        subprocess.run(['git', 'clone', '-q', self.url, d + '-co', '-b', name], check=True, capture_output=True)
        tree = cas.ingest(d + '-co', os.path.join(self.base, 'hs-' + name), ['.git'])
        return {'name': name, 'repo': 'fnv-nx-build', 'ref': name, 'commit': git(d, 'rev-parse', 'HEAD').strip(), 'tree_sha256': tree,
                'at': at or '/ext/' + name}


class use_artifacts:
    """sets ARTIFACTS_URL (read by recipe.fetch_externals) for the block"""
    def __init__(self, art):
        self.art = art

    def __enter__(self):
        self.old = os.environ.get('ARTIFACTS_URL')
        os.environ['ARTIFACTS_URL'] = self.art.url

    def __exit__(self, *exc):
        if self.old is None:
            del os.environ['ARTIFACTS_URL']
        else:
            os.environ['ARTIFACTS_URL'] = self.old


def accepted(gate, out_dir, rc):
    """the trunk's verdict reader (verdict._gate_result) on the result file of GATE: (status, universe, certified, reason)"""
    return verdict._gate_result({'name': gate, 'result': gate + '.json'}, out_dir, rc)
