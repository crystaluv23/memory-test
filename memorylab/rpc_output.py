"""Frozen, narrow role output schemas; no new schema or execution language."""
from pathlib import Path
import hashlib

from .contracts import ContractError,canonical,exact_object,require
from .kernel_cli import read_json


def output_config():
    c=read_json(Path(__file__).resolve().parent.parent/'configs/rpc_output_v0.json')
    exact_object(c,{'schema_version','hypothesis','mode','roles','max_output_bytes','max_schema_depth','max_capture_bytes',
        'raw_policy','final_policy','actual_model_calls','formal_admission','automatic_retry'},'invalid_rpc_output_config')
    require(c['schema_version']=='rpc-output-v0' and c['mode']=='typed_native_rpc_fixed_fixture' and
        c['roles']==['extractor','consolidation','reflector','evaluator','proposer'] and
        c['raw_policy']=='native_rpc_bytes_not_synthetic_exec_events' and c['final_policy']=='frozen_role_schema_unique_final_answer' and
        c['formal_admission'] is False and c['automatic_retry'] is False and type(c['actual_model_calls']) is int and c['actual_model_calls']==0,
        'unsupported_rpc_output_policy')
    for k in ('max_output_bytes','max_schema_depth','max_capture_bytes'):
        require(type(c[k]) is int and c[k]>0,'invalid_rpc_output_limit')
    return c


def output_prompt(packet):return packet['payload']['prompt']+'\nSupplied role packet:\n'+canonical(packet).decode()


def native_program(value):
    exact_object(value,{'transport','cli_path','cli_sha256','output_policy'},'invalid_native_role_program')
    require(value['transport']=='native_rpc_fixed_fixture' and value['output_policy']=='fixed_role_packet_template',
        'unsupported_native_role_program')
    require(type(value['cli_path']) is str and Path(value['cli_path']).is_absolute(),'rpc_role_cli')
    binary=Path(value['cli_path']);require(binary.is_file() and not binary.is_symlink(),'rpc_role_cli')
    require(hashlib.sha256(binary.read_bytes()).hexdigest()==value['cli_sha256'],'native_role_cli_drift')
    return value


def validate_output(value,schema,config=None):
    c=config or output_config();require(len(canonical(value))<=c['max_output_bytes'],'rpc_role_output_overflow')
    def visit(v,s,depth):
        require(depth<=c['max_schema_depth'],'rpc_role_schema_depth')
        require(type(s) is dict,'unsupported_rpc_role_schema')
        if 'anyOf' in s:
            exact_object(s,{'anyOf'},'unsupported_rpc_role_schema');require(type(s['anyOf']) is list and bool(s['anyOf']),'unsupported_rpc_role_schema')
            for sub in s['anyOf']:
                try:visit(v,sub,depth+1);return
                except ContractError as exc:
                    if exc.code!='rpc_role_output_schema':raise
            require(False,'rpc_role_output_schema')
        kind=s.get('type');allowed={'type','enum'}
        if kind=='object':allowed|={'properties','required','additionalProperties'}
        elif kind=='array':allowed|={'items'}
        elif kind=='integer':allowed|={'minimum','maximum'}
        require(set(s)<=allowed,'unsupported_rpc_role_schema')
        types={'object':dict,'array':list,'string':str,'integer':int,'boolean':bool,'null':type(None)}
        require(kind in types,'unsupported_rpc_role_schema');require(type(v) is types[kind],'rpc_role_output_schema')
        if 'enum' in s:require(canonical(v) in [canonical(x) for x in s['enum']],'rpc_role_output_schema')
        if kind=='integer':
            require(('minimum' not in s or v>=s['minimum']) and ('maximum' not in s or v<=s['maximum']),'rpc_role_output_schema')
        if kind=='object':
            require(type(s.get('properties')) is dict and type(s.get('required')) is list and s.get('additionalProperties') is False,'unsupported_rpc_role_schema')
            require(set(s['required'])<=set(v)<=set(s['properties']),'rpc_role_output_schema')
            for k,item in v.items():visit(item,s['properties'][k],depth+1)
        elif kind=='array':
            require('items' in s,'unsupported_rpc_role_schema')
            for item in v:visit(item,s['items'],depth+1)
    visit(value,schema,0)
    return value
