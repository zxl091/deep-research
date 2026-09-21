"""Run-local evidence reuse. Keep different sources and merge chapter ownership."""
import hashlib
import re
import unicodedata


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))


def fact_key(fact):
    return (fact.get('source_url', ''), normalized(fact.get('content')))


def merge_facts(facts):
    found = {}
    for fact in facts:
        key = fact_key(fact)
        if not all(key):
            continue
        if key in found:
            current = found[key]
            current['related_sections'] = list(dict.fromkeys(
                current.get('related_sections', []) + fact.get('related_sections', [])))
        else:
            found[key] = fact
    return list(found.values())


def existing_fact(state, content, url, section_id=None):
    key = (url, normalized(content))
    for fact in state.get('facts', []):
        if fact_key(fact) == key:
            if section_id and section_id not in fact.setdefault('related_sections', []):
                fact['related_sections'].append(section_id)
            return True
    return False


def source_key(row):
    # Compare exactly what the extractor can see, not merely the URL.
    content = row.get('summary') or row.get('snippet') or row.get('content') or ''
    if not row.get('url') or not content.strip():
        return None  # Empty fixtures/unknown bodies are not proof of a cache hit.
    return hashlib.sha256((row['url'] + '\n' + normalized(content)).encode()).hexdigest()


def unique_sources(rows):
    seen, result = set(), []
    for row in rows:
        key = source_key(row)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(row)
    return result


def new_sources(state, section_id, rows):
    # Different chapters may ask different questions of the same document.
    seen = set(state.get('extracted_sources', {}).get(section_id, []))
    return [r for r in unique_sources(rows) if source_key(r) not in seen or source_key(r) is None]


def mark_extracted(state, section_id, rows):
    cache = state.setdefault('extracted_sources', {})
    cache[section_id] = sorted(set(cache.get(section_id, [])) | {k for r in rows if (k := source_key(r))})


def normalize_graph(graph):
    """Correct unambiguous type-inverted predicates; disclose invalid edges."""
    nodes = {n.get('id'): n for n in graph.get('nodes', []) if n.get('id')}
    edges, seen, issues, corrected = [], set(), [], 0
    publishers = {'company', 'organization', 'team', 'institution'}
    products = {'product', 'model'}
    for raw in graph.get('edges', []):
        edge = dict(raw)
        a, b = nodes.get(edge.get('source')), nodes.get(edge.get('target'))
        if not a or not b:
            issues.append('关系端点未在实体列表中：' + str(edge))
            continue
        if edge.get('relation') in ('发布', '研发', '推出'):
            if a.get('type') in products and b.get('type') in publishers:
                edge['source'], edge['target'] = edge['target'], edge['source']
                edge['direction_corrected'] = True
                corrected += 1
        key = (edge.get('source'), edge.get('target'), edge.get('relation'))
        if key not in seen:
            edges.append(edge); seen.add(key)
    graph['edges'] = edges
    return {'corrected_directions': corrected, 'issues': issues,
            'note': '仅校验端点与明确的类型方向，不等于关系事实已核验。'}
