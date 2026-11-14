"""Provider-boundary fixture only: all stages run their real business engines."""
import json
import re
from types import SimpleNamespace as NS

from backend.tests.test_p2a_api import _fake_completion
from backend.tests.test_p2b_engine import candidate
from backend.tests.test_p3_engine import model as p3_reply


class AutoReviewFakeSDK:
    calls = []
    fail_stage = None
    fail_at = None
    dimension_count = 0

    def __init__(self, **kwargs):
        assert kwargs['max_retries'] == 0
        self.chat = NS(completions=NS(create=self.create))

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        if cls.fail_at == len(cls.calls):
            if not kwargs.get('stream'):
                raise ConnectionError('private test error')
            def interrupted():
                yield NS(model='actual-model', choices=[], usage=None)
                raise ConnectionError('private test error')
            return interrupted()
        messages = kwargs['messages']
        system, user = messages[0]['content'], messages[1]['content']
        usage = NS(prompt_tokens=80, completion_tokens=20, total_tokens=100)
        if any(name in system for name in ('paper-type-planner/', 'paper-understanding/',
                'contribution-evidence-mapper/', 'contribution-normalizer/')):
            if 'paper-type-planner/' in system:
                cls.dimension_count = 0
            if 'contribution-normalizer/' not in system and not re.search(r'\[(ru_[^\]]+)\]',user):
                source = re.search(r'ru_ev_[A-Za-z0-9_]+',user)
                if not source:
                    raise AssertionError('Fake response needs an existing source')
                messages = [messages[0],{**messages[1],'content':user+'\n['+source.group()+']'}]
            chunks = _fake_completion(messages)
            chunks[-1].usage = usage
            return iter(chunks)
        data = json.loads(user.split('\nReturn ')[0])
        if 'source_excerpts' in data:
            result = {'source_conflicts':[]}
        elif 'allowed_source_ids' in data:
            item = candidate(data['allowed_source_ids'][0])
            item['claim_ids'] = [data['claim_map']['claims'][0]['id']]
            # Exactly one concern: do not rely on an empty downstream path.
            cls.dimension_count += 1
            if cls.dimension_count == 2:
                item['title'] = 'Experimental scope support'
                item['verification_questions'] = ['Is the experimental scope explicitly supported?']
            candidates = [item] if cls.dimension_count <= 2 else []
            result = {'inspection_focus': ['claim support'], 'inspected_claim_ids': item['claim_ids'],
                'inspected_source_refs': [data['allowed_source_ids'][0]], 'limitations': [],
                'candidates': candidates, 'no_issue_explanation': None if candidates else 'No specific concern.'}
        elif 'pairs' in data:
            result = {'relations':[{'left_issue_id':a,'right_issue_id':b,'relation':'distinct','rationale':'Different scope'} for a,b in data['pairs']]}
        elif 'original_facts' in data:
            result = json.loads(p3_reply('test:source_observation', system, user)['content'])
        elif 'task' in data:
            stage = 'technical' if 'metric_records' in system else ('counterevidence' if 'independent challenger' in system else 'adjudication')
            result = json.loads(p3_reply('test:'+stage, system, user)['content'])
        elif 'report' in data:
            result = {'acceptable': True, 'unsupported_facts': [], 'state_promotions': [],
                      'refuted_accusations': [], 'language_or_scope_changes': []}
        elif 'evidence_pack' in data and 'plan' in data:
            group = data['plan']['groups'][0]
            result = {'schema_version': '1.0', 'prompt_version': 'review-comment-writing-zh/v2',
                'language': 'zh', 'report_kind': 'internal',
                'general_assessment': '本文提出一种方法，以下内容为待人工复核的内部疑问。',
                'comments': [{'report_comment_id': group['group_id'],
                    'source_issue_ids': group['source_issue_ids'], 'source': 'p3',
                    'title_zh': '方法适用范围的澄清', 'title_en': 'Method scope clarification',
                    'comment_body': '建议作者澄清该方法主张的适用范围及相应实验依据。现有材料尚不足以确认是否存在问题，需要人工复核原文中的作者主张、实验范围以及可能的反证，不能据此认定作者存在错误。',
                    'scientific_question': group['scientific_question'], 'evidence_refs': [],
                    'author_action_requested': '请说明主张范围及相应依据。',
                    'comment_category': group['category'], 'uncertainty_qualification': '尚无定论；仅供内部复核。'}]}
        elif 'writing' in data or 'result' in data:
            result = {'acceptable': True, 'unsupported_facts': [], 'state_promotions': [],
                      'refuted_accusations': [], 'language_or_scope_changes': []}
        elif 'candidates' in data:
            result = {'groups': [{'issue_ids': [key], 'rationale': 'Independent question',
                                  'central_question': value['verification_questions'][0]}
                                 for key,value in data['candidates'].items()]}
        else:
            raise AssertionError('Unexpected SDK request keys: '+','.join(data))
        content = json.dumps(result, ensure_ascii=False)
        if cls.fail_stage and cls.fail_stage in system:
            def broken():
                yield NS(model='actual-model', choices=[], usage=None)
                raise ConnectionError('test failure containing private text')
            return broken()
        if kwargs.get('stream'):
            return iter([NS(model='actual-model', choices=[NS(delta=NS(content=content))], usage=None),
                         NS(model='actual-model', choices=[], usage=usage)])
        return NS(model='actual-model', choices=[NS(message=NS(content=content))], usage=usage)
