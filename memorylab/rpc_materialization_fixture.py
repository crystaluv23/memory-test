"""Persist native diagnostic outputs and verify committed roles without writes."""
import argparse
from pathlib import Path

from .cards import CanonicalCards,EvidenceStore
from .common import save
from .contracts import ContractError,canonical,read_kernel,require
from .ledger import Ledger
from .registry import Registry
from .rpc_admission import RPCAdmission
from .rpc_disclosure_fixture import build_fixture
from .rpc_output import output_config
from .rpc_role import run_fixture,sealed_json

AT='2026-10-08T10:00:00+08:00'


def fixed_final(packet):
    """Scripted controller-only output; never evidence of model competence."""
    p=packet['payload'];context=p['context'];role=p['role']
    if role=='extractor':
        return {'cards':[{'id':'native-fixture-'+packet['sha256'][:16],'expected_version':0,'type_hint':'environment/services',
            'brief':'Check the dependency for integrations that use the service.',
            'detail':'RPC_PRIVATE_PRESET: Check service health only for integrations that use it; diagnose other failures separately.',
            'evidence_refs':[context['sources'][0]['ref']],'outcome':'unknown','status':'active','supersedes':[]}]} if context['sources'] else {'cards':[]}
    if role=='consolidation':
        return {'indices':[{'type_hint':'environment','brief':'Conditional service checks',
            'detail':'RPC_PRIVATE_PRESET: Navigation to the conditional integration checks.',
            'support_refs':[v['ref'] for v in context['derived_input']['payload']['cards']]}]}
    if role=='reflector':
        return {'annotations':[{'support':context['derived_input']['payload']['eligible_refs'][0],
            'text':'RPC_PRIVATE_PRESET: Check the current service precondition before applying this experience.'}]}
    field='context_profile' if role=='evaluator' else 'organizer.type'
    old=context['parent_config'][field]
    new=('pressure' if old=='clean' else 'clean') if role=='evaluator' else ('fixed_tree' if old=='flat' else 'flat')
    return {'schema_version':1,'kind':context['parent']['kind'],'parent':context['parent'],
        'mutation':{'field':field,'old':old,'new':new},
        'hypothesis':'RPC_PRIVATE_PRESET: Fixed synthetic proposal for the native commit diagnostic only.',
        'evidence_refs':[context['history'][0]['ref']] if context['history'] else []}


def admission(ledger):
    kernel=read_kernel();registry=Registry(ledger,kernel)
    return RPCAdmission(CanonicalCards(EvidenceStore(ledger,kernel)),registry)


def verify_fixture(output):
    output=Path(output);binding=sealed_json(output,'binding.json')
    require(binding['category']=='infrastructure_acceptance' and binding['formal_admission'] is False,'rpc_materialization_category')
    with Ledger(output/'controller.sqlite',readonly=True) as ledger:
        a=admission(ledger);receipt=a.verify(binding['completion_request_id'])
        require(receipt['result']['attempt']==binding['begin']['result']['attempt'] and
            ledger.receipt(binding['begin']['request_id'])==binding['begin'] and
            a._attempt(receipt['result']['attempt'])['packet']['payload']['role']==binding['role'],'rpc_materialization_binding')
        return {'role':binding['role'],'receipt':receipt,'denominator':a.report(),'category':'infrastructure_acceptance',
            'actual_model_calls':0,'formal_admission':False,'kernel_frozen':False}


def run(role,cli,output):
    require(role in output_config()['roles'],'unsupported_admission_role')
    output=Path(output);gateway=build_fixture(role,output)
    record={'status':'started','role':role,'category':'infrastructure_acceptance','actual_model_calls':0,
        'formal_admission':False,'kernel_frozen':False}
    with Ledger(output/'controller.sqlite') as ledger:
        a=admission(ledger);begin=a.prepare('native-role-start',gateway,cli)
        binding={'role':role,'begin':begin,'completion_request_id':'native-role-complete',
            'category':'infrastructure_acceptance','formal_admission':False}
        save(output/'binding.json',binding);save(output/'result.json',record)
        native=run_fixture(cli,output/'transport',gateway,fixed_final(gateway.packet))
        record['native_status']=native['status']
        try:
            receipt=a.complete(binding['completion_request_id'],begin['result']['attempt'],AT)
        except (ContractError,OSError,ValueError,UnicodeError,KeyError,TypeError) as exc:
            record.update(status='needs_attention',error={'type':type(exc).__name__,'code':getattr(exc,'code','rpc_materialization_error')})
        else:
            record.update(status='typed_native_rpc_materialized_not_formal' if receipt['result']['outcome']=='materialized' else 'failed',receipt=receipt)
        record['denominator']=a.report()
    if 'receipt' in record:
        try:record['read_only_verification']=verify_fixture(output)
        except (ContractError,OSError,ValueError,UnicodeError,KeyError,TypeError) as exc:
            record.update(status='failed',error={'type':type(exc).__name__,'code':getattr(exc,'code','rpc_materialization_reverification_failed')})
    save(output/'result.json',record);return record


def main(argv=None):
    p=argparse.ArgumentParser(description='Fixed-response native role commit diagnostic; verify is read-only and starts no subject')
    commands=p.add_subparsers(dest='action',required=True)
    r=commands.add_parser('run');r.add_argument('--role',choices=output_config()['roles'],required=True)
    r.add_argument('--cli',required=True);r.add_argument('--output',required=True)
    v=commands.add_parser('verify');v.add_argument('--output',required=True)
    args=p.parse_args(argv)
    result=verify_fixture(args.output) if args.action=='verify' else run(args.role,args.cli,args.output)
    print(canonical(result).decode())
    return 0 if args.action=='verify' or result['status']=='typed_native_rpc_materialized_not_formal' else 2


if __name__=='__main__':raise SystemExit(main())
