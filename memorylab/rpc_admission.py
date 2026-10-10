"""Native RPC bytes into the existing atomic role materializers; fixtures only."""
import base64
import hashlib
from pathlib import Path

from .admission import RoleAdmission
from .contracts import ContractError,canonical,identifier,mutation_candidate,reference,require,sha256,timestamp
from .derived import DerivedStage
from .kernel_cli import parse_json
from .roles import EvidenceReader,process_status
from .rpc_disclosure import RPCDisclosure,SealedDisclosure,disclosure_config
from .rpc_output import output_config
from .rpc_role import sealed_json,validate_sealed
from .workspace import read_workspace_bytes


class RPCAdmission(RoleAdmission):
    def prepare(self,request_id,gateway,cli):
        identifier(request_id);require(type(gateway) is RPCDisclosure,'rpc_role_disclosure_type');gateway._check()
        c=output_config();packet=gateway.packet;p=packet['payload']
        require(p['role'] in c['roles'],'unsupported_admission_role');self._regenerate(packet)
        binary=Path(cli);require(binary.is_file() and not binary.is_symlink(),'rpc_role_cli')
        projection=None
        if gateway.initial_identity['kind']=='memory':
            value=gateway.initial_identity['projection'];projection={k:value[k] for k in ('kind','id','version','namespace')};projection['sha256']=sha256(value)
            require(self.ledger.get(projection)==value and projection['namespace']==p['namespace'],'rpc_admission_memory_projection')
        recipe={'packet':packet,'disclosure_sha256':gateway.binding_sha256,'cli_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'root':str(gateway.output.parent.resolve()),'category':'infrastructure_acceptance','rpc_output_config':c,
            'role_config':self.role,'admission_config':self.config,'projection':projection}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,recipe)
            if prior:return prior
            require(gateway.thread is None and not (gateway.output.parent/'transport').exists(),'rpc_admission_already_started')
            ref=self.ledger.put({'kind':'rpc_role_attempt','id':'rpc-attempt-'+sha256({'request_id':request_id}),'version':1,
                'begin_request_id':request_id,**recipe,'reserved_model_sessions':0},'control')
            return self.ledger.commit_receipt(request_id,recipe,{'status':'role_started','attempt':ref,'formal_admission':False},'control')

    def _attempt(self,ref):
        reference(ref,{'rpc_role_attempt'});require(ref['namespace']=='control','invalid_role_attempt_namespace')
        attempt=self.ledger.get(ref);begin=self.ledger.receipt(attempt['begin_request_id'])
        require(begin is not None and begin['result']['attempt']==ref,'uncommitted_role_attempt')
        recipe={k:v for k,v in attempt.items() if k not in ('kind','id','version','begin_request_id','reserved_model_sessions')}
        require(self.ledger.receipt(attempt['begin_request_id'],recipe)==begin,'rpc_admission_begin_binding')
        require(attempt['rpc_output_config']==output_config() and attempt['role_config']==self.role and attempt['admission_config']==self.config and
            attempt['category']=='infrastructure_acceptance' and attempt['reserved_model_sessions']==0,'rpc_admission_config_drift')
        self._regenerate(attempt['packet'])
        return attempt

    def _snapshot_rpc(self,root):
        files={};total=0;c=output_config()
        names=['recipe.json','process.json','handle/process.json','exit.json','stdin.bin','stdout.bin','stderr.txt','generated-config.toml',
            'provider.json','budget.sqlite']
        names += ['provider-'+kind+'-'+str(i)+'.bin' for kind in ('request','response','forwarded') for i in (1,2)]
        for name in names:
            if not (root/'transport'/name).exists():continue
            raw=read_workspace_bytes(root/'transport',name,{'max_depth':2,'max_file_bytes':c['max_capture_bytes']-total})
            files['transport/'+name]=raw;total+=len(raw);require(total<=c['max_capture_bytes'],'rpc_admission_capture_limit')
        for name in ('initial.json','calls.json','service.json'):
            if not (root/'disclosure'/name).exists():continue
            raw=read_workspace_bytes(root/'disclosure',name,{'max_depth':1,'max_file_bytes':min(disclosure_config()['max_snapshot_bytes'],c['max_capture_bytes']-total)})
            files['disclosure/'+name]=raw;total+=len(raw);require(total<=c['max_capture_bytes'],'rpc_admission_capture_limit')
        return files

    def _verified(self,attempt,root,exclude_capture=None):
        recipe=sealed_json(root/'transport','recipe.json')
        require(recipe['cli_sha256']==attempt['cli_sha256'] and recipe['disclosure_sha256']==attempt['disclosure_sha256'] and
            recipe.get('typed_output') is not None,'rpc_admission_recipe_binding')
        service=SealedDisclosure(root/'disclosure',attempt['disclosure_sha256'])
        require(service.packet==attempt['packet'],'rpc_admission_packet_binding')
        if attempt['projection'] is not None:
            require(self.ledger.get(attempt['projection'])==service.initial.projection,'rpc_admission_memory_projection')
        verified=validate_sealed(root/'transport',service.thread,service.turn,service)
        u=verified['simulated_usage']['tokenUsage']['total']
        transport={'transport_success':True,'thread_id':service.thread,'turn_id':service.turn,'turn_completed':True,
            'command_count':len(verified['dynamic']),'usage':{'input_tokens':u['inputTokens'],'cached_input_tokens':u['cachedInputTokens'],
                'output_tokens':u['outputTokens'],'reasoning_output_tokens':u['reasoningOutputTokens']},'total_tokens':u['totalTokens'],
            'final':parse_json(verified['final']['text']),'error':None,'formal_admission':False,'promotion_eligible':False,
            'provider_budget_qualified':False,'resolved_model':'memorylab-fixture'}
        require(not any(ref!=exclude_capture and v.get('transport',{}).get('thread_id')==service.thread and v.get('transport_kind')=='codex_rpc_fixture'
            for ref,v in self.ledger.objects('role_capture','control')),'reused_subject_thread')
        if type(service.initial) is EvidenceReader:
            source_events=service.service.events;memory_events=[]
            disclosed=self._reads(attempt['packet'],self._regenerate(attempt['packet']),source_events)
        else:source_events=[];memory_events=service.service.events;disclosed={}
        return transport,source_events,memory_events,disclosed

    def _verify_materialization(self,attempt,result,capture,transport,disclosed):
        p=attempt['packet']['payload'];role=p['role'];final=transport['final'];m=result['materialization']
        derived=DerivedStage(self.cards,self.registry)
        try:
            if role=='extractor':self._validate_extraction(attempt,final,disclosed)
            elif role in ('consolidation','reflector'):derived._validate_output(p['context']['derived_input'],final)
            else:self._validate_proposal_input(attempt,final)
        except ContractError as exc:
            require(m is None and result['outcome']=='rejected' and result['code']==exc.code,'rpc_admission_failure_binding');return
        require(m is not None,'rpc_admission_materialization_missing')
        if role=='extractor':
            prefix='extract-';request={'proposal':final,'namespace':p['namespace'],'cutoff':p['cutoff'],
                'timestamp':capture['timestamp'],'extractor':self.cards.extractor}
        elif role in ('consolidation','reflector'):
            prefix='derive-';request={'input':p['context']['derived_input'],'raw_response':canonical(final).decode(),
                'invocation':{'mode':'captured_diagnostic','capture':result['capture']}}
        else:
            prefix='proposal-';request={'proposal':final,'role':role,'cutoff':p['cutoff'],'kernel':self.registry.fingerprint}
        require(m['request_id']==prefix+sha256(result['attempt']) and self.ledger.receipt(m['request_id'],request)==m,
            'rpc_admission_materialization_drift')
        mr=m['result'];rejected=mr['status']=='rejected'
        require(result['outcome']==('rejected' if rejected else 'materialized') and
            result['code']==(mr['code'] if rejected else 'role_output_committed'),'rpc_admission_result_binding')
        if rejected:return
        if role=='extractor':
            require(mr['status']=='extracted' and len(mr['cards'])==len(final['cards']),'rpc_admission_materialization_drift')
            for i,(ref,raw) in enumerate(zip(mr['cards'],final['cards'])):
                expected={'schema_version':1,'kind':'card','id':raw['id'] or 'card-'+sha256({'namespace':p['namespace'],'request_id':m['request_id'],'index':i}),
                    'version':raw['expected_version']+1,'namespace':p['namespace'],'timestamp':capture['timestamp'],'extractor':self.cards.extractor,
                    **{k:raw[k] for k in ('type_hint','brief','detail','evidence_refs','outcome','status','supersedes')}}
                actual=self.ledger.get(ref);self.cards.validate(actual)
                require(actual==expected,'rpc_admission_card_binding')
        elif role in ('consolidation','reflector'):
            batch=derived.resolve(mr['batch'],role,p['namespace'],p['cutoff'],p['context']['derived_input']['payload']['spec'])
            require(batch['items']==derived._validate_output(request['input'],final) and batch['raw']==mr['raw'] and
                batch['invocation']==request['invocation'] and batch['request_id']==m['request_id'] and
                self.ledger.get(mr['raw'])=={'kind':'derived_raw','id':'derived-raw-'+sha256(request),'version':1,**request},
                'rpc_admission_derived_binding')
        else:
            child=mutation_candidate(final,self.registry.resolve(final['parent']),final['parent'],self.registry.kernel)
            require((child is None and mr['status']=='noop' and mr['candidate'] is None) or
                (child is not None and mr['status']=='accepted' and self.registry.resolve(mr['candidate'])==child),'rpc_admission_candidate_binding')

    def verify(self,request_id):
        receipt=self.ledger.receipt(request_id);require(receipt is not None,'rpc_admission_receipt_missing')
        result=receipt['result'];attempt=self._attempt(result['attempt']);capture=self.ledger.get(result['capture']);root=Path(attempt['root'])
        require(capture['completion_request_id']==request_id and capture['attempt']==result['attempt'] and
            capture['transport_kind']=='codex_rpc_fixture' and capture['packet']==attempt['packet'] and
            capture['category']=='infrastructure_acceptance' and capture['formal_admission'] is False and
            capture['semantic_validation']=='not_established','rpc_admission_capture_binding')
        require(result['status']=='role_completed' and result['category']=='infrastructure_acceptance' and
            type(result['actual_model_calls']) is int and result['actual_model_calls']==0 and
            type(result['reserved_model_sessions']) is int and result['reserved_model_sessions']==0 and result['simulated_usage'] is True and
            all(result[k] is False for k in ('formal_admission','gain_evidence','promotion_eligible','cost_unknown')) and
            result['usage']==capture['transport']['usage'] and result['transport_usage_unknown']==(result['usage'] is None),'rpc_admission_result_binding')
        files=self._snapshot_rpc(root);hashes={n:hashlib.sha256(v).hexdigest() for n,v in files.items()}
        request={'attempt':result['attempt'],'files':hashes,'timestamp':capture['timestamp']}
        if capture.get('interrupted') is True:request['interrupted']=True
        require(hashes==capture['files'] and self.ledger.receipt(request_id,request)==receipt,'rpc_admission_capture_drift')
        require(base64.b64decode(capture['raw_events_base64'],validate=True)==files.get('transport/stdout.bin',b'') and
            base64.b64decode(capture['raw_stdin_base64'],validate=True)==files.get('transport/stdin.bin',b'') and
            base64.b64decode(capture['raw_stderr_base64'],validate=True)==files.get('transport/stderr.txt',b''),'rpc_admission_raw_binding')
        if capture.get('interrupted') is True:
            require(process_status(root/'transport/handle')['status']=='exited','rpc_role_exit_unverified')
            require(result['outcome']=='interrupted' and result['code']==capture['validation_code']=='unsealed_rpc_role_interruption' and
                result['materialization'] is None and result['usage'] is None and capture['transport']['transport_success'] is False,
                'rpc_admission_failure_binding')
            require(capture['unverified_audits']=={n:{'sha256':hashes[n],'base64':base64.b64encode(v).decode()}
                for n,v in files.items() if n.startswith('disclosure/')},'rpc_admission_raw_binding')
            return receipt
        try:transport,sources,memories,disclosed=self._verified(attempt,root,result['capture'])
        except (ContractError,ValueError,UnicodeError,KeyError,TypeError) as exc:
            code=getattr(exc,'code','invalid_rpc_role_capture')
            require(capture['validation_code']==code and result['outcome']=='rejected' and result['code']==code and
                result['materialization'] is None,'rpc_admission_failure_binding')
        else:
            require(capture['validation_code']=='raw_transport_valid' and capture['transport']==transport and
                capture['source_events']==sources and capture['memory_events']==memories,'rpc_admission_replay_mismatch')
            self._verify_materialization(attempt,result,capture,transport,disclosed)
        require({n:hashlib.sha256(v).hexdigest() for n,v in self._snapshot_rpc(root).items()}==hashes,'rpc_admission_capture_drift')
        return receipt

    def interrupt(self,request_id,attempt_ref,at):
        identifier(request_id);timestamp(at);attempt=self._attempt(attempt_ref);root=Path(attempt['root'])
        require(process_status(root/'transport/handle')['status']=='exited','rpc_role_exit_unverified')
        files=self._snapshot_rpc(root);hashes={n:hashlib.sha256(v).hexdigest() for n,v in files.items()}
        request={'attempt':attempt_ref,'files':hashes,'timestamp':at,'interrupted':True}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return self.verify(request_id)
            require(not any(v['attempt']==attempt_ref for _,v in self.ledger.objects('role_capture','control')),'role_attempt_already_completed')
            code='unsealed_rpc_role_interruption'
            capture=self.ledger.put({'kind':'role_capture','id':'rpc-capture-'+sha256(attempt_ref),'version':1,'attempt':attempt_ref,
                'packet':attempt['packet'],'category':'infrastructure_acceptance','completion_request_id':request_id,
                'transport_kind':'codex_rpc_fixture','timestamp':at,'interrupted':True,'files':hashes,
                'raw_events_base64':base64.b64encode(files.get('transport/stdout.bin',b'')).decode(),
                'raw_stdin_base64':base64.b64encode(files.get('transport/stdin.bin',b'')).decode(),
                'raw_stderr_base64':base64.b64encode(files.get('transport/stderr.txt',b'')).decode(),
                'source_events':[],'memory_events':[],'formal_admission':False,'semantic_validation':'not_established',
                'transport':{'transport_success':False,'usage':None,'total_tokens':None,'error':code},'validation_code':code,
                'unverified_audits':{n:{'sha256':hashes[n],'base64':base64.b64encode(v).decode()} for n,v in files.items() if n.startswith('disclosure/')}},'control')
            result={'status':'role_completed','outcome':'interrupted','code':code,'attempt':attempt_ref,'capture':capture,'materialization':None,
                'category':'infrastructure_acceptance','usage':None,'simulated_usage':True,'actual_model_calls':0,'reserved_model_sessions':0,
                'cost_unknown':False,'transport_usage_unknown':True,'formal_admission':False,'gain_evidence':False,'promotion_eligible':False}
            return self.ledger.commit_receipt(request_id,request,result,'control')

    def complete(self,request_id,attempt_ref,at,fault=None):
        identifier(request_id);timestamp(at);attempt=self._attempt(attempt_ref);root=Path(attempt['root'])
        require(process_status(root/'transport'/'handle')['status']=='exited','rpc_role_exit_unverified')
        files=self._snapshot_rpc(root);request={'attempt':attempt_ref,'files':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()},'timestamp':at}
        def checkpoint(name):
            if fault:fault(name)
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:
                return self.verify(request_id)
            require(not any(v['attempt']==attempt_ref for _,v in self.ledger.objects('role_capture','control')),'role_attempt_already_completed')
            capture={'kind':'role_capture','id':'rpc-capture-'+sha256(attempt_ref),'version':1,'attempt':attempt_ref,'packet':attempt['packet'],
                'category':'infrastructure_acceptance','completion_request_id':request_id,'transport_kind':'codex_rpc_fixture','timestamp':at,
                'files':request['files'],'raw_events_base64':base64.b64encode(files.get('transport/stdout.bin',b'')).decode(),
                'raw_stdin_base64':base64.b64encode(files.get('transport/stdin.bin',b'')).decode(),
                'raw_stderr_base64':base64.b64encode(files.get('transport/stderr.txt',b'')).decode(),
                'source_events':[],'memory_events':[],'formal_admission':False,'semantic_validation':'not_established'}
            code=None;materialized=None;transport=None;disclosed={}
            try:
                transport,capture['source_events'],capture['memory_events'],disclosed=self._verified(attempt,root)
                require({n:hashlib.sha256(v).hexdigest() for n,v in self._snapshot_rpc(root).items()}==request['files'],'rpc_admission_capture_drift')
            except (ContractError,ValueError,UnicodeError,KeyError,TypeError) as exc:
                code=getattr(exc,'code','invalid_rpc_role_capture')
            capture['transport']=transport or {'transport_success':False,'usage':None,'total_tokens':None,'error':code}
            capture['validation_code']=code or 'raw_transport_valid'
            capture_ref=self.ledger.put(capture,'control');checkpoint('raw')
            if code is None:
                try:
                    with self.ledger.savepoint():
                        materialized=self._materialize(attempt,transport['final'],disclosed,capture_ref,capture,attempt_ref,at)
                        if materialized['result']['status']=='rejected':code=materialized['result']['code']
                except ContractError as exc:code=exc.code
            checkpoint('materialized')
            result={'status':'role_completed','outcome':'rejected' if code else 'materialized','code':code or 'role_output_committed',
                'attempt':attempt_ref,'capture':capture_ref,'materialization':materialized,'category':'infrastructure_acceptance',
                'usage':capture['transport']['usage'],'simulated_usage':True,'actual_model_calls':0,'reserved_model_sessions':0,
                'cost_unknown':False,'transport_usage_unknown':capture['transport']['usage'] is None,'formal_admission':False,'gain_evidence':False,'promotion_eligible':False}
            receipt=self.ledger.commit_receipt(request_id,request,result,'control');checkpoint('receipt');return receipt
