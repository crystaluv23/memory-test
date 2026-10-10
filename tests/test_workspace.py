import hashlib
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memorylab.common import tree_hash
from memorylab.contracts import ContractError
from memorylab.workspace import (copy_workspace,read_workspace_text,workspace_config,workspace_hash,workspace_snapshot)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.worker=self.root/"worker";self.worker.mkdir()
        self.outside=self.root/"outside-marker";self.outside.write_text("harmless outside boundary marker")
        (self.worker/"engine.py").write_text("ordinary candidate source")
    def tearDown(self):self.tmp.cleanup()
    def denied(self,action):
        with self.assertRaises(ContractError):action()

    def test_normal_files_hash_and_copy_exact_bytes(self):
        nested=self.worker/"nested";nested.mkdir();(nested/"raw.bin").write_bytes(b"\x00\xff")
        expected={"engine.py":b"ordinary candidate source","nested/raw.bin":b"\x00\xff"}
        self.assertEqual(workspace_snapshot(self.worker),expected)
        self.assertEqual(tree_hash(self.worker),{k:hashlib.sha256(v).hexdigest() for k,v in expected.items()})
        destination=self.root/"snapshot";copy_workspace(self.worker,destination)
        self.assertEqual(workspace_snapshot(destination),expected)

    def test_external_dangling_and_internal_file_links_never_read_or_copy(self):
        for target in (self.outside,self.root/"missing",self.worker/"ordinary"):
            (self.worker/"ordinary").write_text("safe internal target")
            link=self.worker/"engine.py";link.unlink();link.symlink_to(target)
            self.denied(lambda:read_workspace_text(self.worker,"engine.py"))
            self.denied(lambda:tree_hash(self.worker))
            destination=self.root/"copy";self.denied(lambda:copy_workspace(self.worker,destination))
            self.assertFalse(destination.exists())

    def test_linked_directories_root_cache_and_hard_links_are_rejected(self):
        outside_dir=self.root/"external-dir";outside_dir.mkdir();(outside_dir/"value").write_text("marker")
        linked=self.worker/"sub";linked.symlink_to(outside_dir)
        self.denied(lambda:read_workspace_text(self.worker,"sub/value"));self.denied(lambda:tree_hash(self.worker));linked.unlink()
        linkedroot=self.root/"root-alias";linkedroot.symlink_to(self.worker)
        self.denied(lambda:tree_hash(linkedroot))
        cache=self.worker/"__pycache__";cache.mkdir();(cache/"ignored.pyc").symlink_to(self.outside)
        self.denied(lambda:tree_hash(self.worker));(cache/"ignored.pyc").unlink()
        os.link(self.outside,self.worker/"hardlink")
        self.denied(lambda:tree_hash(self.worker));self.denied(lambda:read_workspace_text(self.worker,"hardlink"))

    def test_fifo_is_rejected_without_blocking_and_only_named_sockets_are_transient(self):
        fifo=self.worker/"fifo";os.mkfifo(fifo);self.denied(lambda:tree_hash(self.worker));fifo.unlink()
        for name,allowed in ((".memory.sock",True),(".scenario.sock",True),("other.sock",False)):
            with socket.socket(socket.AF_UNIX) as sock:
                path=self.worker/name;sock.bind(str(path))
                if allowed:self.assertEqual(list(tree_hash(self.worker)),["engine.py"])
                else:self.denied(lambda:tree_hash(self.worker))
            path.unlink()

    def test_limits_reject_complete_view_and_relative_traversal_is_denied(self):
        c=workspace_config()
        for key,limit in (("max_file_bytes",1),("max_tree_bytes",1)):
            self.denied(lambda:workspace_hash(self.worker,dict(c,**{key:limit})))
        (self.worker/"extra").write_text("a")
        self.denied(lambda:workspace_hash(self.worker,dict(c,max_tree_entries=1)))
        nested=self.worker/"a"/"b";nested.mkdir(parents=True)
        self.denied(lambda:workspace_hash(self.worker,dict(c,max_depth=1)))
        for relative in ("../outside-marker",str(self.outside)):
            self.denied(lambda:read_workspace_text(self.worker,relative))

    def test_swap_between_metadata_and_open_cannot_redirect_parent_read(self):
        actual=os.open;swapped=False
        def swap(path,flags,*args,**kwargs):
            nonlocal swapped
            if path=="engine.py" and kwargs.get("dir_fd") is not None and not swapped:
                swapped=True;(self.worker/"engine.py").unlink();(self.worker/"engine.py").symlink_to(self.outside)
            return actual(path,flags,*args,**kwargs)
        with patch("memorylab.workspace.os.open",side_effect=swap):self.denied(lambda:tree_hash(self.worker))
        self.assertTrue(swapped)

    def test_growth_during_read_is_bounded(self):
        c=dict(workspace_config(),max_file_bytes=30);actual=os.read;grew=False
        def grow(fd,size):
            nonlocal grew
            if not grew:
                grew=True
                with (self.worker/"engine.py").open("a") as stream:stream.write("x"*100)
            return actual(fd,size)
        with patch("memorylab.workspace.os.read",side_effect=grow):
            self.denied(lambda:read_workspace_text(self.worker,"engine.py",c))
