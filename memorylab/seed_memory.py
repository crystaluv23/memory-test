"""Experiment-local full seed: immutable evidence, typed files, bounded disclosure."""
import copy
import heapq
import json
import re
from pathlib import Path
from .common import save
from .contracts import canonical, require, sha256


def terms(text):
    return set(re.findall(r'[a-z0-9_]+', text.lower()))


class Archive:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, value):
        ref = sha256(value)
        path = self.root / 'objects' / (ref + '.json')
        if path.exists():
            require(json.loads(path.read_text()) == value, 'seed_object_drift')
        else:
            save(path, value)
        return ref

    def get(self, ref):
        require(type(ref) is str and re.fullmatch('[a-f0-9]{64}', ref), 'seed_invalid_ref')
        value = json.loads((self.root / 'objects' / (ref + '.json')).read_text())
        require(sha256(value) == ref, 'seed_object_drift')
        return value

    def experience(self, raw, sources, category, stamp, id_, level=0, supports=()):
        require(type(raw) is dict and set(raw) == {'brief', 'detail', 'evidence_refs'}, 'seed_card_shape')
        require(all(type(raw[k]) is str and raw[k].strip() for k in ('brief', 'detail')), 'seed_empty_card')
        require(len(raw['brief'].encode()) <= 600 and len(raw['detail'].encode()) <= 8000, 'seed_card_size')
        require(type(raw['evidence_refs']) is list and bool(raw['evidence_refs']) and
                set(raw['evidence_refs']) <= set(sources), 'seed_foreign_evidence')
        require(level in (0, 1) and (not level or len(set(supports)) >= 2), 'seed_index_support_count')
        for ref in raw['evidence_refs']: self.get(ref)
        for ref in supports:
            card = self.get(ref)
            require(card['level'] == 0 and (card['type'] == category or card['type'].startswith(category + '/')), 'seed_index_type_conflict')
        record = {'kind': 'experience', 'id': id_, 'version': 1, 'type': category,
                  'level': level, 'at': stamp, **raw, 'support_refs': list(supports),
                  'origin_category': 'synthetic_project_preferences'}
        return self.put(record)

    def projection(self, folder, active, importance):
        folder = Path(folder)
        manifest = []
        for ref in active:
            record = self.get(ref)
            name = '_INDEX.md' if record['level'] else record['id'] + '.md'
            path = folder / record['type'] / name
            require(not path.exists(), 'seed_projection_path_collision')
            path.parent.mkdir(parents=True, exist_ok=True)
            text = ('---\n' + json.dumps({'ref': ref, 'id': record['id'], 'version': record['version'],
                    'type': record['type'], 'abstraction_level': record['level'],
                    'importance_self_report_ema': importance.get(ref, 0)}, ensure_ascii=False) + '\n---\n\n'
                    '# Brief\n\n' + record['brief'] + '\n\n# Detail\n\n' + record['detail'] + '\n\n'
                    '# Exact evidence refs\n\n' + '\n'.join(record['evidence_refs']) + '\n\n'
                    '# Exact lower experience refs\n\n' + '\n'.join(record['support_refs']) + '\n')
            path.write_text(text)
            manifest.append({'path': str(path.relative_to(folder)), 'ref': ref,
                             'sha256': __import__('hashlib').sha256(path.read_bytes()).hexdigest()})
        save(folder / 'manifest.json', manifest)
        return manifest


class SeedSession:
    def __init__(self, archive, active, importance, arm, config, task, folder):
        self.archive = archive
        self.active = list(active) if arm['enabled'] else []
        self.importance = copy.deepcopy(importance)
        self.arm = arm; self.config = config; self.task = task; self.folder = Path(folder)
        self.cards = {r: archive.get(r) for r in self.active}
        self.ids = {c['id']: r for r, c in self.cards.items()}
        self.disclosed = set(); self.opened = set(); self.detail_bytes = 0; self.events = []
        self.brief_bytes = 0
        self.routes = {}
        for r, c in self.cards.items():
            parts = c['type'].split('/')
            for n in range(1, len(parts) + 1): self.routes.setdefault('/'.join(parts[:n]), []).append(r)

    def global_score(self, ref):
        card = self.cards[ref]
        if self.arm['global_rank'] == 'recent': return (card['at'], ref)
        w = self.config['global_weights']; maximum = max([c['at'] for c in self.cards.values()] or [1])
        return (w['importance'] * (self.importance.get(ref, 0) + 1) / 2 +
                w['recency'] * card['at'] / max(1, maximum) + w['level'] * card['level'], ref)

    def rank(self):
        global_refs = [] if self.arm['global_rank'] == 'off' else heapq.nlargest(self.config['global_k'], self.active, key=self.global_score)
        local_k = 4 if self.arm['local_rank'] == 'lexical4' else self.config['local_k']
        local_refs = []
        if self.arm['local_rank'] == 'ancestor':
            category = self.task.get('type', '')
            parts = category.split('/') if category else []
            pool = set()
            for n in range(len(parts), 0, -1):
                route = '/'.join(parts[:n])
                pool.update(self.routes.get(route, []))
                if len([r for r in pool if self.cards[r]['level'] == 0]) >= local_k: break
            def proximity(ref):
                card = self.cards[ref]; p = card['type'].split('/'); shared = 0
                for a, b in zip(parts, p):
                    if a != b: break
                    shared += 1
                return (shared, card['level'], self.importance.get(ref, 0), card['at'], ref)
            local_refs = heapq.nlargest(local_k, pool, key=proximity)
        else:
            query = terms(canonical(self.task).decode())
            def lexical(ref):
                card = self.cards[ref]
                return (len(query & terms(card['brief'] + ' ' + card['type'])),
                        self.importance.get(ref, 0), card['at'], ref)
            pool = [r for r in self.active if lexical(r)[0] > 0]
            local_refs = heapq.nlargest(local_k, pool, key=lexical)
        return global_refs, local_refs

    def emit(self, operation, request, response):
        self.events.append({'operation': operation, 'request': request, 'response': response})
        save(self.folder / 'memory-events.json', self.events)
        return canonical(response) + b'\n'

    def briefs(self):
        glob, local = self.rank(); items = []; dropped = []
        for ref in dict.fromkeys(glob + local):
            card = self.cards[ref]
            brief = {'id': card['id'], 'ref': ref, 'type': card['type'], 'level': card['level'], 'brief': card['brief']}
            size = len(canonical(brief))
            if self.brief_bytes + size > self.config['brief_bytes_max']:
                dropped.append(card['id']); continue
            items.append(brief); self.disclosed.add(card['id']); self.brief_bytes += size
        return self.emit('briefs', None, {'items': items, 'mandatory_global': [self.cards[r]['id'] for r in glob],
                         'related': [self.cards[r]['id'] for r in local], 'budget_dropped': dropped,
                         'budget_unit': 'utf8_bytes'})

    def open(self, request):
        if type(request) is not dict or set(request) != {'op', 'id'} or request.get('op') != 'open_memory' or request.get('id') not in self.disclosed:
            return self.emit('open', request, {'error': 'not_disclosed'})
        card = self.cards[self.ids[request['id']]]
        supports = [{'id': self.archive.get(r)['id'], 'ref': r, 'brief': self.archive.get(r)['brief']} for r in card['support_refs']]
        response = {'id': card['id'], 'detail': card['detail'], 'evidence_refs': card['evidence_refs'], 'supports': supports}
        size = len(canonical(response))
        if self.detail_bytes + size > self.config['detail_bytes_max']:
            return self.emit('open', request, {'error': 'detail_budget'})
        self.detail_bytes += size; self.opened.add(card['id'])
        self.disclosed.update(c['id'] for c in supports)
        return self.emit('open', request, response)

    def ratings(self, final):
        # Only actual declared use; never convert self-report into independent success.
        for rating in final['ratings']:
            ref = self.ids[rating['id']]
            old = self.importance.get(ref, 0)
            a = self.config['importance_ema_alpha']
            self.importance[ref] = (1 - a) * old + a * rating['value']
        save(self.folder / 'importance-update.json', {'self_report_only': True, 'importance': self.importance,
             'used': final['memory_uses'], 'opened': sorted(self.opened), 'ratings': final['ratings']})
        return self.importance
