"""Bounded subprocess transport; preserve partial outputs and kill whole group."""
import os
import base64
import hashlib
import selectors
import signal
import subprocess
import time

from .contracts import require


def terminate(process):
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass


def bounded_run(command,cwd,input_bytes,seconds,stdout_bytes,stderr_bytes,env=None,on_stdout=None,on_start=None):
    require(type(input_bytes) is bytes,"invalid_process_input")
    for v in (seconds,stdout_bytes,stderr_bytes):require(type(v) is int and v>0,"invalid_process_limit")
    started=time.monotonic();outputs={"stdout":bytearray(),"stderr":bytearray()};limits={"stdout":stdout_bytes,"stderr":stderr_bytes}
    truncated={"stdout":False,"stderr":False};reason=None;sent=0
    process=subprocess.Popen(command,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        start_new_session=True,bufsize=0,env=env)
    selector=selectors.DefaultSelector()
    try:
        if on_start is not None:on_start(process)
        for pipe,key in ((process.stdout,"stdout"),(process.stderr,"stderr")):
            os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,key)
        if input_bytes:
            os.set_blocking(process.stdin.fileno(),False);selector.register(process.stdin,selectors.EVENT_WRITE,"stdin")
        else:process.stdin.close()
        while selector.get_map():
            remaining=seconds-(time.monotonic()-started)
            if remaining<=0:reason="production_timeout";terminate(process);break
            for key,mask in selector.select(min(.1,remaining)):
                pipe=key.fileobj;name=key.data
                if name=="stdin":
                    try:sent+=os.write(pipe.fileno(),input_bytes[sent:sent+65536])
                    except BrokenPipeError:sent=len(input_bytes)
                    except BlockingIOError:continue
                    if sent==len(input_bytes):selector.unregister(pipe);pipe.close()
                else:
                    try:chunk=os.read(pipe.fileno(),min(65536,limits[name]-len(outputs[name])+1))
                    except BlockingIOError:continue
                    if not chunk:selector.unregister(pipe);pipe.close();continue
                    available=limits[name]-len(outputs[name]);outputs[name].extend(chunk[:available])
                    if len(chunk)>available:
                        truncated[name]=True;reason="production_"+name+"_overflow"
                    if name=="stdout" and on_stdout is not None:
                        observed=on_stdout(chunk[:available])
                        if observed is not None:
                            require(type(observed) is str and bool(observed),"invalid_process_observer_reason")
                            reason=reason or observed
                    if reason:terminate(process);break
            if reason:break
        try:process.wait(timeout=max(.01,seconds-(time.monotonic()-started)))
        except subprocess.TimeoutExpired:
            reason="production_timeout";terminate(process);process.wait(timeout=2)
    finally:
        selector.close()
        terminate(process)
        if process.poll() is None:terminate(process);process.wait(timeout=2)
        for pipe in (process.stdin,process.stdout,process.stderr):
            if not pipe.closed:pipe.close()
    return {"exit_code":process.returncode,"stdout":bytes(outputs["stdout"]).decode("utf-8",errors="replace"),
        "stderr":bytes(outputs["stderr"]).decode("utf-8",errors="replace"),"error":reason,
        "raw_streams":{name:{"base64":base64.b64encode(data).decode(),"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)} for name,data in outputs.items()},
        "stdout_truncated":truncated["stdout"],"stderr_truncated":truncated["stderr"],
        "wall_seconds":time.monotonic()-started}
