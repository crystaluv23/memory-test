"""Bounded descriptor-relative reads of files controlled by a test subject."""
import hashlib
import json
import os
import stat
from pathlib import Path

from .contracts import ContractError,exact_object,require


def workspace_config():
    c=json.loads((Path(__file__).resolve().parent.parent/"configs/workspace_v0.json").read_text())
    exact_object(c,{"schema_version","hypothesis","max_file_bytes","max_tree_bytes","max_tree_entries","max_depth",
        "transient_sockets","links","formal_admission","model_calls"},"invalid_workspace_config")
    require(c["schema_version"]=="workspace-boundary-v0" and c["links"]=="reject_symbolic_and_multiple_hard_links" and
        c["transient_sockets"]==[".memory.sock",".scenario.sock"] and c["formal_admission"] is False and
        type(c["model_calls"]) is int and c["model_calls"]==0,"unsupported_workspace_policy")
    for k in ("max_file_bytes","max_tree_bytes","max_tree_entries","max_depth"):
        require(type(c[k]) is int and c[k]>0,"invalid_workspace_limit")
    return c


def _directory(path,dir_fd=None):
    try:return os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dir_fd)
    except OSError as exc:raise ContractError("unsafe_workspace_directory") from exc


def _file(parent,name,limit):
    fd=None
    try:
        # NONBLOCK prevents an attacker-created FIFO from hanging the parent.
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        meta=os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_nlink==1,"unsafe_workspace_file")
        require(meta.st_size<=limit,"workspace_file_byte_limit")
        result=bytearray()
        while True:
            chunk=os.read(fd,min(65536,limit+1-len(result)))
            if not chunk:break
            result.extend(chunk);require(len(result)<=limit,"workspace_file_byte_limit")
        return bytes(result)
    except OSError as exc:raise ContractError("unsafe_workspace_file") from exc
    finally:
        if fd is not None:os.close(fd)


def read_workspace_bytes(root,relative,config=None):
    c=config or workspace_config();parts=Path(relative).parts
    require(parts and not Path(relative).is_absolute() and all(p not in (".","..") for p in parts),"invalid_workspace_relative_path")
    require(len(parts)<=c["max_depth"],"workspace_depth_limit")
    fd=_directory(root)
    try:
        for part in parts[:-1]:
            child=_directory(part,fd);os.close(fd);fd=child
        return _file(fd,parts[-1],c["max_file_bytes"])
    finally:os.close(fd)


def read_workspace_text(root,relative,config=None):
    return read_workspace_bytes(root,relative,config).decode("utf-8")


def workspace_snapshot(root,config=None):
    c=config or workspace_config();result={};count=0;size=0
    def walk(fd,prefix,depth):
        nonlocal count,size
        require(depth<=c["max_depth"],"workspace_depth_limit")
        # Entry count is bounded while enumerating, before sorting or reading.
        names=[]
        with os.scandir(fd) as entries:
            for entry in entries:
                count+=1;require(count<=c["max_tree_entries"],"workspace_entry_limit");names.append(entry.name)
        for name in sorted(names):
            relative=prefix+(name,)
            try:meta=os.stat(name,dir_fd=fd,follow_symlinks=False)
            except OSError as exc:raise ContractError("unsafe_workspace_file") from exc
            require(not stat.S_ISLNK(meta.st_mode),"unsafe_workspace_file")
            if stat.S_ISDIR(meta.st_mode):
                child=_directory(name,fd)
                try:walk(child,relative,depth+1)
                finally:os.close(child)
            elif stat.S_ISSOCK(meta.st_mode) and len(relative)==1 and name in c["transient_sockets"]:
                continue
            else:
                data=_file(fd,name,c["max_file_bytes"]);size+=len(data)
                require(size<=c["max_tree_bytes"],"workspace_tree_byte_limit")
                # Caches are checked for links too, but never task evidence.
                if "__pycache__" not in relative:result["/".join(relative)]=data
    fd=_directory(root)
    try:walk(fd,(),0)
    finally:os.close(fd)
    return result


def workspace_hash(root,config=None):
    return {name:hashlib.sha256(data).hexdigest() for name,data in workspace_snapshot(root,config).items()}


def copy_workspace(root,destination):
    # Capture first; copying never follows a path supplied by the subject.
    contents=workspace_snapshot(root);destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    for name,data in contents.items():
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
