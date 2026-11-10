"""023 原生评审的传输结构；语义、引文和归属仍由原生校验器核验。"""


def obj(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def enum(*values):
    return {'type': 'string', 'enum': list(values)}


def array(items):
    return {'type': 'array', 'items': items}


def compact(schema):
    """把重复出现的相同子结构集中到$defs并原位引用；约束内容不变，只缩小请求体。

    分项变体里的事件枚举和公共子对象会重复数十次，事件越多请求体越大。"""
    import json
    counts = {}

    def children(node):
        yield from node.get('properties', {}).values()
        if isinstance(node.get('items'), dict): yield node['items']
        yield from node.get('anyOf', [])

    def count(node):
        key = json.dumps(node, sort_keys=True, ensure_ascii=False)
        counts[key] = counts.get(key, 0) + 1
        for child in children(node): count(child)

    count(schema)
    # 短结构引用后并不更小，保持内联。
    names = {key: 's' + str(index) for index, key in enumerate(sorted(k for k, n in counts.items() if n > 1 and len(k) > 120))}

    def rewrite(node, top=False):
        if not top:
            name = names.get(json.dumps(node, sort_keys=True, ensure_ascii=False))
            if name: return {'$ref': '#/$defs/' + name}
        result = dict(node)
        if 'properties' in node: result['properties'] = {k: rewrite(v) for k, v in node['properties'].items()}
        if isinstance(node.get('items'), dict): result['items'] = rewrite(node['items'])
        if 'anyOf' in node: result['anyOf'] = [rewrite(v) for v in node['anyOf']]
        return result

    result = rewrite(schema, top=True)
    if names: result['$defs'] = {name: rewrite(json.loads(key), top=True) for key, name in names.items()}
    return result


def schema(unit, rubric, version):
    text = {'type': 'string'}
    refs = array(text)
    required_refs = dict(refs, minItems=1)
    # 来源前缀由结构合同约束；本人动作字段不能选择AI/工具背景引文。
    person_refs = dict(type='array', items=dict(type='string', pattern='^person-[A-Za-z0-9_-]+$'), minItems=1)
    supplied_refs = dict(type='array', items=dict(type='string', pattern='^(person|supplied)-[A-Za-z0-9_-]+$'), minItems=1)
    # 原生核验要求这里引用实际事件键；person-/context-是证据键，二者不能混用。
    event_ids=[e['event'] for e in unit['events']]
    event_schema=enum(*event_ids) if event_ids else dict(type='string',maxLength=0)
    checked_events = dict(type='array',items=event_schema,minItems=1)
    missing = obj(dict(needed=text, why_changes_judgment=text, checked_events=checked_events))
    quality = []
    for label in ('sufficient', 'minor_gap', 'necessary_gap', 'major_gap', 'critical_breach', 'unresolved'):
        fields = dict(label=enum(label), reason=text, refs=required_refs)
        if label == 'unresolved':
            fields['missing'] = missing
        elif label != 'sufficient':
            fields['responsibility'] = obj({key: text for key in (
                'known_at_time', 'person_control', 'feasible_alternative', 'actual_gap')})
        quality.append(obj(fields))
    cells = []
    scope = {'person_week': 'scored_week_scope', 'person_week_fragment': 'scored_fragment_scope', 'person_month': 'scored_month_scope'}.get(
        unit.get('evaluation_scope'), 'local')
    # 只按传输所需的本人引用与M项附加字段分组，十二项判据仍逐项由原生合同校验。
    groups = [('P', [key for key in rubric['items'] if key.startswith('P')]),
              ('M', [key for key in rubric['items'] if key.startswith('M')]),
              ('JE', [key for key in rubric['items'] if key.startswith(('J', 'E'))])]
    for item, item_codes in groups:
        for state in ('observed', 'no_opportunity', 'related', 'unobservable', 'attribution_unclear', 'not_applicable'):
            fields = dict(item=enum(*item_codes), state=enum(state), reason=text,
                          opportunity=obj(dict(description=text, refs=refs)))
            if state == 'observed':
                fields['opportunity'] = obj(dict(description=text, refs=required_refs))
                fields.update(action=obj(dict(fact=text, ai_role=text, attribution_explanation=text,
                                              refs=supplied_refs if item.startswith('P') else person_refs)),
                              quality={'anyOf': quality},
                              performance=obj(dict(verdict=enum(*rubric['performance_values'], 'unresolved'),
                                                   scope=enum(scope), reason=text)), difference=dict(type='string',minLength=1))
            elif state == 'related':
                fields['primary_item'] = enum(*rubric['items'])
            elif state in ('unobservable', 'attribution_unclear'):
                fields['missing'] = missing
            if state in ('related', 'unobservable', 'attribution_unclear'):
                fields['opportunity'] = obj(dict(description=text, refs=required_refs))
            if item.startswith('M'):
                handling = [obj(dict(status=enum('not_demonstrated'), reason=text, refs=refs))]
                if state == 'observed':
                    handling.append(obj(dict(status=enum('confirmed'), reason=text, refs=person_refs, issue=text)))
                if state not in ('no_opportunity', 'related', 'not_applicable'):
                    handling.append(obj(dict(status=enum('unresolved'), reason=text, refs=refs)))
                fields['challenge_handling'] = {'anyOf': handling}
            if state == 'observed':
                # 质量与表现是同一个原生判断合同，不能让两个独立枚举生成矛盾组合。
                for variant in quality:
                    label = variant['properties']['label']['enum'][0]
                    allowed = {'sufficient': ('reasonable', 'good', 'outstanding', 'exceptional'),
                               'minor_gap': ('partial',)}.get(label, (label,))
                    cells.append(obj(dict(fields, quality=variant,
                        performance=obj(dict(verdict=enum(*allowed), scope=enum(scope), reason=text)))))
            else:
                cells.append(obj(fields))
    context = dict(effective_request=text, necessary_scope=text, observed_execution=text,
                   boundary_reason=text, boundary=enum('consistent', 'uncertain', 'needs_correction'), refs=required_refs)
    if scope == 'local':
        context['scope'] = enum('parent', 'bounded_local', 'uncertain')
        challenge_variants = []
        for level, sufficient in (('routine', 'yes'), ('substantial', 'no'), ('complex', 'no'), ('unknown', 'unknown')):
            challenge_variants.append(obj(dict(
                level=enum(level), reason=text, alternative=text,
                scope_basis=obj(dict(kind=enum('explicit_requirement', 'necessary_implication', 'execution_revealed', 'uncertain'), fact=text, refs=refs)),
                routine_test=obj(dict(sufficient=enum(sufficient), reason=text, refs=refs)),
                facets=array(obj(dict(aspect=enum(*rubric['challenge_facets']),
                                      status=enum('present', 'not_demonstrated', 'unknown'), fact=text, refs=refs))))))
        context['challenge'] = {'anyOf': challenge_variants}
    else:
        context['scope'] = enum(scope)
    from scoring_pipeline.weekly_performance.review_policy import REVIEW_POLICY
    own = [e['event'] for e in unit.get('events', []) if e['event'] in unit.get('person_events', [])]
    all_events = [e['event'] for e in unit.get('events', [])]
    evidence = []
    for prefix, origins, events in (
        ('person', ('direct_expression', 'explicit_adoption'), own),
        ('supplied', ('quoted_or_forwarded',), own),
        ('context', ('quoted_or_forwarded', 'uncertain', 'context'), all_events),
    ):
        if events:
            evidence.append(obj(dict(id=dict(type='string', pattern='^'+prefix+'-[A-Za-z0-9_-]+$'),
                event=enum(*events), quote=text, origin=enum(*origins), origin_reason=text)))
    readable = sorted({n['event_id'] for n in unit.get('reading_notes', [])} - set(all_events))
    needs = dict(type='array',items=enum(*readable),maxItems=24) if readable else dict(type='array',items=text,maxItems=0)
    result = obj(dict(version=enum(version), review_policy=enum(REVIEW_POLICY), key=enum(unit['key']),
                    evidence=array({'anyOf': evidence}) if evidence else dict(array(text),maxItems=0),
                    period_reviews=array(obj(dict(period_ids=refs, context=obj(context), cells=dict(array({'anyOf': cells}), minItems=12, maxItems=12)))),
                    needs_events=needs))
    if unit.get('context_access'):
        from navigation_access import query_schema
        result['properties']['needs_nodes']=query_schema(unit)
        result['required'].append('needs_nodes')
    return compact(result)
